from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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
.card{{display:block;background:#fff;padding:18px;border-radius:16px;box-shadow:0 4px 18px #0000000d;
color:#172b45;text-decoration:none;min-height:155px}}
.card:active{{transform:scale(.99)}}.card b{{font-size:18px}}.card p{{font-weight:500;line-height:1.65}}
.badge{{font-size:13px;color:#53677e}}.login{{display:inline-block;margin-top:5px;color:#1457b8;text-decoration:none;font-weight:700}}
</style></head><body><main>
<header><h1>생활의 회복</h1><div>Life Recover</div><p class='badge'>{auth_text}</p>
<a class='login' href='/life/auth/login'>로그인</a></header>
<div class='grid'>
<a class='card' href='/life/daily'><b>일상</b><p>일정·활동·문서·지원사업</p></a>
<a class='card' href='/life/medical'><b>의료</b><p>약물패스포트·치료목적·안전확인</p></a>
<a class='card' href='/life/legal'><b>법률</b><p>사건 타임라인·증거·기한관리</p></a>
<a class='card' href='/life/privacy'><b>개인정보</b><p>동의·공유범위·내 정보 사용이력</p></a>
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


def _page_user_or_login(request: Request) -> str | RedirectResponse:
    try:
        return require_life_user(request)
    except HTTPException:
        return RedirectResponse("/life/auth/login", status_code=303)


def _section_page(*, title: str, subtitle: str, items: list[dict], empty_text: str) -> HTMLResponse:
    if items:
        body = "".join(
            f"<div class='item'><b>{escape(str(item.get('kind','자료')))}</b><p>{escape(str(item.get('updated_at','')))}</p></div>"
            for item in items
        )
    else:
        body = f"<div class='empty'>{escape(empty_text)}</div>"
    html = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{escape(title)} · Life Recover</title>
<style>
body{{margin:0;font-family:system-ui,-apple-system,'Noto Sans KR',sans-serif;background:#f5f7fa;color:#172b45}}
main{{max-width:760px;margin:auto;padding:20px}}a{{color:#1457b8;text-decoration:none;font-weight:700}}
.top{{display:inline-block;margin:8px 0 18px}}h1{{margin:0 0 6px}}.sub{{color:#53677e;margin-bottom:22px}}
.item,.empty{{background:#fff;padding:16px;border-radius:14px;margin:10px 0;box-shadow:0 4px 18px #0000000d}}
.item p{{margin:6px 0 0;color:#657589;font-size:13px}}
</style></head><body><main><a class='top' href='/life'>← 홈</a>
<h1>{escape(title)}</h1><div class='sub'>{escape(subtitle)}</div>{body}</main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer",
        "X-Content-Type-Options":"nosniff","Content-Security-Policy":"default-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"})


@router.get("/life/daily", response_class=HTMLResponse)
def daily_page(request: Request):
    user = _page_user_or_login(request)
    if isinstance(user, RedirectResponse):
        return user
    items = list_resources(domain="life", subject_id=user)
    return _section_page(
        title="일상",
        subtitle="일정 · 활동 · 문서 · 지원사업",
        items=items,
        empty_text="아직 저장된 일상 자료가 없습니다.",
    )


@router.get("/life/medical", response_class=HTMLResponse)
def medical_page(request: Request):
    user = _page_user_or_login(request)
    if isinstance(user, RedirectResponse):
        return user
    items = list_resources(domain="medical", subject_id=user)
    body = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>의료 · Life Recover</title>
<style>
body{{margin:0;font-family:system-ui,-apple-system,'Noto Sans KR',sans-serif;background:#f5f7fa;color:#172b45}}
main{{max-width:760px;margin:auto;padding:20px}}a{{color:#1457b8;text-decoration:none;font-weight:700}}
.hero{{display:block;background:#172b45;color:#fff;padding:20px;border-radius:16px;margin:18px 0;text-decoration:none}}
.hero b{{font-size:20px}}.hero p{{margin:8px 0 0;line-height:1.6;font-weight:500;color:#dfe8f5}}
.card{{background:#fff;padding:16px;border-radius:14px;margin:10px 0;box-shadow:0 4px 18px #0000000d}}
.sub{{color:#53677e;margin-bottom:18px}}.count{{font-size:13px;color:#66788c}}
</style></head><body><main><a href='/life'>← 홈</a><h1>의료</h1>
<div class='sub'>약물패스포트 · 치료목적 · 안전확인</div>
<a class='hero' href='/life/medical/hospital-os'><b>Medical Recover Hospital OS</b>
<p>변경된 의료정보가 어떤 현재 업무에 영향을 주는지 확인하고, 필요한 업무만 재확인하도록 관리합니다.</p></a>
<div class='count'>저장된 의료 자료 {len(items)}건</div>
</main></body></html>"""
    return HTMLResponse(body, headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer",
        "X-Content-Type-Options":"nosniff","Content-Security-Policy":"default-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"})


@router.get("/life/medical/hospital-os", response_class=HTMLResponse)
def hospital_os_page(request: Request):
    user = _page_user_or_login(request)
    if isinstance(user, RedirectResponse):
        return user
    actions = list_resources(domain="medical", subject_id=user, kind="hospital_os_clinical_action")
    sources = list_resources(domain="medical", subject_id=user, kind="hospital_os_source_revision")
    receipts = list_resources(domain="medical", subject_id=user, kind="hospital_os_revalidation_receipt")
    identities = list_resources(domain="medical", subject_id=user, kind="hospital_os_identity_check")
    medications = list_resources(domain="medical", subject_id=user, kind="hospital_os_medication_object")
    holds = sum(1 for item in actions if item["payload"].get("action_state") == "HOLD")
    rechecks = sum(1 for item in actions if item["payload"].get("action_state") == "REVALIDATION_REQUIRED")
    medication_holds = sum(1 for item in medications if item["payload"].get("safety_state") == "HOLD")
    html = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Medical Recover Hospital OS</title>
<style>
body{{margin:0;font-family:system-ui,-apple-system,'Noto Sans KR',sans-serif;background:#f5f7fa;color:#172b45}}
main{{max-width:820px;margin:auto;padding:20px}}a{{color:#1457b8;text-decoration:none;font-weight:700}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:20px 0}}
.box{{background:#fff;padding:18px;border-radius:15px;box-shadow:0 4px 18px #0000000d}}
.num{{font-size:28px;font-weight:850;margin-top:5px}}.note{{background:#eef5ff;padding:16px;border-radius:14px;line-height:1.6}}
@media(max-width:520px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main><a href='/life/medical'>← 의료</a>
<h1>Medical Recover Hospital OS</h1>
<p>현재 의료업무에 실제로 사용된 정보의 변경 영향을 추적하는 안전·업무 관리 계층입니다.</p>
<div class='grid'>
<div class='box'>의료정보 변경본<div class='num'>{len(sources)}</div></div>
<div class='box'>현재 업무<div class='num'>{len(actions)}</div></div>
<div class='box'>현재 HOLD<div class='num'>{holds}</div></div>
<div class='box'>재확인 필요<div class='num'>{rechecks}</div></div>
<div class='box'>재확인 기록<div class='num'>{len(receipts)}</div></div>
<div class='box'>환자확인 기록<div class='num'>{len(identities)}</div></div>
<div class='box'>투약 일시 보류<div class='num'>{medication_holds}</div></div>
</div>
<div class='note'><b>안전 원칙</b><br>
불확실한 정보를 자동으로 확인 완료 상태로 올리지 않습니다.<br>
이미 종료된 과거 업무는 변경하지 않고, 실제로 해당 정보를 사용한 현재 업무만 다시 확인 대상으로 분류합니다.<br>
환자확인이 맞지 않거나 확인되지 않으면 완료 단계로 넘어가지 않습니다.<br>
예전 처방으로 준비된 미투약 약 중 변경 영향을 받는 것만 일시 보류합니다.<br>
처방 변경이나 수술 가능 여부를 시스템이 자동 결정하지 않습니다.</div>
</main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer",
        "X-Content-Type-Options":"nosniff","Content-Security-Policy":"default-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"})


@router.get("/life/legal", response_class=HTMLResponse)
def legal_page(request: Request):
    user = _page_user_or_login(request)
    if isinstance(user, RedirectResponse):
        return user
    items = list_resources(domain="legal", subject_id=user)
    return _section_page(
        title="법률",
        subtitle="사건 타임라인 · 증거 · 기한관리",
        items=items,
        empty_text="아직 저장된 법률 자료가 없습니다.",
    )


@router.get("/life/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    user = _page_user_or_login(request)
    if isinstance(user, RedirectResponse):
        return user
    html = """<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>개인정보 · Life Recover</title>
<style>
body{margin:0;font-family:system-ui,-apple-system,'Noto Sans KR',sans-serif;background:#f5f7fa;color:#172b45}
main{max-width:760px;margin:auto;padding:20px}a{color:#1457b8;text-decoration:none;font-weight:700}
.card{background:#fff;padding:17px;border-radius:14px;margin:12px 0;box-shadow:0 4px 18px #0000000d}
h1{margin:18px 0 6px}.sub{color:#53677e;margin-bottom:20px}
</style></head><body><main><a href='/life'>← 홈</a><h1>개인정보</h1>
<div class='sub'>내 정보가 어떻게 사용되는지 직접 확인합니다.</div>
<div class='card'><b>동의</b><p>어떤 정보를 어떤 목적으로 사용할지 확인합니다.</p></div>
<div class='card'><b>공유범위</b><p>어떤 자료를 누구에게 공유하도록 허용했는지 확인합니다.</p></div>
<div class='card'><b>내 정보 사용이력</b><p>내 정보가 언제 어떤 용도로 사용되었는지 확인합니다.</p></div>
</main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer",
        "X-Content-Type-Options":"nosniff","Content-Security-Policy":"default-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"})


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
