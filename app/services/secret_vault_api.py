"""Private mobile owner console; all reads and writes require existing owner auth."""
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.services.owner_basic_auth import require_owner
from app.services.secret_vault import Revision, SecretVault, VaultError

vault_router = APIRouter(prefix="/owner", dependencies=[Depends(require_owner)], tags=["owner"])
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"}


class Preview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    purpose: str
    subject: str = "영어"
    grade: str = ""
    question_types: list[str] = Field(default_factory=list, max_length=30)
    source_text: str = Field(default="", max_length=50000)


@vault_router.get("/secrets", response_class=HTMLResponse)
def console():
    return HTMLResponse(Path(__file__).with_name("secret_vault.html").read_text(encoding="utf-8"), headers=HEADERS)


@vault_router.get("/secrets/assets")
def assets():
    return JSONResponse({"assets": SecretVault().assets()}, headers=HEADERS)


@vault_router.get("/secrets/assets/{asset_id}")
def get_asset(asset_id: str):
    try:
        return JSONResponse(SecretVault().get(asset_id), headers=HEADERS)
    except VaultError:
        raise HTTPException(404, "자료를 찾을 수 없습니다.") from None


@vault_router.post("/secrets/assets")
def save_asset(value: Revision):
    try:
        return JSONResponse(SecretVault().save(value), headers=HEADERS)
    except VaultError as exc:
        raise HTTPException(409, str(exc)) from None


@vault_router.post("/secrets/preview")
def preview(value: Preview):
    try:
        return JSONResponse(SecretVault().select(**value.model_dump(), record=False), headers=HEADERS)
    except VaultError as exc:
        raise HTTPException(422, str(exc)) from None


@vault_router.get("/secrets/receipts")
def receipts():
    return JSONResponse({"receipts": SecretVault().receipts()}, headers=HEADERS)
