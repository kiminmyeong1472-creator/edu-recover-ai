from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from app.platform_core.life_auth import require_life_user
from app.platform_core.persistent_domain_store import (
    get_resource,
    list_resources,
    put_resource,
    storage_status,
)
from app.services.owner_basic_auth import require_owner


router = APIRouter(tags=["life-recover-runtime"])


class ResourceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: Literal["life", "medical", "legal"]
    kind: str = Field(min_length=1, max_length=80)
    payload: dict


@router.get("/life", response_class=HTMLResponse)
def life_home(request: Request) -> HTMLResponse:
    try:
        account_id = require_life_user(request)
        identity = escape(account_id[-8:])
        auth_text = f"로그인됨 · {identity}"
    except HTTPException:
        auth_text = "로그인이 필요합니다."

    html = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>생활의 회복 · Life Recover</title>
<style>
body{{margin:0;font-family:system-ui,-apple-system,'Noto Sans KR',sans-serif;background:#f5f7fa;color:#172b45}}
main{{max-width:760px;margin:auto;padding:20px}}header{{padding:20px 0}}h1{{margin:0 0 6px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.card{{background:#fff;padding:18px;border-radius:16px;box-shadow:0 4px 18px #0000000d}}
.badge{{font-size:13px;color:#53677e}}a{{color:#1457b8;text-decoration:none;font-weight:700}}
</style></head><body><main>
<header><h1>생활의 회복</h1><div>Life Recover</div><p class='badge'>{auth_text}</p></header>
<div class='grid'>
<div class='card'><b>일상</b><p>일정·활동·문서·지원사업</p></div>
<div class='card'><b>의료</b><p>약물패스포트·치료목적·안전확인</p></div>
<div class='card'><b>법률</b><p>사건 타임라인·증거·기한관리</p></div>
<div class='card'><b>개인정보</b><p>동의·공유범위·감사기록</p></div>
</div></main></body></html>"""
    return HTMLResponse(
        html,
        headers={
            "Cache-Control":"no-store",
            "Referrer-Policy":"no-referrer",
            "X-Content-Type-Options":"nosniff",
            "Content-Security-Policy":"default-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
        },
    )


@router.get("/life/api/me")
def me(request: Request) -> dict[str, str]:
    return {"account_id": require_life_user(request)}


@router.post("/life/api/resources")
def save_resource(payload: ResourceWrite, request: Request) -> dict[str, str]:
    account_id = require_life_user(request)
    resource_id = put_resource(
        domain=payload.domain,
        subject_id=account_id,
        kind=payload.kind,
        payload=payload.payload,
    )
    return {"resource_id": resource_id, "domain": payload.domain}


@router.get("/life/api/resources/{domain}")
def resources(domain: Literal["life","medical","legal"], request: Request) -> dict:
    account_id = require_life_user(request)
    return {"resources": list_resources(domain=domain, subject_id=account_id)}


@router.get("/life/api/resources/{domain}/{resource_id}")
def resource(domain: Literal["life","medical","legal"], resource_id: str, request: Request) -> dict:
    account_id = require_life_user(request)
    item = get_resource(domain=domain, subject_id=account_id, resource_id=resource_id)
    if not item:
        raise HTTPException(status_code=404, detail="resource_not_found")
    return item


@router.get("/owner/liferecover-storage-status")
def owner_storage_status(_: None = Depends(require_owner)) -> dict:
    return storage_status()
