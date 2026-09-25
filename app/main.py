from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.pilot_page import PILOT_HTML
from app.services.auto_generator import (
    GenerationFailed,
    GeneratorNotConfigured,
    generate_questions,
)
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
    provider: Literal["openai", "gemini"] = "openai"
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
        "anthropic_generation": "not_used",
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
