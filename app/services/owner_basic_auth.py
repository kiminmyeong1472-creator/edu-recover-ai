"""Owner authentication with a mobile-friendly form and short-lived sessions.

Existing HTTP Basic clients remain supported. Session cookies contain no
credentials and are invalidated on restart or any owner credential change.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic(auto_error=False)
owner_auth_router = APIRouter(include_in_schema=False)
_COOKIE = "__Host-edurecover_owner_session"
_TTL = 3600
_SESSION_KEY = secrets.token_bytes(32)
# The production service runs one Uvicorn worker. Restarting it ends sessions.
_revoked: dict[str, int] = {}


def _credentials() -> tuple[tuple[str, ...], str]:
    users = tuple(user for user in (
        os.getenv("EDURECOVER_OWNER_USER", ""),
        os.getenv("EDURECOVER_OWNER_USER ", ""),
    ) if user)
    return users, os.getenv("EDURECOVER_OWNER_PASSWORD", "")


def _matches(username: str, password: str, users: tuple[str, ...], expected: str) -> bool:
    # Bytes also support non-ASCII credentials without compare_digest errors.
    user_matches = [secrets.compare_digest(username.encode("utf-8"), user.encode("utf-8"))
                    for user in users]
    password_matches = secrets.compare_digest(password.encode("utf-8"), expected.encode("utf-8"))
    return bool(users and expected and any(user_matches) and password_matches)


def _sign(payload: str, users: tuple[str, ...], password: str) -> str:
    config = json.dumps([users, password], ensure_ascii=False).encode("utf-8")
    key = hmac.digest(_SESSION_KEY, config, "sha256")
    return hmac.new(key, payload.encode("ascii"), hashlib.sha256).hexdigest()


def _new_session(users: tuple[str, ...], password: str) -> str:
    payload = f"{int(time.time()) + _TTL}.{secrets.token_hex(24)}"
    return f"{payload}.{_sign(payload, users, password)}"


def _prune_revocations() -> None:
    now = int(time.time())
    for token_hash, expiry in list(_revoked.items()):
        if expiry <= now:
            del _revoked[token_hash]


def _valid_session(token: str | None, users: tuple[str, ...], password: str) -> bool:
    if not token or len(token) > 160 or not users or not password:
        return False
    try:
        expiry, nonce, signature = token.split(".")
        now = int(time.time())
        if not now < int(expiry) <= now + _TTL:
            return False
        if len(nonce) != 48 or len(signature) != 64:
            return False
        payload = f"{expiry}.{nonce}"
        valid = secrets.compare_digest(signature, _sign(payload, users, password))
        _prune_revocations()
        return valid and hashlib.sha256(token.encode("ascii")).hexdigest() not in _revoked
    except (ValueError, TypeError, UnicodeError):
        return False


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin", "")
    try:
        parsed = urlsplit(origin)
        return (
            parsed.scheme == "https"
            and parsed.netloc == request.url.netloc
            and not parsed.path and not parsed.query and not parsed.fragment
            and parsed.username is None and parsed.password is None
        )
    except ValueError:
        return False


def require_owner(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> None:
    users, password = _credentials()
    if not users or not password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Owner authentication is not configured.",
            headers={"Cache-Control": "no-store"},
        )
    if _valid_session(request.cookies.get(_COOKIE), users, password):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not _same_origin(request):
            raise HTTPException(status_code=403, detail="Same-origin request required.")
        return
    if credentials is not None and _matches(
        credentials.username, credentials.password, users, password
    ):
        return
    # A normal page navigation now opens an actual form, not a native auth popup.
    if request.url.path == "/owner" and request.method in {"GET", "HEAD"}:
        raise HTTPException(
            status_code=303, detail="Owner login required.",
            headers={"Location": "/owner/login", "Cache-Control": "no-store"},
        )
    detail = "Owner authentication required." if credentials is None else "Invalid owner credentials."
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
        headers={"WWW-Authenticate": "Basic", "Cache-Control": "no-store"},
    )


_LOGIN_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>에듀리커버 관리자 로그인</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f2f5fa;color:#172b45;
font-family:system-ui,-apple-system,"Noto Sans KR",sans-serif;line-height:1.6}
main{width:min(440px,calc(100% - 32px));margin:8vh auto;background:white;
padding:30px 24px;border-radius:20px;box-shadow:0 10px 35px #172b4514}
.brand{color:#185bc4;font-size:17px;font-weight:750}h1{font-size:26px;margin:12px 0}
p{color:#4b5e74}label{display:block;margin-top:20px;font-weight:650}
input{display:block;width:100%;font-size:18px;padding:13px;margin-top:7px;
border:1px solid #8495ad;border-radius:9px;color:#172b45;background:white}
input:focus{outline:3px solid #bad2ff;border-color:#185bc4}
button{margin-top:25px;width:100%;padding:14px;background:#185bc4;color:white;
border:0;border-radius:9px;font-size:18px;font-weight:700;cursor:pointer}
.error{background:#fff0f0;border:1px solid #f3a7a7;color:#8b1515;padding:12px;border-radius:8px}
small{display:block;color:#53677e;margin-top:18px}
</style></head><body><main><div class="brand">EduRecover</div>
<h1>관리자 로그인</h1><p>등록하신 관리자 아이디와 비밀번호를 입력해 주세요.</p>
__ERROR__
<form method="post" action="/owner/login">
<label for="username">관리자 아이디</label>
<input id="username" name="username" type="text" autocomplete="username"
autocapitalize="none" spellcheck="false" maxlength="256" required>
<label for="password">비밀번호</label>
<input id="password" name="password" type="password" autocomplete="current-password"
maxlength="1024" required>
<button type="submit">로그인</button></form>
<small>로그인은 1시간 동안 유지됩니다. 서버가 재시작되면 다시 로그인해 주세요.</small>
</main></body></html>"""

