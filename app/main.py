from typing import Any, Literal

import hashlib
import json
import logging
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.training.backup_api import backup_router
from app.services.secret_vault_api import vault_router
from app.services.private_document_store import (
    import_private_documents_from_env,
    private_document_router,
)
from app.training.runtime_storage import prepare_runtime_storage, log_backup_readiness
from app.services.storage_inventory import log_storage_inventory
from app.training.drive_oauth import drive_router
from app.training.backup_scheduler import start_backup_scheduler, stop_backup_scheduler
from app.services.authoring_assets import get_asset_manifest
import app.services.context_overlay  # installs task-scoped provider context builders
from app.services.human_ai_charter import CHARTER
from app.services.owner_basic_auth import require_owner, owner_auth_router, OWNER_LOGOUT_BAR
from app.services.teacher_view import build_teacher_html
from app.services.private_asset_registry import (
    drive_archive_inventory_summary,
    private_asset_integrity_snapshot,
)
from app.services.privacy_guard import ProviderZDRHeld, privacy_status as provider_privacy_status
from app.services.control_plane import (
    ProviderHeld,
    assert_provider_allowed,
    control_status,
    evaluate_provider,
    notebook_tasks,
)
from app.pilot_page import PILOT_HTML
from app.classroom_tutor import classroom_tutor_router, teacher_classroom_router
from app.services.auto_generator import (
    GenerationFailed,
    GeneratorNotConfigured,
    generate_questions,
)
from app.services.provider_verifier import verify_with_provider
from app.services.generation_provenance import (
    ProvenanceError, issue_origin_proof, verify_origin_proof, issue_verification_proof,
)
from app.services.docx_export import build_exam_docx
from app.services.encrypted_backup import (
    backup_encryption_status,
    run_backup_encryption_self_test,
)
from app.services.agent_access_guard import agent_access_status, evaluate_agent_access
from app.services.socratic_trap import detect_socratic_traps
from app.platform_core.life_auth import (
    router as life_auth_router, owner_router as life_owner_router, log_login_readiness,
)
from app.liferecover_runtime import router as liferecover_router
from app.domains.medical.hospital_os_api import router as hospital_os_router
from app.domains.medical.hospital_access_api import router as hospital_access_router, owner_router as hospital_access_owner_router
from app.services.open_model_api import router as open_model_router
from app.services.code_execution_api import router as code_execution_router


