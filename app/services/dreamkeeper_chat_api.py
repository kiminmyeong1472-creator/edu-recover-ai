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
from pydantic import BaseModel, ConfigDict, Field

from app.services.dreamkeeper_chat_core import (
    DEFAULT_CONFIG_PATH, ChatHold, DreamkeeperChatCore, load_config,
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
_cores: dict[tuple[str, str, str], DreamkeeperChatCore] = {}
_cores_lock = threading.Lock()


def chat_enabled() -> bool:
    return os.getenv(ENABLE_ENV, "") == "1"


def configure_chat_runtime(factory: Callable[[], Any] | None) -> None:
    """호스트가 실제 모델 묶음(ChatRuntime)을 만드는 함수를 주입한다. None은 해제."""
    global _runtime_factory
    _runtime_factory = factory


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


def install_dreamkeeper_chat(app: FastAPI) -> bool:
    """켜져 있을 때만 경로를 붙인다. 붙였으면 True."""
    if not chat_enabled():
        return False
    app.include_router(router)
    return True
