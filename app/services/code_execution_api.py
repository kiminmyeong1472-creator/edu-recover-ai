"""Owner-only coding jobs. Submitted code only runs in an isolated VM."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

from app.services.owner_basic_auth import require_owner
from app.services.open_model_api import _PrivateValidationRoute, _workspace_response
from app.services.code_evaluations import get_case, public_cases
from app.services import code_execution_runtime as runtime

HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
router = APIRouter(prefix="/owner/code-workspace", dependencies=[Depends(require_owner)],
                   route_class=_PrivateValidationRoute, include_in_schema=False)
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="code-workspace")
_lock = threading.RLock()
_active: dict[str, threading.Event] = {}
_recovered: set[str] = set()
MAX_TOTAL_FILES_BYTES = 512 * 1024

class CodeFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: StrictStr = Field(min_length=1, max_length=240)
    content: StrictStr = Field(max_length=128 * 1024)

    @model_validator(mode="after")
    def safe_path(self):
        path = PurePosixPath(self.path)
        if (path.is_absolute() or ".." in path.parts or "\\" in self.path
                or self.path in {".", ""} or any(ord(c) < 32 for c in self.path)
                or any(part in {"", ".", ".."} for part in self.path.split("/"))
                or len(self.path.encode("utf-8")) > 240 or path.parts[0] == ".edurecover"
                or len(self.content.encode("utf-8")) > 128 * 1024):
            raise ValueError("Invalid project path")
        return self

class CodeRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model_key: StrictStr | None = Field(default=None, max_length=256)
    route: Literal["learning", "chutes"] = "learning"
    prompt: StrictStr = Field(default="", max_length=16000)
    files: list[CodeFile] = Field(default_factory=list, max_length=40)
    command: StrictStr | None = Field(default=None, max_length=8000)
    evaluation_case: StrictStr | None = Field(default=None, max_length=100)
    explicit_authorization: Literal[True]

    @field_validator("explicit_authorization", mode="before")
    @classmethod
    def require_true_boolean(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError("Explicit boolean authorization required")
        return value

    @model_validator(mode="after")
    def bounded(self):
        if type(self.explicit_authorization) is not bool:
            raise ValueError("Explicit boolean authorization required")
        if not self.model_key and not (self.command and self.command.strip()):
            raise ValueError("A model or command is required")
        if self.model_key and not self.prompt.strip() and not self.evaluation_case:
            raise ValueError("Model prompt is required")
        if len({file.path for file in self.files}) != len(self.files):
            raise ValueError("Duplicate file path")
        if sum(len(f.content.encode()) for f in self.files) > MAX_TOTAL_FILES_BYTES:
            raise ValueError("Project too large")
        get_case(self.evaluation_case)
        return self

def _db():
    root = Path(os.getenv("EDURECOVER_CLASSROOM_STATE_DIR", ".edurecover_classroom_state"))
    root.mkdir(parents=True, exist_ok=True)
    path = root / "code_workspace.sqlite3"
    if root.is_symlink() or path.is_symlink():
        raise RuntimeError("CODE_STORAGE_INVALID")
    db = sqlite3.connect(path, timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, created REAL NOT NULL, body TEXT NOT NULL)")
    key = str(path.resolve())
    with _lock:
        if key not in _recovered:
            for rid, body in db.execute("SELECT id,body FROM runs").fetchall():
                value = json.loads(body)
                if value["status"] in {"queued", "running", "cancelling"}:
                    value.update(status="interrupted", message="서버가 재시작되어 실행이 중단되었습니다. 다시 시험할 수 있습니다.")
                    db.execute("UPDATE runs SET body=? WHERE id=?", (json.dumps(value, ensure_ascii=False), rid))
            db.commit(); _recovered.add(key)
    return db

def _save(record):
    with _lock, _db() as db:
        db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?)", (record["id"], record["created_at"], json.dumps(record, ensure_ascii=False)))
        # Bounded retention; full code is saved once in the exported files.
        db.execute("DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY created DESC LIMIT 30)")

def _load(run_id):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise HTTPException(404, "실행 기록이 없습니다.")
    with _db() as db:
        row = db.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "실행 기록이 없습니다.")
    return json.loads(row[0])

class _CancelableSession:
    def __init__(self, session, event, record):
        self.session, self.event, self.record = session, event, record
    def execute(self, name, arguments):
        if self.event.is_set():
            raise RuntimeError("CODE_RUN_CANCELLED")
        result = self.session.execute(name, arguments)
        evidence_arguments = dict(arguments)
        if name == "file_write":
            content = evidence_arguments.pop("content", "").encode()
            evidence_arguments.update(content_bytes=len(content), content_sha256=hashlib.sha256(content).hexdigest())
        self.record["steps"].append({"tool": name, "arguments": evidence_arguments, "result": result})
        _save(self.record)
        return result

def _work(request: CodeRunRequest, record: dict, event: threading.Event):
    started = time.monotonic()
    session = None
    try:
        if event.is_set():
            raise RuntimeError("CODE_RUN_CANCELLED")
        record.update(status="running", message="격리된 실행 환경을 준비하고 있습니다.")
        _save(record)
        session = runtime.RailwayCodeSession()
        with session:
            record["runtime_tools"] = getattr(session, "tools", {})
            case = get_case(request.evaluation_case)
            files = {f["path"]: f for f in (case["files"] if case else [])}
            files.update({f.path: f.model_dump() for f in request.files})
            session.seed(list(files.values()))
            executor = _CancelableSession(session, event, record)
            if request.model_key:
                from app.services.code_model_agent import run_code_agent
                result = asyncio.run(run_code_agent(request.model_key,
                    request.prompt or case["prompt"], executor, route=request.route, cancel_check=event.is_set))
                record["answer"] = result.get("answer", "")
                if result.get("status") not in {"completed", "success"}:
                    record.update(status="failed", message="모델의 도구 작업이 완료되지 않았습니다.")
            if request.command and not event.is_set():
                result = executor.execute("run_command", {"argv": ["bash", "-lc", request.command], "cwd": ".", "timeout_seconds": 120})
                record.update(stdout=result.get("stdout", ""), stderr=result.get("stderr", ""), exit_code=result.get("exit_code", result.get("exitCode")))
                if record["exit_code"] != 0 or result.get("timed_out", result.get("timedOut")):
                    record.update(status="failed", message="명령 실행 결과를 확인하십시오.")
            if case and not event.is_set():
                session.execute("file_write", {"path": ".edurecover_check.py", "content": case["verification"]})
                result = executor.execute("run_command", {"argv": ["python3", ".edurecover_check.py"], "cwd": ".", "timeout_seconds": 30})
                code = result.get("exit_code", result.get("exitCode"))
                passed = code == 0 and not result.get("timed_out", result.get("timedOut"))
                record["evaluation"] = {"case_id": case["id"], "suite": "EduRecover 자체 시험", "version": "2026-10-17.1", "status": "passed" if passed else "failed", "exit_code": code, "stdout": result.get("stdout", ""), "stderr": result.get("stderr", ""), "official_benchmark_score": None}
                if not passed:
                    record.update(status="failed", message="자체 시험에서 통과하지 못한 항목이 있습니다.")
            try:
                record["files"] = [f for f in session.snapshot() if f["path"] != ".edurecover_check.py"]
            except Exception as export_error:
                # The command already ran; losing the file copy must not hide its result.
                record["file_export_state"] = "failed"
                record["file_export_error"] = getattr(export_error, "code", "CODE_EXECUTION_FAILED")
            record["file_export_truncated"] = getattr(session, "snapshot_truncated", False)
            if event.is_set():
                record.update(status="cancelled", message="사용자가 실행을 중단했습니다.")
            elif record["status"] == "running":
                record.update(status="completed", message="실행이 끝났습니다. 파일과 시험 결과를 확인하십시오.")
    except Exception as exc:
        code = getattr(exc, "code", "CODE_EXECUTION_FAILED")
        if event.is_set() or str(exc) == "CODE_RUN_CANCELLED":
            record.update(status="cancelled", message="사용자가 실행을 중단했습니다.")
        else:
            record["failed_step"] = getattr(session, "phase", "start") if session is not None else "start"
            record.update(status="failed", error_code=code,
                          message="실행을 완료하지 못했습니다. 실행 환경 설정과 기록을 확인하십시오.")
    finally:
        if session is not None:
            try:
                # __exit__ exports before destruction even when model calls fail.
                record["files"] = [f for f in session.snapshot() if f["path"] != ".edurecover_check.py"]
            except Exception:
                record["file_export_state"] = "failed"
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        record["cleanup_status"] = getattr(session, "cleanup_status", "not_created")
        if session is not None and getattr(session, "export_error", None):
            record["file_export_state"] = "failed"
            record["file_export_error"] = session.export_error
        record["command_count"] = sum(step["tool"] == "run_command" for step in record["steps"])
        record["execution_state"] = "executed" if record["command_count"] else "not_executed"
        record["checks_state"] = record.get("evaluation", {}).get("status", "not_run")
        record["system_applied"] = False
        _save(record)
        with _lock:
            _active.pop(record["id"], None)

@router.get("")
def coding_workspace():
    # Default: one-button runner. The full editor stays at /advanced.
    from app.services.code_execution_workspace import code_execution_simple_html
    return _workspace_response(code_execution_simple_html())

@router.get("/advanced")
def coding_workspace_advanced():
    from app.services.code_execution_workspace import code_execution_workspace_html
    return _workspace_response(code_execution_workspace_html())

@router.get("/status")
def status():
    return JSONResponse(runtime.runtime_status() | {"system_apply_available": False,
        "tools": ["file_read", "file_write", "file_list", "run_command"],
        "code_isolation": "separate_vm_no_app_secrets"}, headers=HEADERS)

@router.get("/evaluation-cases")
def evaluation_cases():
    return JSONResponse(public_cases(), headers=HEADERS)

@router.get("/runs")
def list_runs():
    with _db() as db:
        rows = db.execute("SELECT body FROM runs ORDER BY created DESC LIMIT 100").fetchall()
    return JSONResponse({"runs": [json.loads(row[0]) for row in rows]}, headers=HEADERS)

@router.post("/runs", status_code=202)
def start_run(request: CodeRunRequest):
    readiness = runtime.runtime_status()
    if not readiness["configured"]:
        raise HTTPException(503, {"code": "CODE_RUNTIME_NOT_CONFIGURED", "message": readiness["message"]}, headers=HEADERS)
    if request.model_key:
        from app.services.code_model_agent import configured_turn
        from app.services.control_plane import ProviderHeld, assert_provider_allowed
        from app.services.open_model_catalog import model_family
        from app.services.open_model_transport import OpenModelError
        try:
            assert_provider_allowed(model_family(request.model_key), "generate")
            configured_turn(request.model_key, route=request.route)
        except ProviderHeld:
            raise HTTPException(423, "선택한 모델의 사용 설정을 확인하십시오.") from None
        except (ValueError, OpenModelError) as exc:
            raise HTTPException(getattr(exc, "http_status", 400), {"code": getattr(exc, "code", "CODE_MODEL_INVALID"), "message": "모델의 정확한 연결 설정을 확인하십시오."}) from None
    with _lock:
        if _active:
            raise HTTPException(409, "현재 실행을 마치거나 중단한 뒤 다시 시작하십시오.")
        record = {"id": uuid.uuid4().hex, "created_at": time.time(), "status": "queued",
            "model_key": request.model_key, "route": request.route, "evaluation_case": request.evaluation_case,
            "prompt": request.prompt, "command": request.command,
            "steps": [], "files": [], "system_applied": False,
            "cost": None, "cost_state": "제공자 실제 비용 미확인",
            "message": "실행 대기 중입니다."}
        _save(record)
        event = threading.Event(); _active[record["id"]] = event
        _pool.submit(_work, request, record.copy(), event)
    return JSONResponse(record, status_code=202, headers=HEADERS)

@router.get("/runs/{run_id}")
def get_run(run_id: str):
    return JSONResponse(_load(run_id), headers=HEADERS)

@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    record = _load(run_id)
    with _lock:
        if run_id in _active:
            _active[run_id].set()
            record.update(status="cancelling", message="진행 중인 명령이 끝나면 실행 환경을 정리합니다.")
            _save(record)
    return JSONResponse(record, headers=HEADERS)