app = FastAPI(
    title=settings.project_name,
    version=settings.app_version,
    description=(
        "EduRecover AI backend for Trust Chain, Vaccine Graph, "
        "and Socratic Trap services."
    ),
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


app.include_router(backup_router)
app.include_router(vault_router)
app.include_router(private_document_router)
app.include_router(drive_router)
app.router.add_event_handler("startup", prepare_runtime_storage)
app.router.add_event_handler("startup", import_private_documents_from_env)
app.router.add_event_handler("startup", log_storage_inventory)
app.router.add_event_handler("startup", log_backup_readiness)
app.router.add_event_handler("startup", log_login_readiness)
app.router.add_event_handler("startup", start_backup_scheduler)
app.router.add_event_handler("shutdown", stop_backup_scheduler)
app.include_router(owner_auth_router)
app.include_router(classroom_tutor_router)
app.include_router(teacher_classroom_router)
app.include_router(life_auth_router)
app.include_router(life_owner_router)
app.include_router(liferecover_router)
app.include_router(hospital_os_router)
app.include_router(hospital_access_router)
app.include_router(hospital_access_owner_router)
app.include_router(open_model_router)
app.include_router(code_execution_router)

logger = logging.getLogger(__name__)

@app.on_event("startup")
async def backup_encryption_startup_self_test() -> None:
    """Run a non-secret backup encrypt/decrypt round-trip on each deploy."""
    try:
        result = run_backup_encryption_self_test()
    except Exception as exc:
        logger.error(
            "backup_encryption_self_test=FAILED error_type=%s",
            type(exc).__name__,
        )
        return

    logger.info(
        "backup_encryption_self_test=SUCCESS algorithm=%s roundtrip_verified=%s "
        "temporary_artifacts_removed=%s",
        result["algorithm"],
        result["roundtrip_verified"],
        result["temporary_artifacts_removed"],
    )


@app.middleware("http")
async def agent_ingress_gate(request: Request, call_next):
    """Reject blocked or unapproved AI-agent/automation callers before routing."""
    agent_id = request.headers.get("X-EduRecover-Agent-Id")
    automation_declared = request.headers.get("X-EduRecover-Automation", "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if agent_id or automation_declared:
        decision = evaluate_agent_access(
            agent_id,
            automation_declared=automation_declared,
        )
        if not decision.allowed:
            return JSONResponse(
                status_code=423,
                content={
                    "detail": "AI agent/automation access blocked by owner policy.",
                    "state": decision.state,
                },
                headers={"Cache-Control": "no-store"},
            )
    return await call_next(request)


class VerifyProblemRequest(BaseModel):
    problems: list[dict[str, Any]] = Field(
        ...,
        description="English reading-comprehension problem objects. The teacher chooses the item count.",
    )


class GenerateQuestionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["openai", "gemini", "claude"]
    explicit_authorization: Literal[True]
    source_text: str = Field(..., min_length=1, max_length=100000)
    count: int = Field(default=20, ge=1, le=50)
    subject: str = "영어"
    school_level: str = "고등학교"
    grade: str = "1학년"
    exam_type: str = "중간고사"
    question_types: list[str] = Field(default_factory=list)
    difficulty: str = "쉬움 25% / 보통 50% / 어려움 25%"
    scope_memo: str = ""
    extra_instructions: str = ""


class VerifyWithProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["openai", "gemini", "claude"]
    origin_provider: Literal["openai", "gemini", "claude"]
    origin_invocation_id: str = Field(min_length=8, max_length=128)
    origin_proof: str = Field(min_length=64, max_length=4096)
    explicit_authorization: Literal[True]
    problems: list[dict[str, Any]] = Field(..., min_length=1, max_length=50)


class ReviseAuthoringRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin_proof: str = Field(min_length=64, max_length=4096)
    problems: list[dict[str, Any]] = Field(min_length=1, max_length=50)


class ExportDocxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exam: dict[str, Any]
    items: list[dict[str, Any]] = Field(..., min_length=1, max_length=100)


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _execution_receipt(
    *,
    provider: str,
    action: Literal["generate", "verify"],
    request_payload: dict[str, Any],
    result_payload: dict[str, Any],
    control_epoch: str,
    origin_provider: str | None = None,
    origin_invocation_id: str | None = None,
) -> dict[str, Any]:
    """Return a non-secret, provider-isolated receipt for one explicit call."""
    receipt = {
        "invocation_id": str(uuid.uuid4()),
        "provider": provider,
        "action": action,
        "channel": f"/channel/{provider}/{action}",
        "control_epoch": control_epoch,
        "request_sha256": _sha256_json(request_payload),
        "result_sha256": _sha256_json(result_payload),
        "session_scope": "single_request_only",
        "provider_session_persisted": False,
        "cross_provider_state_shared": False,
        "explicit_authorization": True,
    }
    if origin_provider is not None:
        receipt["origin_provider"] = origin_provider
    if origin_invocation_id is not None:
        receipt["origin_invocation_id"] = origin_invocation_id
    return receipt


class ApplyTrapRequest(BaseModel):
    problems: list[dict[str, Any]] = Field(
        ...,
        description=(
            "English reading-comprehension problem/explanation objects for "
            "Socratic Trap analysis. The teacher chooses the item count."
        ),
    )


@app.get("/", include_in_schema=False)
async def platform_home() -> RedirectResponse:
    """Public entry goes to the teacher-only surface."""
    return RedirectResponse(url="/teacher-free", status_code=307)


@app.get("/owner", response_class=HTMLResponse, include_in_schema=False)
async def owner_platform(_: None = Depends(require_owner)) -> HTMLResponse:
    """Owner-only platform address."""
    return HTMLResponse(
        PILOT_HTML.replace("교사 출제 플랫폼", "소유자 운영 플랫폼", 1)
        .replace(
            "<body>",
            "<body>" + OWNER_LOGOUT_BAR
            + '<nav style="padding:12px;display:flex;gap:10px;flex-wrap:wrap">'
              '<a href="/life" style="display:inline-block;padding:12px 16px;border-radius:10px;'
              'background:#185bc4;color:#fff;text-decoration:none;font-weight:800">Life Recover로 이동</a>'
              '<a href="/owner/life/account-setup" style="display:inline-block;padding:12px 16px;border-radius:10px;'
              'border:1px solid #185bc4;color:#185bc4;text-decoration:none;font-weight:750">Life Recover 계정 만들기</a>'
              '<a href="/owner/secrets" style="display:inline-block;padding:12px 16px">영업비밀 · 백업 관리</a>'
              '<a href="/owner/open-models" style="display:inline-block;padding:12px 16px">선정 25개 모델 호출·수업·출제</a>'
              '<a href="/owner/code-workspace" style="display:inline-block;padding:12px 16px">코딩 도구 · 실행 · 모델별 시험</a>'
              '</nav>',
            1,
        ),
        headers={"Cache-Control": "no-store", "Referrer-Policy": "same-origin"},
    )


@app.get("/teacher-free", response_class=HTMLResponse, include_in_schema=False)
async def free_teacher_platform() -> HTMLResponse:
    """Free-teacher platform with owner/admin diagnostics removed."""
    return HTMLResponse(build_teacher_html(PILOT_HTML))


@app.get("/pilot", include_in_schema=False)
async def legacy_pilot_redirect() -> RedirectResponse:
    """Compatibility redirect; this service is no longer labelled a pilot."""
    return RedirectResponse(url="/teacher-free", status_code=307)


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    """Return a lightweight service health signal."""
    return {
        "status": "ok",
        "service": settings.project_name,
        "app_version": settings.app_version,
        "api_version": settings.api_version,
        "environment": settings.environment,
    }


def _provider_secret_configured(provider: str) -> bool:
    from app.services import bedrock_provider
    if provider in {"openai", "claude"}:
        try:
            if bedrock_provider.uses_bedrock(provider):
                return bedrock_provider.configured(provider)
        except bedrock_provider.BedrockProviderError:
            return False
    return {
        "openai": settings.openai_api_key is not None,
        "gemini": settings.google_credentials_json is not None,
        "claude": settings.anthropic_api_key is not None,
    }[provider]


def _provider_model(provider: str) -> str:
    if provider in {"openai", "claude"} and settings.provider_transport.strip().lower() == "bedrock":
        return {
            "openai": settings.bedrock_openai_inference_profile or "",
            "claude": settings.bedrock_claude_inference_profile or "",
        }[provider]
    return {
        "openai": settings.openai_model,
        "gemini": settings.gemini_model,
        "claude": settings.anthropic_model,
    }[provider]


def _provider_ready_for_teacher(provider: str, action: str = "generate") -> bool:
    configured = _provider_secret_configured(provider)
    try:
        decision = evaluate_provider(provider, action)
        control_allowed = decision.allowed
    except RuntimeError:
        control_allowed = False
    privacy = provider_privacy_status()
    privacy_allowed = bool(
        privacy.get("providers", {})
        .get(provider, {})
        .get("call_allowed_by_privacy_gate", False)
    )
    return bool(configured and control_allowed and privacy_allowed)


@app.get("/ai-status", tags=["authoring"])
async def ai_status() -> dict[str, Any]:
    """Teacher-safe provider readiness. Detailed configuration is owner-only."""
    return {
        "provider_selection_required": True,
        "execution_authentication": "owner_login_required",
        "readiness_basis": "configuration_only_not_live_provider_success",
        "providers": {
            provider: {
                "ready": _provider_ready_for_teacher(provider),
                "verify_ready": _provider_ready_for_teacher(provider, "verify"),
            }
            for provider in ("openai", "gemini", "claude")
        },
    }


@app.get("/owner/ai-status", tags=["owner"])
async def owner_ai_status(_: None = Depends(require_owner)) -> dict[str, Any]:
    """Owner-only provider configuration/control/privacy diagnostics without secret values."""
    privacy = provider_privacy_status()
    rows: dict[str, Any] = {}
    for provider in ("openai", "gemini", "claude"):
        try:
            decision = evaluate_provider(provider, "generate")
            control = {
                "allowed": decision.allowed,
                "state": decision.state,
                "reason": decision.reason,
                "control_epoch": decision.control_epoch,
            }
        except RuntimeError as exc:
            control = {
                "allowed": False,
                "state": "CONTROL_CONFIGURATION_ERROR",
                "reason": type(exc).__name__,
                "control_epoch": "UNAVAILABLE",
            }
        rows[provider] = {
            "configured": _provider_secret_configured(provider),
            "model": _provider_model(provider),
            "thinking_level": settings.gemini_thinking_level if provider == "gemini" else None,
            "product_profile": ("GOOGLE_CLOUD_VERTEX_AI" if provider == "gemini" else
                                "AWS_BEDROCK_CONVERSE" if settings.provider_transport.strip().lower() == "bedrock" else
                                "DIRECT_PROVIDER_API"),
            "location": (settings.gemini_vertex_location if provider == "gemini" else
                         settings.aws_region if settings.provider_transport.strip().lower() == "bedrock" else None),
            "connection_verification": "not_checked_configuration_only",
            "runtime_retention_check": ("optional_diagnostic_not_enforced" if provider in {"openai", "claude"}
                                        and settings.provider_transport.strip().lower() == "bedrock" else None),
            "control": control,
            "privacy": privacy.get("providers", {}).get(provider, {}),
        }
    return {
        "provider_selection_required": True,
        "providers": rows,
        "strict_provider_zdr_required": privacy.get("strict_provider_zdr_required", False),
        "platform_chat_history_forwarded": False,
        "provider_calls_are_stateless": True,
    }


@app.get("/authoring-assets", tags=["authoring"])
async def authoring_assets() -> dict[str, Any]:
    """Teacher-safe summary; internal asset names and locations are not exposed."""
    return {
        "usage": "generation_and_verification",
        "sharing": "task-relevant excerpts only",
        "platform_chat_history_forwarded": False,
        "asset_categories": [
            "학년별 어휘·구문 기준",
            "문항 출제·검수 기준",
            "오답 설계 기준",
            "문항유형별 검증 기준",
        ],
    }


@app.get("/owner/authoring-assets", tags=["owner"])
async def owner_authoring_assets(_: None = Depends(require_owner)) -> dict[str, Any]:
    """Owner-only detailed server asset manifest and integrity state."""
    return {
        "runtime_assets": get_asset_manifest(),
        "drive_archive": drive_archive_inventory_summary(),
        "private_assets": private_asset_integrity_snapshot(),
    }


@app.get("/storage-locations", response_class=HTMLResponse, tags=["owner"])
async def storage_locations(_: None = Depends(require_owner)) -> HTMLResponse:
    """Return the non-secret storage-location report."""
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "SERVER_STORAGE_LOCATIONS.txt"
    escaped = path.read_text(encoding="utf-8").replace("&", "&amp;").replace("<", "&lt;")
    return HTMLResponse("<pre>" + escaped + "</pre>")


@app.get("/human-ai-charter", tags=["system"])
async def human_ai_charter() -> dict[str, Any]:
    """Publish the full human-sovereignty reflection and pledge."""
    return CHARTER


@app.get("/control-status", tags=["owner"])
async def platform_control_status(_: None = Depends(require_owner)) -> dict[str, Any]:
    """Expose non-secret owner-sovereignty and AI-control state."""
    return control_status()


@app.get("/owner/agent-access-status", tags=["owner"])
async def owner_agent_access_status(_: None = Depends(require_owner)) -> dict[str, Any]:
    """Return non-secret AI-agent ingress policy and block-list state."""
    return agent_access_status()


@app.get("/owner-notebook-tasks", tags=["owner"])
async def owner_notebook_tasks(_: None = Depends(require_owner)) -> dict[str, Any]:
    """Return the remaining owner-side setup steps without secret values."""
    return {"tasks": notebook_tasks()}


@app.get("/privacy-status", tags=["owner"])
async def privacy_status(_: None = Depends(require_owner)) -> dict[str, Any]:
    """Describe application and provider-retention gates without exposing secrets."""
    status = provider_privacy_status()
    status.update({
        "unknown_request_fields_rejected": True,
        "openai_store_requested": False,
        "full_authoring_corpus_location": "private EduRecover runtime authoring asset directory",
        "api_key_custody": "deployment environment variables only; secret values are not returned to prompts or status APIs",
        "human_owner_auto_blocking": False,
        "provider_zero_data_retention": (
            "External calls are fail-closed when strict ZDR is required and provider-side coverage has not "
            "been explicitly confirmed for the exact organization/project, endpoint and model."
        ),
    })
    return status


@app.get("/owner/backup-encryption-status", tags=["owner"])
async def owner_backup_encryption_status(_: None = Depends(require_owner)) -> dict[str, Any]:
    """Return backup-encryption readiness without exposing any secret value."""
    return backup_encryption_status()


async def _run_generation_channel(
    provider: Literal["openai", "gemini", "claude"],
    payload: GenerateQuestionsRequest,
) -> dict[str, Any]:
    if payload.provider != provider:
        raise HTTPException(status_code=400, detail="Provider/channel mismatch.")

    try:
        decision = assert_provider_allowed(provider, "generate")
        result = await generate_questions(
            provider=provider,
            source_text=payload.source_text,
            count=payload.count,
            subject=payload.subject,
            school_level=payload.school_level,
            grade=payload.grade,
            exam_type=payload.exam_type,
            question_types=payload.question_types,
            difficulty=payload.difficulty,
            scope_memo=payload.scope_memo,
            extra_instructions=payload.extra_instructions,
        )
    except (ProviderHeld, ProviderZDRHeld) as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except GeneratorNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GenerationFailed as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc),
                            headers={"X-EduRecover-Error-Code": exc.code}) from exc

    if result.provider != provider:
        raise HTTPException(status_code=502, detail="AI 응답의 공급자 식별이 요청과 일치하지 않습니다.")
    response = result.to_dict()
    response["requested_provider"] = provider
    response["effective_provider"] = provider
    response["fallback_used"] = False
    receipt_request = payload.model_dump(mode="json")

    response["execution_receipt"] = _execution_receipt(
        provider=result.provider,
        action="generate",
        request_payload=receipt_request,
        result_payload=response,
        control_epoch=decision.control_epoch,
    )
    receipt = response["execution_receipt"]
    receipt["origin_proof"] = issue_origin_proof(provider, receipt["invocation_id"], response["items"])
    return response