OWNER_LOGOUT_BAR = """<div style="display:flex;justify-content:flex-end;padding:8px 16px">
<form method="post" action="/owner/logout">
<button type="submit" style="padding:8px 14px;border:1px solid #8899aa;border-radius:8px;
background:#fff;color:#172b45;cursor:pointer">관리자 로그아웃</button>
</form></div>"""


def _login_page(error: str = "", code: int = 200) -> HTMLResponse:
    # Error messages are fixed server text; no submitted values are echoed.
    from html import escape
    message = f'<p class="error" role="alert">{escape(error)}</p>' if error else ""
    return HTMLResponse(
        _LOGIN_HTML.replace("__ERROR__", message), status_code=code,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "same-origin",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                "base-uri 'none'; frame-ancestors 'none'"
            ),
        },
    )


@owner_auth_router.get("/owner/login", response_class=HTMLResponse)
async def owner_login_page() -> HTMLResponse:
    return _login_page()


@owner_auth_router.post("/owner/login")
async def owner_login(request: Request):
    if not _same_origin(request):
        return _login_page("로그인 화면을 새로 열고 다시 시도해 주세요.", 403)
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/x-www-form-urlencoded":
        return _login_page("로그인 입력 형식을 확인해 주세요.", 400)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            return _login_page("입력 내용이 너무 깁니다.", 413)
    try:
        fields = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=2)
        if set(fields) != {"username", "password"} or any(len(v) != 1 for v in fields.values()):
            raise ValueError
        username, supplied_password = fields["username"][0], fields["password"][0]
        if not username or not supplied_password or len(username) > 256 or len(supplied_password) > 1024:
            raise ValueError
    except (ValueError, UnicodeError):
        return _login_page("아이디와 비밀번호를 입력해 주세요.", 400)
    users, password = _credentials()
    if not users or not password:
        return _login_page("관리자 로그인 설정이 완료되지 않았습니다.", 503)
    if not _matches(username, supplied_password, users, password):
        return _login_page("아이디 또는 비밀번호가 일치하지 않습니다.", 401)
    response = RedirectResponse("/owner", status_code=303, headers={"Cache-Control": "no-store"})
    response.set_cookie(
        _COOKIE, _new_session(users, password), max_age=_TTL,
        httponly=True, secure=True, samesite="strict", path="/",
    )
    return response


@owner_auth_router.post("/owner/logout")
async def owner_logout(request: Request):
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="Same-origin request required.")
    users, password = _credentials()
    token = request.cookies.get(_COOKIE)
    if _valid_session(token, users, password):
        _prune_revocations()
        if len(_revoked) >= 1024:
            # Bound memory while invalidating all sessions if the limit is hit.
            global _SESSION_KEY
            _SESSION_KEY = secrets.token_bytes(32)
            _revoked.clear()
        else:
            _revoked[hashlib.sha256(token.encode("ascii")).hexdigest()] = int(token.split(".")[0])
    response = RedirectResponse("/owner/login", status_code=303, headers={"Cache-Control": "no-store"})
    response.delete_cookie(_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return response
