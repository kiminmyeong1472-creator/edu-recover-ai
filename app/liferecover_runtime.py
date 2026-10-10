from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from app.platform_core.life_auth import require_life_user
from app.domains.medical.hospital_access_api import require_hospital_permission, hospital_list_resources
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
    try:
        subject_id = require_hospital_permission(request, "hospital.dashboard")
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse("/life/auth/login", status_code=303)
        return HTMLResponse(
            "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Hospital OS 접근 제한</title></head><body>"
            "<main><h1>Hospital OS 접근 제한</h1>"
            "<p>현재 계정에 Hospital Today 조회 권한이 없습니다.</p>"
            "<a href='/life/medical'>의료 화면으로 돌아가기</a></main></body></html>",
            status_code=403,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    actions = [
        item["payload"]
        for item in hospital_list_resources(
            domain="medical", subject_id=subject_id, kind="hospital_os_clinical_action"
        )
    ]
    medications = [
        item["payload"]
        for item in hospital_list_resources(
            domain="medical", subject_id=subject_id, kind="hospital_os_medication_object"
        )
    ]
    workflows = [
        item["payload"]
        for item in hospital_list_resources(
            domain="medical", subject_id=subject_id, kind="hospital_os_er_workflow"
        )
    ]
    claims = [
        item["payload"]
        for item in hospital_list_resources(
            domain="medical", subject_id=subject_id, kind="hospital_os_claim_readiness"
        )
    ]
    operations = [
        item["payload"]
        for item in hospital_list_resources(
            domain="medical", subject_id=subject_id, kind="hospital_os_operation_record"
        )
    ]
    patient_statuses = [
        item["payload"]
        for item in hospital_list_resources(
            domain="medical", subject_id=subject_id, kind="hospital_os_patient_operational_status"
        )
        if item["payload"].get("state") == "CURRENT"
    ]
    events = [
        item["payload"]
        for item in hospital_list_resources(
            domain="medical", subject_id=subject_id, kind="hospital_os_event"
        )
    ]

    def operation_count(kind: str) -> int:
        return sum(
            1 for item in operations
            if item.get("operation_type") == kind and item.get("state") != "DONE"
        )

    def event_is_recent(item: dict) -> bool:
        raw = item.get("occurred_at")
        if not raw:
            return False
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
            return 0 <= age <= 3600
        except (ValueError, TypeError):
            return False

    recent_events = sorted(
        (item for item in events if event_is_recent(item)),
        key=lambda item: item.get("occurred_at") or "",
        reverse=True,
    )[:8]

    holds = sum(1 for item in actions if item.get("action_state") == "HOLD")
    rechecks = sum(
        1 for item in actions if item.get("action_state") == "REVALIDATION_REQUIRED"
    )
    medication_holds = sum(
        1 for item in medications if item.get("safety_state") == "HOLD"
    )
    er_unfinished = sum(
        1 for item in workflows
        if item.get("workflow_state") not in {"ADMISSION", "TRANSFER", "DISCHARGE"}
        or item.get("pending_items")
    )
    claim_review = sum(1 for item in claims if item.get("review_reasons"))
    outpatient = sum(1 for item in patient_statuses if item.get("care_setting") == "OUTPATIENT")
    inpatient = sum(1 for item in patient_statuses if item.get("care_setting") == "INPATIENT")
    er_patients = sum(1 for item in patient_statuses if item.get("care_setting") == "ER")
    discharge_planned = sum(1 for item in patient_statuses if item.get("discharge_planned"))
    surgery_planned = sum(1 for item in patient_statuses if item.get("surgery_planned"))

    recent_html = "".join(
        "<div class='change'><b>"
        + escape(str(item.get("event_type", "변경")))
        + "</b><span>"
        + escape(str(item.get("occurred_at", "")))
        + "</span></div>"
        for item in recent_events
    ) or "<div class='empty'>최근 1시간 변경 기록이 없습니다.</div>"

    cards = [
        ("외래환자", outpatient),
        ("입원환자", inpatient),
        ("응급실 환자", er_patients),
        ("퇴원예정", discharge_planned),
        ("수술예정", surgery_planned),
        ("현재 HOLD", holds),
        ("재확인 필요", rechecks),
        ("투약보류", medication_holds),
        ("응급실 미완료", er_unfinished),
        ("청구 검토", claim_review),
        ("병상대기", operation_count("BED_WAIT")),
        ("서명대기", operation_count("SIGNATURE_PENDING")),
        ("검사대기", operation_count("TEST_PENDING")),
        ("협진대기", operation_count("CONSULT_PENDING")),
        ("청구오류", operation_count("BILLING_ERROR")),
        ("미수금", operation_count("RECEIVABLE")),
        ("재고부족", operation_count("INVENTORY_SHORTAGE")),
        ("직원공백", operation_count("STAFF_GAP")),
    ]
    cards_html = "".join(
        f"<div class='box'><span>{escape(label)}</span><strong>{value}</strong></div>"
        for label, value in cards
    )

    tenant_badge = ""
    tenant_id = getattr(subject_id, "tenant_id", None)
    if tenant_id:
        tenant_badge = (
            "<div class='tenant'>병원 조직: "
            + escape(str(tenant_id))
            + "</div>"
        )

    html = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Hospital Today · Medical Recover</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;font-family:system-ui,-apple-system,'Noto Sans KR',sans-serif;
background:#f4f7fb;color:#172b45}}main{{max-width:1100px;margin:auto;padding:20px}}
a{{color:#1457b8;text-decoration:none;font-weight:750}}h1{{margin:14px 0 5px}}
.sub{{color:#5b6d82;line-height:1.55}}.tenant{{display:inline-block;background:#e8f1ff;color:#164f9b;
padding:7px 11px;border-radius:999px;font-size:13px;margin-top:10px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;margin:22px 0}}
.box{{background:#fff;border-radius:14px;padding:15px;box-shadow:0 4px 18px #1730500c}}
.box span{{display:block;color:#5b6d82;font-size:13px}}.box strong{{display:block;font-size:27px;margin-top:5px}}
.panel{{background:#fff;border-radius:16px;padding:18px;margin:14px 0;box-shadow:0 4px 18px #1730500c}}
.panel h2{{font-size:18px;margin:0 0 12px}}.review{{display:flex;flex-wrap:wrap;gap:8px}}
.pill{{background:#fff3df;border:1px solid #efd4a5;border-radius:999px;padding:8px 11px;font-size:13px}}
.change{{display:flex;justify-content:space-between;gap:15px;padding:10px 0;border-bottom:1px solid #edf1f5}}
.change:last-child{{border-bottom:0}}.change span{{font-size:12px;color:#6c7e92}}.empty{{color:#718398}}
.note{{background:#eef5ff;border-radius:14px;padding:15px;line-height:1.65;color:#31506f}}
</style></head><body><main>
<a href='/life/medical'>← 의료</a><h1>Hospital Today</h1>
<div class='sub'>지금 병원에서 막힌 곳과 최근 변경을 한 화면에서 확인합니다.</div>{tenant_badge}
<div class='grid'>{cards_html}</div>
<div class='panel'><h2>지금 확인할 것</h2><div class='review'>
<div class='pill'>현재 HOLD {holds}</div><div class='pill'>재확인 {rechecks}</div>
<div class='pill'>투약보류 {medication_holds}</div><div class='pill'>ER 미완료 {er_unfinished}</div>
<div class='pill'>청구 검토 {claim_review}</div></div></div>
<div class='panel'><h2>지난 1시간 변경</h2>{recent_html}</div>
<div class='note'><b>안전 원칙</b><br>
불확실한 정보는 자동으로 VERIFIED가 되지 않습니다. 환자확인 불일치·미확인 상태에서는 완료를 막습니다.
처방이나 수술 가능 여부를 시스템이 자동 결정하지 않으며, 변경 영향이 있는 현재 업무만 재확인 대상으로 표시합니다.</div>
</main></body></html>"""
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
        },
    )


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
    if payload.kind.strip().lower().startswith("hospital_os_"):
        raise HTTPException(status_code=403, detail="hospital_resource_requires_dedicated_api")
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
    return {"resources": [item for item in list_resources(domain=domain, subject_id=account_id)
                          if not item["kind"].startswith("hospital_os_")]}


@router.get("/life/api/resources/{domain}/{resource_id}")
def resource(domain: Literal["life","medical","legal"], resource_id: str, request: Request) -> dict:
    account_id = require_life_user(request)
    item = get_resource(domain=domain, subject_id=account_id, resource_id=resource_id)
    if not item or item["kind"].startswith("hospital_os_"):
        raise HTTPException(status_code=404, detail="resource_not_found")
    return item


@router.get("/owner/liferecover-storage-status")
def owner_storage_status(_: None = Depends(require_owner)) -> dict:
    return storage_status()

