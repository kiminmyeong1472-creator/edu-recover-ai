from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.services.secret_vault import state_root


router = APIRouter(prefix="/life/auth", tags=["life-auth"])
_COOKIE = "__Host-liferecover_session"
_SESSION_TTL = 60 * 60 * 24 * 7


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=10, max_length=256)
    invite_code: str = Field(min_length=1, max_length=256)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=1, max_length=256)


def _db_path() -> Path:
    root = state_root() / "liferecover"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    return root / "accounts.sqlite3"


@contextmanager
def _db():
    path = _db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS account (
            account_id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
        """)
        conn.commit()
        yield conn
    finally:
        conn.close()


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**15,
        r=8,
        p=1,
        dklen=32,
    ).hex()


def _session_secret() -> bytes:
    raw = os.getenv("LIFERECOVER_SESSION_SECRET", "").encode("utf-8")
    if len(raw) < 32:
        raise HTTPException(status_code=503, detail="life_session_secret_not_configured")
    return hashlib.sha256(raw).digest()


def _sign(payload: str) -> str:
    return hmac.new(_session_secret(), payload.encode("ascii"), hashlib.sha256).hexdigest()


def _issue_session(account_id: str) -> str:
    expiry = int(time.time()) + _SESSION_TTL
    nonce = secrets.token_hex(16)
    payload = f"{account_id}.{expiry}.{nonce}"
    return f"{payload}.{_sign(payload)}"


def _parse_session(token: str | None) -> str | None:
    if not token or len(token) > 256:
        return None
    try:
        account_id, expiry, nonce, signature = token.split(".")
        if int(expiry) <= int(time.time()) or len(nonce) != 32:
            return None
        payload = f"{account_id}.{expiry}.{nonce}"
        if not secrets.compare_digest(signature, _sign(payload)):
            return None
        with _db() as conn:
            row = conn.execute(
                "SELECT account_id FROM account WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return account_id if row else None
    except (ValueError, TypeError):
        return None


def require_life_user(request: Request) -> str:
    account_id = _parse_session(request.cookies.get(_COOKIE))
    if not account_id:
        raise HTTPException(status_code=401, detail="life_auth_required")
    return account_id


@router.post("/register")
def register(payload: RegisterRequest, response: Response) -> dict[str, str]:
    invite = os.getenv("LIFERECOVER_REGISTRATION_INVITE", "")
    if not invite or not secrets.compare_digest(payload.invite_code, invite):
        raise HTTPException(status_code=403, detail="registration_invite_required")

    salt = os.urandom(16)
    account_id = f"usr_{uuid.uuid4().hex}"
    try:
        with _db() as conn:
            conn.execute(
                "INSERT INTO account(account_id, username, password_hash, salt, created_at) VALUES(?,?,?,?,?)",
                (
                    account_id,
                    payload.username.strip(),
                    _hash_password(payload.password, salt),
                    salt.hex(),
                    int(time.time()),
                ),
            )
            conn.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="username_already_exists") from exc

    response.set_cookie(
        _COOKIE,
        _issue_session(account_id),
        max_age=_SESSION_TTL,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    return {"account_id": account_id, "status": "registered"}


@router.post("/login")
def login(payload: LoginRequest, response: Response) -> dict[str, str]:
    with _db() as conn:
        row = conn.execute(
            "SELECT account_id,password_hash,salt FROM account WHERE username = ?",
            (payload.username.strip(),),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="invalid_credentials")
    actual = _hash_password(payload.password, bytes.fromhex(row["salt"]))
    if not secrets.compare_digest(actual, row["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid_credentials")
    response.set_cookie(
        _COOKIE,
        _issue_session(row["account_id"]),
        max_age=_SESSION_TTL,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    return {"account_id": row["account_id"], "status": "logged_in"}


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return {"status": "logged_out"}
