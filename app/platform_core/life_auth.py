from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from html import escape

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from app.services.owner_basic_auth import require_owner
from app.services.secret_vault import state_root


router = APIRouter(prefix="/life/auth", tags=["life-auth"])
owner_router = APIRouter(prefix="/owner/life", tags=["owner"])
_COOKIE = "__Host-liferecover_session"
_SESSION_TTL = 60 * 60 * 24 * 7
logger = logging.getLogger(__name__)

_FORM_STYLE = """
<style>
*{box-sizing:border-box}body{margin:0;background:#f5f7fa;color:#172b45;
font-family:system-ui,-apple-system,"Noto Sans KR",sans-serif}
main{width:min(440px,calc(100% - 32px));margin:7vh auto;background:#fff;padding:28px 24px;
border-radius:20px;box-shadow:0 10px 35px #172b4514}
h1{margin:8px 0 6px}.brand{color:#1457b8;font-weight:800}p{color:#52657a}
label{display:block;margin-top:18px;font-weight:700}input{width:100%;padding:13px;margin-top:6px;
font-size:17px;border:1px solid #8b9bb0;border-radius:9px}button{width:100%;margin-top:24px;padding:14px;
border:0;border-radius:9px;background:#1457b8;color:white;font-weight:800;font-size:17px}
.error{background:#fff0f0;border:1px solid #efaaaa;color:#8b1515;padding:11px;border-radius:8px}
a{color:#1457b8}
</style>
"""


def _form_page(title: str, action: str, button: str, *, error: str = "",
               status_code: int = 200, password_min_length: int = 1) -> HTMLResponse:
    error_html = f'<p class="error" role="alert">{escape(error)}</p>' if error else ""
    help_html = (
        '<p>생활앱 전용 계정으로 로그인하십시오. 관리자 계정과는 별도입니다.</p>'
        '<p><a href="/owner/life/account-setup">관리자가 생활앱 계정 만들기</a></p>'
        if action == "/life/auth/login-form" else ""
    )
    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>{_FORM_STYLE}</head>
