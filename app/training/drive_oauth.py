"""Owner-initiated My Drive consent using drive.file, never a ChatGPT credential.

OAuth credentials are kept encrypted outside the registered content backup set.
Only app-created backup folders are used; no sharing permissions are modified.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.services.owner_basic_auth import require_owner
from app.services.secret_vault import state_root
from app.services.encrypted_backup import backup_encryption_status, encrypt_file, decrypt_file

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
COOKIE = "__Secure-edurecover_drive_state"
_pending: dict[str, tuple[float, str, str]] = {}
drive_router = APIRouter(prefix="/owner/drive", dependencies=[Depends(require_owner)], tags=["owner"])


def redirect_uri():
    origin = os.getenv("EDURECOVER_PUBLIC_ORIGIN", "")
    if not origin and os.getenv("RAILWAY_PUBLIC_DOMAIN"):
        origin = "https://" + os.environ["RAILWAY_PUBLIC_DOMAIN"]
    from urllib.parse import urlsplit
    parts = urlsplit(origin)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment or parts.path not in {"", "/"}:
        return ""
    return origin.rstrip("/") + "/owner/drive/callback"


def credential_path():
    return state_root() / "oauth_private" / "drive_oauth.edurbk"


def oauth_configured():
    p = credential_path()
    return p.is_file() and not p.is_symlink()


def client_configured():
    return bool(os.getenv("EDURECOVER_DRIVE_CLIENT_ID") and os.getenv("EDURECOVER_DRIVE_CLIENT_SECRET") and redirect_uri())


def _flow(**kwargs):
    from google_auth_oauthlib.flow import Flow
    return Flow.from_client_config({"web": {
        "client_id": os.environ["EDURECOVER_DRIVE_CLIENT_ID"],
        "client_secret": os.environ["EDURECOVER_DRIVE_CLIENT_SECRET"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [redirect_uri()],
    }}, scopes=SCOPES, redirect_uri=redirect_uri(), **kwargs)


def _write_credentials(value: dict):
    from app.training.backup_adapters import private_directory, fsync_directory
    root = credential_path().parent
    private_directory(root)
    if credential_path().is_symlink():
        raise ValueError("OAUTH_PATH_INVALID")
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        plain = Path(tmp) / "credential.json"
        plain.write_text(json.dumps(value), encoding="utf-8"); plain.chmod(0o600)
        encrypted = Path(tmp) / "credential.edurbk"
        encrypt_file(plain, encrypted)
        check = Path(tmp) / "restore.json"
        decrypt_file(encrypted, check)
        if json.loads(check.read_text()) != value:
            raise ValueError("OAUTH_RESTORE_CHECK_FAILED")
        with encrypted.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(encrypted, credential_path())
        fsync_directory(root)


def read_credentials():
    from app.training.backup_adapters import private_directory
    p = credential_path()
    private_directory(p.parent)
    if not oauth_configured():
        raise ValueError("DRIVE_OAUTH_NOT_CONNECTED")
    with tempfile.TemporaryDirectory(dir=p.parent) as tmp:
        plain = Path(tmp) / "credential.json"
        decrypt_file(p, plain)
        value = json.loads(plain.read_text())
    if (value.get("type") != "authorized_user" or value.get("scopes") != SCOPES
            or value.get("client_id") != os.getenv("EDURECOVER_DRIVE_CLIENT_ID")
            or not value.get("refresh_token")):
        raise ValueError("OAUTH_CREDENTIAL_INVALID")
    return value


def authorized_session():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import AuthorizedSession
    value = read_credentials()
    credentials = Credentials(token=None, refresh_token=value["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token", client_id=value["client_id"],
        client_secret=os.environ["EDURECOVER_DRIVE_CLIENT_SECRET"], scopes=SCOPES)
    return AuthorizedSession(credentials), value["folders"]


@drive_router.get("/status")
def drive_status():
    return JSONResponse({"client_configured": client_configured(), "connected_credential_present": oauth_configured(),
        "cloud_access_verified": False, "scope": "drive.file", "redirect_uri": redirect_uri(),
        "note": "연결과 실제 백업 성공은 별개입니다. 백업 결과를 확인해 주세요."}, headers={"Cache-Control": "no-store"})


@drive_router.post("/connect")
def connect(request: Request):
    if not client_configured():
        raise HTTPException(503, "서버의 Google OAuth 클라이언트 설정이 필요합니다.")
    if not backup_encryption_status()["ready"]:
        raise HTTPException(503, "백업 암호화 설정을 먼저 확인해 주세요.")
    if os.getenv("EDURECOVER_BACKUP_DRIVE_SERVICE_ACCOUNT_FILE") or os.getenv("EDURECOVER_BACKUP_DRIVE_SERVICE_ACCOUNT_JSON"):
        raise HTTPException(409, "기존 서비스 계정 백업 설정을 먼저 확인해 주세요.")
    for key, item in list(_pending.items()):
        if item[0] < time.time(): del _pending[key]
    if len(_pending) >= 10:
        raise HTTPException(429, "진행 중인 Google 연결을 완료한 뒤 다시 시도해 주세요.")
    state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    flow = _flow(autogenerate_code_verifier=True)
    url, _ = flow.authorization_url(access_type="offline", prompt="consent", state=state)
    _pending[state] = (time.time() + 600, browser, flow.code_verifier)
    response = JSONResponse({"authorization_url": url}, headers={"Cache-Control": "no-store"})
    response.set_cookie(COOKIE, browser, secure=True, httponly=True, samesite="lax", max_age=600, path="/owner/drive")
    return response


def _complete(code: str, state: str, verifier: str):
    from google.auth.transport.requests import AuthorizedSession
    from app.training.trade_secret_engine import BackupTarget
    flow = _flow(state=state, code_verifier=verifier)
    flow.fetch_token(code=code, timeout=30)
    credentials = flow.credentials
    if not credentials.refresh_token or not credentials.has_scopes(SCOPES):
        raise ValueError("OFFLINE_DRIVE_CONSENT_REQUIRED")
    folders = {}
    # A reconnection retains the existing folder mapping, avoiding duplicate roots.
    if oauth_configured():
        old = read_credentials()
        folders = old.get("folders", {})
    with AuthorizedSession(credentials) as session:
        if folders:
            for folder in set(folders.values()):
                r = session.get(f"https://www.googleapis.com/drive/v3/files/{folder}",
                    params={"fields": "id,ownedByMe,trashed"}, timeout=30)
                if r.status_code != 200 or not r.json().get("ownedByMe") or r.json().get("trashed"):
                    raise ValueError("RECONNECT_ACCOUNT_OR_FOLDER_MISMATCH")
        else:
            r = session.post("https://www.googleapis.com/drive/v3/files", json={
                "name": "EduRecover_암호화_자동백업", "mimeType": "application/vnd.google-apps.folder"},
                params={"fields": "id"}, timeout=30)
            if r.status_code != 200:
                raise ValueError("DRIVE_FOLDER_CREATE_FAILED")
            root = r.json()["id"]
            for target in BackupTarget:
                if not target.value.startswith("DRIVE_"): continue
                r = session.post("https://www.googleapis.com/drive/v3/files", json={"name": target.value,
                    "mimeType": "application/vnd.google-apps.folder", "parents": [root]}, params={"fields": "id"}, timeout=30)
                if r.status_code != 200:
                    raise ValueError("DRIVE_FOLDER_CREATE_FAILED")
                folders[target.value] = r.json()["id"]
    _write_credentials({"type": "authorized_user", "client_id": credentials.client_id,
                        "refresh_token": credentials.refresh_token, "scopes": SCOPES, "folders": folders})
    from app.training.backup_service import atomic_json
    atomic_json(state_root() / "backup_activation.json", {"enabled": True})
    os.environ["EDURECOVER_BACKUP_ENABLED"] = "true"
    os.environ["EDURECOVER_BACKUP_SCHEDULER_ENABLED"] = "true"


@drive_router.get("/callback")
async def callback(request: Request, state: str = "", code: str = "", error: str = ""):
    pending = _pending.pop(state, None)
    if (not pending or pending[0] < time.time() or not secrets.compare_digest(pending[1], request.cookies.get(COOKIE, ""))):
        raise HTTPException(403, "Google 연결 요청이 만료되었거나 일치하지 않습니다.")
    if error or not code or len(code) > 4096:
        raise HTTPException(400, "Google 연결이 승인되지 않았습니다.")
    try:
        await run_in_threadpool(_complete, code, state, pending[2])
    except Exception:
        # Never expose Google's response, authorization code or refresh token.
        raise HTTPException(503, "Google 연결을 완료하지 못했습니다. OAuth·Drive API 설정을 확인해 주세요.") from None
    from app.training.backup_scheduler import start_backup_scheduler
    await start_backup_scheduler()
    response = RedirectResponse("/owner/secrets", status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    response.delete_cookie(COOKIE, path="/owner/drive", secure=True, httponly=True, samesite="lax")
    return response
