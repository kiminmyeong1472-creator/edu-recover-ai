"""Short-lived Redis context for oral-interview turns."""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import redis.asyncio as redis
from redis.exceptions import LockError

from app.core.config import settings


_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        if settings.redis_url is None:
            raise RuntimeError("REDIS_URL is not configured")
        _client = redis.from_url(
            settings.redis_url.get_secret_value(),
            decode_responses=True,
            health_check_interval=30,
            socket_connect_timeout=3,
            socket_timeout=5,
        )
    return _client


def _scope(session_id: str, student_alias: str, question_id: str) -> str:
    raw = f"{session_id}\0{student_alias}\0{question_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def context_key(session_id: str, student_alias: str, question_id: str) -> str:
    return f"oralctx:{_scope(session_id, student_alias, question_id)}"


async def get_context(session_id: str, student_alias: str, question_id: str) -> list[dict[str, str]]:
    rows = await _redis().lrange(context_key(session_id, student_alias, question_id), 0, -1)
    result: list[dict[str, str]] = []
    for row in rows:
        try:
            item = json.loads(row)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str):
            result.append({"role": item["role"], "content": item["content"]})
    return result


async def append_turn(
    session_id: str,
    student_alias: str,
    question_id: str,
    user_message: dict[str, str],
    assistant_message: dict[str, str],
) -> None:
    key = context_key(session_id, student_alias, question_id)
    client = _redis()
    pipe = client.pipeline(transaction=True)
    pipe.rpush(
        key,
        json.dumps(user_message, ensure_ascii=False),
        json.dumps(assistant_message, ensure_ascii=False),
    )
    pipe.ltrim(key, -settings.interview_context_max_messages, -1)
    pipe.expire(key, settings.interview_context_ttl_seconds)
    await pipe.execute()


@asynccontextmanager
async def context_lock(
    session_id: str, student_alias: str, question_id: str
) -> AsyncIterator[None]:
    key = f"orallock:{_scope(session_id, student_alias, question_id)}"
    lock = _redis().lock(
        key,
        timeout=settings.interview_lock_seconds,
        blocking_timeout=settings.interview_lock_wait_seconds,
    )
    acquired = await lock.acquire()
    if not acquired:
        raise TimeoutError("interview context is busy")
    try:
        yield
    finally:
        try:
            await lock.release()
        except LockError:
            pass
