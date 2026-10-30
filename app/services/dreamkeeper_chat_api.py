"""주 프로그램 연결: 특허기술 전용 챗봇 대화 한 차례 처리 경로.

- ``DREAMKEEPER_CHAT_ENABLED=1``일 때만 경로를 등록한다(시작할 때 한 번 판단).
  꺼져 있으면 경로 자체가 없으므로 기존 동작에 영향이 없다. 등록 뒤 값이 꺼지면
  요청마다 다시 확인해 404를 돌려준다.
- 소유자 인증은 기존 ``require_owner``를 그대로 쓴다.
- 외부 모델(생성·검증)은 ``configure_chat_runtime``으로 주입한다. 주입이 없거나,
  데이터베이스 위치(``DREAMKEEPER_CHAT_DB``)가 없거나, langgraph가 설치되지 않았으면
  503 보류로 응답한다. 이 모듈은 비밀 열쇠를 읽지 않는다. 주입되는 모델 함수가
  필요한 열쇠를 환경변수에서만 읽어야 한다.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from app.services.dreamkeeper_chat_core import (
    CLASS_LABELS, DEFAULT_CONFIG_PATH, ChatHold, DreamkeeperChatCore, load_config,
)
from app.services.dreamkeeper_engine import DreamkeeperEngine, EngineHold
from app.services.owner_basic_auth import require_owner

ENABLE_ENV = "DREAMKEEPER_CHAT_ENABLED"
DB_ENV = "DREAMKEEPER_CHAT_DB"
CONFIG_ENV = "DREAMKEEPER_CHAT_CONFIG"
OWNER_ENV = "DREAMKEEPER_CHAT_OWNER_ID"
ROUTE = "/dreamkeeper/chat/turn"

logger = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)
_runtime_factory: Callable[[], Any] | None = None
_trial_runner: Callable[[dict], dict] | None = None
_cores: dict[tuple[str, str, str], DreamkeeperChatCore] = {}
_cores_lock = threading.Lock()


def chat_enabled() -> bool:
    return os.getenv(ENABLE_ENV, "") == "1"


def configure_chat_runtime(factory: Callable[[], Any] | None) -> None:
    """호스트가 실제 모델 묶음(ChatRuntime)을 만드는 함수를 주입한다. None은 해제."""
    global _runtime_factory
    _runtime_factory = factory


def configure_trial_runner(runner: Callable[[dict], dict] | None) -> None:
    """격리 후보 시험 함수 주입(후보 본문 dict -> {"passed": bool, ...}). None은 해제."""
    global _trial_runner
    _trial_runner = runner


def reset_chat_cores() -> None:
    with _cores_lock:
        for core in _cores.values():
            core.engine.close()
        _cores.clear()


def _require_enabled() -> None:
    if not chat_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


def _hold(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail, headers={"Cache-Control": "no-store"})


def _core() -> DreamkeeperChatCore:
    database = os.getenv(DB_ENV, "").strip()
    if not database:
        raise _hold(503, "dreamkeeper_chat_database_not_configured")
    owner = os.getenv(OWNER_ENV, "").strip() or "edurecover-owner"
    config_path = os.getenv(CONFIG_ENV, "").strip() or str(DEFAULT_CONFIG_PATH)
    key = (database, owner, config_path)
    with _cores_lock:
        if key not in _cores:
            try:
                config = load_config(config_path)
                _cores[key] = DreamkeeperChatCore(DreamkeeperEngine(database, owner), config)
            except (OSError, ValueError) as exc:
                logger.error("dreamkeeper_chat_setup_failed error_type=%s", type(exc).__name__)
                raise _hold(503, "dreamkeeper_chat_setup_failed") from None
        return _cores[key]


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    logical_id: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=100_000)
    domain: str = Field(min_length=1, max_length=100)
    purpose: str | None = Field(default=None, min_length=1, max_length=100)


@router.post(ROUTE, dependencies=[Depends(_require_enabled), Depends(require_owner)])
def dreamkeeper_chat_turn(body: ChatTurnRequest) -> JSONResponse:
    if _runtime_factory is None:
        raise _hold(503, "dreamkeeper_chat_models_not_configured")
    try:
        from app.services.dreamkeeper_chat_graph import run_chat_turn
    except ImportError:
        raise _hold(503, "dreamkeeper_chat_graph_dependency_missing") from None
    core = _core()
    runtime = _runtime_factory()
    if runtime is None:
        raise _hold(503, "dreamkeeper_chat_models_not_configured")
    try:
        result = run_chat_turn(core, runtime, logical_id=body.logical_id, task_id=body.task_id,
                               question=body.question, domain=body.domain, purpose=body.purpose)
    except (ChatHold, EngineHold) as exc:
        return JSONResponse({"status": "HOLD", "hold_reason": str(exc)}, status_code=409,
                            headers={"Cache-Control": "no-store"})
    output = result.get("output") or {}
    payload = {
        "status": result["status"], "turn_id": result.get("turn_id"),
        "hold_reason": result.get("hold_reason"), "engine": result.get("engine"),
        "output_id": output.get("output_id"), "text": output.get("text"),
        "receipt_sources": (output.get("receipt") or {}).get("sources"),
        "engine_failures": result.get("engine_failures", []),
    }
    return JSONResponse(payload, status_code=200 if result["status"] == "OUTPUT" else 409,
                        headers={"Cache-Control": "no-store"})


# ------------------------------------------------ 관리 경로(같은 조건·같은 인증)
_GUARDS = [Depends(_require_enabled), Depends(require_owner)]
_NO_STORE = {"Cache-Control": "no-store"}


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")
    logical_id: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=200)


class SourcePolicyRequest(_Base):
    source_id: str = Field(min_length=1, max_length=200)
    allowed_purposes: list[str] = Field(min_length=1, max_length=50)
    valid: StrictBool
    user_receipt: str = Field(min_length=1, max_length=500)


class PolicyAdvanceRequest(_Base):
    reason: str = Field(min_length=1, max_length=1000)
    user_receipt: str = Field(min_length=1, max_length=500)


class SourceCorrectRequest(_Base):
    source_id: str = Field(min_length=1, max_length=200)
    fields: dict[str, Any] = Field(min_length=1)
    expected_revision: StrictInt
    user_receipt: str = Field(min_length=1, max_length=500)


class OutputRecheckRequest(_Base):
    output_id: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=100)
    sources: dict[str, list[str]] = Field(min_length=1)
    user_receipt: str = Field(min_length=1, max_length=500)


class ConfirmRequest(_Base):
    user_confirmed: StrictBool
    user_receipt: str = Field(min_length=1, max_length=500)


class OutputReleaseRequest(ConfirmRequest):
    output_id: str = Field(min_length=1, max_length=100)


class IOSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_unit: str = Field(min_length=1, max_length=100)
    output_unit: str = Field(min_length=1, max_length=100)
    operation: str = Field(min_length=1, max_length=100)


class QuarantineEvaluateRequest(_Base):
    domain: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=100)
    io_spec: IOSpec


class QuarantineTrialRequest(_Base):
    quarantine_id: str = Field(min_length=1, max_length=100)


class QuarantineAdoptRequest(ConfirmRequest):
    quarantine_id: str = Field(min_length=1, max_length=100)


def _call(fn: Callable[[], Any]) -> JSONResponse:
    try:
        return JSONResponse(fn(), headers=_NO_STORE)
    except (ChatHold, EngineHold) as exc:
        return JSONResponse({"status": "HOLD", "hold_reason": str(exc)}, status_code=409,
                            headers=_NO_STORE)
    except ValueError as exc:
        return JSONResponse({"status": "INVALID", "detail": str(exc)}, status_code=422,
                            headers=_NO_STORE)


@router.post("/dreamkeeper/chat/source/policy", dependencies=_GUARDS)
def dreamkeeper_source_policy(body: SourcePolicyRequest) -> JSONResponse:
    core = _core()
    return _call(lambda: core.set_source_policy(
        body.logical_id, body.task_id, source_id=body.source_id,
        allowed_purposes=body.allowed_purposes, valid=body.valid, user_receipt=body.user_receipt))


@router.post("/dreamkeeper/chat/policy/advance", dependencies=_GUARDS)
def dreamkeeper_policy_advance(body: PolicyAdvanceRequest) -> JSONResponse:
    core = _core()
    return _call(lambda: {"policy_generation": core.advance_policy_generation(
        body.logical_id, body.task_id, reason=body.reason, user_receipt=body.user_receipt)})


@router.post("/dreamkeeper/chat/source/correct", dependencies=_GUARDS)
def dreamkeeper_source_correct(body: SourceCorrectRequest) -> JSONResponse:
    core = _core()

    def run():
        result = core.correct_source(body.logical_id, body.task_id, source_id=body.source_id,
                                     fields=body.fields, expected_revision=body.expected_revision,
                                     user_receipt=body.user_receipt)
        holds = core.load_ledger(body.logical_id, body.task_id)["holds"]
        return {"source_revision": result["source"]["revision"],
                "classification": result["classification"],
                "labels": CLASS_LABELS, "held_outputs": holds["outputs"]}
    return _call(run)


@router.post("/dreamkeeper/chat/output/recheck", dependencies=_GUARDS)
def dreamkeeper_output_recheck(body: OutputRecheckRequest) -> JSONResponse:
    core = _core()
    return _call(lambda: core.recheck_output(
        body.logical_id, body.task_id, body.output_id, purpose=body.purpose,
        sources=body.sources, user_receipt=body.user_receipt))


@router.post("/dreamkeeper/chat/output/release", dependencies=_GUARDS)
def dreamkeeper_output_release(body: OutputReleaseRequest) -> JSONResponse:
    core = _core()
    return _call(lambda: core.release_output(
        body.logical_id, body.task_id, body.output_id, user_confirmed=body.user_confirmed,
        user_receipt=body.user_receipt))


@router.post("/dreamkeeper/chat/quarantine/evaluate", dependencies=_GUARDS)
def dreamkeeper_quarantine_evaluate(body: QuarantineEvaluateRequest) -> JSONResponse:
    core = _core()
    return _call(lambda: {"permitted": core.evaluate_quarantine_candidates(
        body.logical_id, body.task_id, domain=body.domain, purpose=body.purpose,
        io_spec=body.io_spec.model_dump())})


@router.post("/dreamkeeper/chat/quarantine/trial", dependencies=_GUARDS)
def dreamkeeper_quarantine_trial(body: QuarantineTrialRequest) -> JSONResponse:
    if _trial_runner is None:
        raise _hold(503, "dreamkeeper_chat_trial_runner_not_configured")
    core, runner = _core(), _trial_runner
    return _call(lambda: core.run_trial(body.logical_id, body.task_id, body.quarantine_id, runner))


@router.post("/dreamkeeper/chat/quarantine/adopt", dependencies=_GUARDS)
def dreamkeeper_quarantine_adopt(body: QuarantineAdoptRequest) -> JSONResponse:
    core = _core()
    return _call(lambda: core.adopt_candidate(
        body.logical_id, body.task_id, body.quarantine_id, user_confirmed=body.user_confirmed,
        user_receipt=body.user_receipt))


def install_dreamkeeper_chat(app: FastAPI) -> bool:
    """켜져 있을 때만 경로를 붙인다. 붙였으면 True."""
    if not chat_enabled():
        return False
    app.include_router(router)
    return True