<body><main><div class="brand">생활의 회복 · Life Recover</div><h1>{title}</h1>{error_html}
<form method="post" action="{action}">
<label for="username">아이디</label><input id="username" name="username" autocomplete="username" autocapitalize="none" spellcheck="false" minlength="3" maxlength="80" required>
<label for="password">비밀번호</label><input id="password" name="password" type="password" autocomplete="{'new-password' if password_min_length == 10 else 'current-password'}" minlength="{password_min_length}" maxlength="256" required>
<button type="submit">{button}</button></form>{help_html}</main></body></html>"""
    return HTMLResponse(html, status_code=status_code, headers={"Cache-Control":"no-store","Referrer-Policy":"same-origin",
        "X-Content-Type-Options":"nosniff","Content-Security-Policy":"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})


async def _read_form(request: Request, *, password_min_length: int = 1) -> tuple[str, str]:
    if request.headers.get("content-type", "").split(";",1)[0].strip() != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=400, detail="form_required")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise HTTPException(status_code=413, detail="form_too_large")
    try:
        fields = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=2)
        if set(fields) != {"username","password"} or any(len(v) != 1 for v in fields.values()):
            raise ValueError
        username = fields["username"][0].strip()
        password = fields["password"][0]
        if not (3 <= len(username) <= 80 and password_min_length <= len(password) <= 256):
            raise ValueError
        return username, password
    except (ValueError, UnicodeError, KeyError, IndexError):
        raise HTTPException(status_code=400, detail="invalid_form") from None


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
        n=2**14,
        r=8,
        p=1,
        dklen=32,
        maxmem=64 * 1024 * 1024,
    ).hex()


@contextmanager
def _existing_accounts():
    """Login and diagnostics must never create an empty replacement database."""
    path = state_root() / "liferecover" / "accounts.sqlite3"
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _authenticate(username: str, password: str) -> str:
    try:
        with _existing_accounts() as conn:
            row = conn.execute(
                "SELECT account_id,password_hash,salt FROM account WHERE username = ?",
                (username.strip(),),
            ).fetchone()
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="life_account_database_unavailable") from None
    if not row:
        raise HTTPException(status_code=401, detail="invalid_credentials")
    try:
        actual = _hash_password(password, bytes.fromhex(row["salt"]))
        matches = secrets.compare_digest(actual, row["password_hash"])
    except (ValueError, TypeError):
        raise HTTPException(status_code=503, detail="life_account_record_invalid") from None
    if not matches:
        raise HTTPException(status_code=401, detail="invalid_credentials")
    return row["account_id"]


def login_readiness() -> dict:
    """Read-only operator diagnostics; never returns names, hashes or secrets."""
    blockers = []
    try:
        _session_secret()
        signing_ready = True
    except HTTPException:
        signing_ready = False
        blockers.append("LIFE_SESSION_SECRET_NOT_CONFIGURED")
    accounts = None
    database_status = "unavailable"
    try:
        with _existing_accounts() as conn:
            accounts = conn.execute("SELECT COUNT(*) FROM account").fetchone()[0]
        database_status = "readable"
        if accounts == 0:
            blockers.append("LIFE_ACCOUNT_SETUP_REQUIRED")
    except (sqlite3.Error, OSError):
        blockers.append("LIFE_ACCOUNT_DATABASE_UNAVAILABLE")
    return {"read_only": True, "ready": not blockers, "blockers": blockers,
            "session_signing_ready": signing_ready, "database_status": database_status,
            "account_count": accounts, "password_check": "not_performed",
            "owner_and_life_accounts_are_separate": True}


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
        with _existing_accounts() as conn:
            row = conn.execute(
                "SELECT account_id FROM account WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return account_id if row else None
    except (ValueError, TypeError, UnicodeError):
        return None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="life_account_database_unavailable") from None


def require_life_user(request: Request) -> str:
    account_id = _parse_session(request.cookies.get(_COOKIE))
    if not account_id:
        raise HTTPException(status_code=401, detail="life_auth_required")
    return account_id


@router.post("/register")
def register(payload: RegisterRequest, response: Response) -> dict[str, str]:
    invite = os.getenv("LIFERECOVER_REGISTRATION_INVITE", "")
    if not invite or not secrets.compare_digest(payload.invite_code.encode("utf-8"), invite.encode("utf-8")):
        raise HTTPException(status_code=403, detail="registration_invite_required")

    # Fail before inserting an account if a session cannot be issued.
    _session_secret()

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
    account_id = _authenticate(payload.username, payload.password)
    response.set_cookie(
        _COOKIE,
        _issue_session(account_id),
        max_age=_SESSION_TTL,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"account_id": account_id, "status": "logged_in"}


@router.get("/login", response_class=HTMLResponse)
def login_page() -> HTMLResponse:
    return _form_page("로그인", "/life/auth/login-form", "로그인")


@router.post("/login-form")
async def login_form(request: Request):
    try:
        username, password = await _read_form(request)
        account_id = _authenticate(username, password)
        response = RedirectResponse("/life", status_code=303, headers={"Cache-Control":"no-store"})
        response.set_cookie(_COOKIE,_issue_session(account_id),max_age=_SESSION_TTL,
            httponly=True,secure=True,samesite="strict",path="/")
        return response
    except HTTPException as exc:
        logger.warning("life_login_failed reason=%s status=%s", exc.detail, exc.status_code)
        if exc.status_code == 401:
            error = "아이디 또는 비밀번호가 일치하지 않습니다. 생활앱 전용 계정을 확인하십시오."
        elif exc.status_code == 503:
            error = "로그인 서버 설정 또는 계정 저장소를 확인해야 합니다. 관리자에게 문의하십시오."
        else:
            error = "아이디와 비밀번호의 입력 형식을 확인하십시오."
        return _form_page("로그인", "/life/auth/login-form", "로그인",
                          error=error, status_code=exc.status_code)


@owner_router.get("/auth-status")
def owner_auth_status(response: Response, _: None = Depends(require_owner)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return login_readiness()


@owner_router.get("/account-setup", response_class=HTMLResponse)
def owner_account_setup(_: None = Depends(require_owner)) -> HTMLResponse:
    return _form_page("Life Recover 계정 만들기", "/owner/life/account-setup", "계정 생성", password_min_length=10)


@owner_router.post("/account-setup")
async def owner_account_setup_submit(request: Request, _: None = Depends(require_owner)):
    try:
        username, password = await _read_form(request, password_min_length=10)
        salt = os.urandom(16)
        account_id = f"usr_{uuid.uuid4().hex}"
        with _db() as conn:
            conn.execute(
                "INSERT INTO account(account_id, username, password_hash, salt, created_at) VALUES(?,?,?,?,?)",
                (account_id,username,_hash_password(password,salt),salt.hex(),int(time.time())),
            )
            conn.commit()
        return HTMLResponse(
            f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{_FORM_STYLE}</head>
<body><main><div class="brand">생활의 회복 · Life Recover</div><h1>계정 생성 완료</h1>
<p>아이디 <b>{escape(username)}</b> 계정이 생성됐습니다.</p><p><a href="/life/auth/login">Life Recover 로그인 화면 열기</a></p></main></body></html>""",
            headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer","X-Content-Type-Options":"nosniff",
                     "Content-Security-Policy":"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"}
        )
    except sqlite3.IntegrityError:
        return _form_page("Life Recover 계정 만들기", "/owner/life/account-setup", "계정 생성", error="이미 사용 중인 아이디입니다.", status_code=409, password_min_length=10)
    except HTTPException as exc:
        return _form_page("Life Recover 계정 만들기", "/owner/life/account-setup", "계정 생성", error="입력 내용을 확인하십시오.", status_code=exc.status_code, password_min_length=10)


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return {"status": "logged_out"}