@app.post("/owner/authoring/revise", tags=["authoring"], dependencies=[Depends(require_owner)])
async def revise_authoring(payload: ReviseAuthoringRequest) -> dict[str, Any]:
    """Record teacher edits while preserving the authenticated original provider."""
    try:
        origin = verify_origin_proof(payload.origin_proof)
        invocation_id = str(uuid.uuid4())
        proof = issue_origin_proof(origin["provider"], invocation_id, payload.problems,
                                   origin["generation_invocation_id"])
    except (ProvenanceError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"provider": origin["provider"], "invocation_id": invocation_id,
            "generation_invocation_id": origin["generation_invocation_id"],
            "origin_proof": proof, "verification_state": "REQUIRED_FOR_REVISED_CONTENT"}


@app.post("/channel/openai/generate", tags=["authoring"], dependencies=[Depends(require_owner)])
async def generate_openai_channel(payload: GenerateQuestionsRequest) -> dict[str, Any]:
    return await _run_generation_channel("openai", payload)


@app.post("/channel/gemini/generate", tags=["authoring"], dependencies=[Depends(require_owner)])
async def generate_gemini_channel(payload: GenerateQuestionsRequest) -> dict[str, Any]:
    return await _run_generation_channel("gemini", payload)


@app.post("/channel/claude/generate", tags=["authoring"], dependencies=[Depends(require_owner)])
async def generate_claude_channel(payload: GenerateQuestionsRequest) -> dict[str, Any]:
    return await _run_generation_channel("claude", payload)


