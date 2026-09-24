from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
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
