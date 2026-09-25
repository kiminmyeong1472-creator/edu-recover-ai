from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.pilot_page import PILOT_HTML
from app.services.socratic_trap import (
    SOCRATIC_EXPECTED_BATCH_SIZE,
    detect_socratic_traps,
)
from app.services.trust_chain import EXPECTED_BATCH_SIZE, run_trust_chain


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
        description="Exactly 28 English reading-comprehension problem objects.",
    )


class ApplyTrapRequest(BaseModel):
    problems: list[dict[str, Any]] = Field(
        ...,
        description=(
            "Exactly 28 English reading-comprehension problem/explanation "
            "objects for Socratic Trap analysis."
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
    """Cross-validate a 28-problem dataset with strict unanimity."""

    if len(payload.problems) != EXPECTED_BATCH_SIZE:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Trust Chain requires exactly {EXPECTED_BATCH_SIZE} problems; "
                f"received {len(payload.problems)}."
            ),
        )

    result = await run_trust_chain(payload.problems)
    return result.to_dict()


@app.post("/apply-trap", tags=["socratic-trap"])
async def apply_trap(payload: ApplyTrapRequest) -> dict[str, Any]:
    """Detect reasoning traps and return Socratic re-check prompts."""

    if len(payload.problems) != SOCRATIC_EXPECTED_BATCH_SIZE:
        raise HTTPException(
            status_code=422,
            detail=(
                "Socratic Trap requires exactly "
                f"{SOCRATIC_EXPECTED_BATCH_SIZE} problems; "
                f"received {len(payload.problems)}."
            ),
        )

    result = detect_socratic_traps(payload.problems)
    return result.to_dict()
