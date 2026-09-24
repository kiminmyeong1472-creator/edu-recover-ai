"""Prototype Trust Chain cross-validation service.

The protocol deliberately rejects majority voting. A 28-item problem set is
approved only when every independent worker returns PASS. Any split decision,
worker failure, or negative verdict moves the set to revalidation pending.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


EXPECTED_BATCH_SIZE = 28


class WorkerVerdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class TrustChainStatus(StrEnum):
    APPROVED = "APPROVED"
    REJECTED_REVALIDATION_PENDING = "REJECTED_REVALIDATION_PENDING"


@dataclass(frozen=True)
class WorkerResult:
    worker: str
    verdict: WorkerVerdict
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class TrustChainResult:
    status: TrustChainStatus
    unanimous: bool
    majority_rule_used: bool
    worker_results: tuple[WorkerResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "unanimous": self.unanimous,
            "majority_rule_used": self.majority_rule_used,
            "worker_results": [
                {
                    **asdict(result),
                    "verdict": result.verdict.value,
                    "reasons": list(result.reasons),
                }
                for result in self.worker_results
            ],
        }


class VirtualAIWorker:
    """Independent virtual validator standing in for an external AI provider."""

    def __init__(self, name: str, latency_seconds: float) -> None:
        self.name = name
        self.latency_seconds = latency_seconds

    async def validate(self, problems: list[dict[str, Any]]) -> WorkerResult:
        # Simulate independent asynchronous model latency without calling
        # external providers in this prototype.
        await asyncio.sleep(self.latency_seconds)

        reasons: list[str] = []

        if len(problems) != EXPECTED_BATCH_SIZE:
            reasons.append(
                f"Expected {EXPECTED_BATCH_SIZE} problems, received {len(problems)}."
            )

        for index, problem in enumerate(problems, start=1):
            if not isinstance(problem, dict):
                reasons.append(f"Problem {index} is not a JSON object.")
                continue

            required_fields = ("question", "answer", "explanation")
            missing = [field for field in required_fields if not problem.get(field)]
            if missing:
                reasons.append(
                    f"Problem {index} is missing required fields: {', '.join(missing)}."
                )

        verdict = WorkerVerdict.PASS if not reasons else WorkerVerdict.FAIL
        return WorkerResult(
            worker=self.name,
            verdict=verdict,
            reasons=tuple(reasons),
        )


WORKERS: tuple[VirtualAIWorker, ...] = (
    VirtualAIWorker("GPT-validator", latency_seconds=0.01),
    VirtualAIWorker("Claude-validator", latency_seconds=0.02),
    VirtualAIWorker("Gemini-validator", latency_seconds=0.03),
)


async def run_trust_chain(
    problems: list[dict[str, Any]],
) -> TrustChainResult:
    """Run all validation workers concurrently and enforce unanimity.

    Approval requires PASS from every worker. A 2:1 split is never treated as
    approval; majority voting is explicitly disabled. Any non-unanimous result
    is rejected and queued for revalidation.
    """

    worker_results = tuple(
        await asyncio.gather(*(worker.validate(problems) for worker in WORKERS))
    )

    all_pass = all(
        result.verdict is WorkerVerdict.PASS for result in worker_results
    )

    return TrustChainResult(
        status=(
            TrustChainStatus.APPROVED
            if all_pass
            else TrustChainStatus.REJECTED_REVALIDATION_PENDING
        ),
        unanimous=all_pass,
        majority_rule_used=False,
        worker_results=worker_results,
    )
