from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.pilot_page import PILOT_HTML
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