@app.post("/generate-questions", tags=["authoring"], deprecated=True)
async def legacy_generate_questions(payload: GenerateQuestionsRequest) -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="Use the provider-isolated channel endpoint.")


async def _run_verification_channel(
    provider: Literal["openai", "gemini", "claude"],
    payload: VerifyWithProviderRequest,
) -> dict[str, Any]:
    if payload.provider != provider:
        raise HTTPException(status_code=400, detail="Provider/channel mismatch.")
    try:
        origin_claims = verify_origin_proof(payload.origin_proof, provider=payload.origin_provider,
            invocation_id=payload.origin_invocation_id, problems=payload.problems)
    except (ProvenanceError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if origin_claims["provider"] == provider:
        raise HTTPException(
            status_code=409,
            detail="Cross-verification must use a provider different from the generation provider.",
        )
    try:
        decision = assert_provider_allowed(provider, "verify")
        result = await verify_with_provider(provider, payload.problems)
    except ProviderHeld as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except ProviderZDRHeld as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except GeneratorNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GenerationFailed as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc),
                            headers={"X-EduRecover-Error-Code": exc.code}) from exc

    response = dict(result)
    response["origin_provider"] = payload.origin_provider
    response["execution_receipt"] = _execution_receipt(
        provider=provider,
        action="verify",
        request_payload=payload.model_dump(mode="json"),
        result_payload=response,
        control_epoch=decision.control_epoch,
        origin_provider=payload.origin_provider,
        origin_invocation_id=payload.origin_invocation_id,
    )
    if response.get("verdict") == "PASS":
        receipt = response["execution_receipt"]
        receipt["verification_proof"] = issue_verification_proof(provider,
            receipt["invocation_id"], payload.problems, origin_claims, "PASS")
    return response


