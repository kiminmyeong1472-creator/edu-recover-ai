from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.services.authoring_assets import get_asset_manifest
from app.pilot_page import PILOT_HTML
from app.services.auto_generator import (
    GenerationFailed,
    GeneratorNotConfigured,
    generate_questions,
)
from app.services.provider_verifier import verify_with_provider
from app.services.socratic_trap import detect_socratic_traps
from app.services.trust_chain import run_trust_chain


app = FastAPI(
    title=settings.project_name,
    version=settings.app_version,
    description=(
        "EduRecover AI backend for Trust Chain, Vaccine Graph, "
        "and Socratic Trap services."
    ),
)


class VerifyProblemRequest(BaseModel):
    problems: list[dict[str, Any]] = Field(
        ...,
        description="English reading-comprehension problem objects. The teacher chooses the item count.",
    )


class GenerateQuestionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["openai", "gemini", "claude"] = "openai"
    source_text: str = Field(..., min_length=1)
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
    problems: list[dict[str, Any]] = Field(..., min_length=1)


class ApplyTrapRequest(BaseModel):
    problems: list[dict[str, Any]] = Field(
        ...,
        description=(
            "English reading-comprehension problem/explanation objects for "
            "Socratic Trap analysis. The teacher chooses the item count."
        ),
    )


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/pilot", response_class=HTMLResponse, include_in_schema=False)
async def teacher_pilot() -> HTMLResponse:
    """Serve the teacher-facing pilot console."""
    return HTMLResponse(PILOT_HTML)


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


@app.get("/ai-status", tags=["authoring"])
async def ai_status() -> dict[str, Any]:
    """Report provider configuration without exposing any API secret values."""
    return {
        "automatic_generation_endpoint": "/generate-questions",
        "openai": {
            "configured": settings.openai_api_key is not None,
            "model": settings.openai_model,
        },
        "gemini": {
            "configured": settings.gemini_api_key is not None,
            "model": settings.gemini_model,
        },
        "claude": {
            "configured": settings.anthropic_api_key is not None,
            "model": settings.anthropic_model,
        },
        "default_provider": "openai",
    }


@app.get("/authoring-assets", tags=["authoring"])
async def authoring_assets() -> dict[str, Any]:
    """List private server-side EduRecover authoring assets without exposing raw internal text."""
    return {
        "usage": "generation_and_verification",
        "sharing": "task-relevant excerpts only",
        "platform_chat_history_forwarded": False,
        "assets": get_asset_manifest(),
    }


@app.get("/privacy-status", tags=["system"])
async def privacy_status() -> dict[str, Any]:
    """Describe application-level data-minimization controls."""
    return {
        "platform_chat_history_forwarded": False,
        "unknown_request_fields_rejected": True,
        "openai_store_requested": False,
        "gemini_store_requested": False,
        "full_authoring_corpus_location": "private EduRecover server/repository",
        "provider_zero_data_retention": (
            "Provider-side ZDR/retention controls must be separately enabled or contracted. "
            "The application does not claim provider-side zero retention by itself."
        ),
    }


@app.post("/generate-questions", tags=["authoring"])
async def generate_question_drafts(
    payload: GenerateQuestionsRequest,
) -> dict[str, Any]:
    """Generate teacher-editable draft questions from supplied source material."""
    try:
        result = await generate_questions(
            provider=payload.provider,
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
    except GeneratorNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GenerationFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return result.to_dict()


@app.post("/verify-with-provider", tags=["authoring"])
async def verify_generated_item_with_provider(
    payload: VerifyWithProviderRequest,
) -> dict[str, Any]:
    """Cross-check generated item(s) with one explicitly selected AI provider."""
    try:
        return await verify_with_provider(payload.provider, payload.problems)
    except GeneratorNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GenerationFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/verify-problem", tags=["trust-chain"])
async def verify_problem(payload: VerifyProblemRequest) -> dict[str, Any]:
    """Cross-validate a teacher-selected problem set with strict unanimity."""

    result = await run_trust_chain(payload.problems)
    return result.to_dict()


@app.post("/apply-trap", tags=["socratic-trap"])
async def apply_trap(payload: ApplyTrapRequest) -> dict[str, Any]:
    """Detect reasoning traps and return Socratic re-check prompts."""

    result = detect_socratic_traps(payload.problems)
    return result.to_dict()