@app.post("/channel/openai/verify", tags=["authoring"], dependencies=[Depends(require_owner)])
async def verify_openai_channel(payload: VerifyWithProviderRequest) -> dict[str, Any]:
    return await _run_verification_channel("openai", payload)


@app.post("/channel/gemini/verify", tags=["authoring"], dependencies=[Depends(require_owner)])
async def verify_gemini_channel(payload: VerifyWithProviderRequest) -> dict[str, Any]:
    return await _run_verification_channel("gemini", payload)


@app.post("/channel/claude/verify", tags=["authoring"], dependencies=[Depends(require_owner)])
async def verify_claude_channel(payload: VerifyWithProviderRequest) -> dict[str, Any]:
    return await _run_verification_channel("claude", payload)


@app.post("/verify-with-provider", tags=["authoring"], deprecated=True)
async def legacy_verify_provider(payload: VerifyWithProviderRequest) -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="Use the provider-isolated channel endpoint.")


@app.post("/verify-problem", tags=["trust-chain"], deprecated=True,
          dependencies=[Depends(require_owner)])
async def verify_problem(payload: VerifyProblemRequest) -> dict[str, Any]:
    """The legacy automatic multi-provider route cannot authorize an AI call."""
    raise HTTPException(
        status_code=410,
        detail="교차검증 창에서 출제 AI와 다른 AI 한 개를 선택해 주세요. /channel/{provider}/verify 경로를 사용합니다.",
    )


@app.post("/export-docx", tags=["output"])
async def export_docx(payload: ExportDocxRequest) -> Response:
    """Build the current teacher workspace as a downloadable DOCX exam + answer sheet."""
    document_bytes = build_exam_docx(payload.exam, payload.items)
    return Response(
        content=document_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": 'attachment; filename="edurecover_exam.docx"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/apply-trap", tags=["socratic-trap"])
async def apply_trap(payload: ApplyTrapRequest) -> dict[str, Any]:
    """Detect reasoning traps and return Socratic re-check prompts."""

    result = detect_socratic_traps(payload.problems)
    return result.to_dict()
