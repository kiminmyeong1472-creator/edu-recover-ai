"""Trust Chain cross-validation using real OpenAI and Anthropic APIs.

The protocol is fail-closed and deliberately rejects majority voting.
A 28-item problem set is approved only when both independent providers return
PASS. Any disagreement, malformed response, configuration failure, or provider
error moves the set to revalidation pending.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from app.core.config import settings


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


VALIDATION_INSTRUCTIONS = """You are an independent EduRecover AI Trust Chain validator.
Review exactly 28 English reading-comprehension problem/explanation objects.

Validate conservatively. Return PASS only if the full batch has no material
defect. Check at least:
- answer and explanation consistency,
- whether the explanation is supported by the supplied passage/evidence,
- contradictions or unsupported claims,
- hallucinated facts not grounded in the supplied data,
- ambiguity or multiple-answer risk where detectable,
- missing information that prevents reliable verification.

Do not use majority voting and do not assume another validator will catch an
error. If any item cannot be verified, return FAIL.

Return ONLY valid JSON in this exact shape:
{"verdict":"PASS","reasons":[]}
or
{"verdict":"FAIL","reasons":["brief reason 1","brief reason 2"]}
"""


def _serialize_problems(problems: list[dict[str, Any]]) -> str:
    return json.dumps(
        {"problem_count": len(problems), "problems": problems},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _clean_json_text(text: str) -> str:
    cleaned = text.strip()
    fence = chr(96) * 3
    if cleaned.startswith(fence):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith(fence):
            lines = lines[1:]
        if lines and lines[-1].strip() == fence:
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _parse_worker_response(worker: str, text: str) -> WorkerResult:
    try:
        payload = json.loads(_clean_json_text(text))
    except (json.JSONDecodeError, TypeError):
        return WorkerResult(
            worker=worker,
            verdict=WorkerVerdict.FAIL,
            reasons=("Provider returned a non-JSON validation response.",),
        )

    verdict_raw = str(payload.get("verdict", "")).strip().upper()
    reasons_raw = payload.get("reasons", [])

    if verdict_raw not in {WorkerVerdict.PASS.value, WorkerVerdict.FAIL.value}:
        return WorkerResult(
            worker=worker,
            verdict=WorkerVerdict.FAIL,
            reasons=("Provider returned an invalid verdict.",),
        )

    if not isinstance(reasons_raw, list):
        reasons_raw = ["Provider returned an invalid reasons field."]

    reasons = tuple(
        str(reason).strip()
        for reason in reasons_raw
        if str(reason).strip()
    )

    verdict = WorkerVerdict(verdict_raw)
    if verdict is WorkerVerdict.FAIL and not reasons:
        reasons = ("Provider rejected the batch without a reason.",)

    return WorkerResult(
        worker=worker,
        verdict=verdict,
        reasons=reasons,
    )


async def _validate_with_openai(
    problems: list[dict[str, Any]],
) -> WorkerResult:
    worker = "OpenAI-validator"

    if settings.openai_api_key is None:
        return WorkerResult(
            worker=worker,
            verdict=WorkerVerdict.FAIL,
            reasons=("OPENAI_API_KEY is not configured.",),
        )

    client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
    )

    try:
        response = await client.responses.create(
            model=settings.openai_model,
            instructions=VALIDATION_INSTRUCTIONS,
            input=_serialize_problems(problems),
        )
        return _parse_worker_response(worker, response.output_text)
    except Exception as exc:
        return WorkerResult(
            worker=worker,
            verdict=WorkerVerdict.FAIL,
            reasons=(
                f"OpenAI validation request failed ({type(exc).__name__}).",
            ),
        )
    finally:
        await client.close()


async def _validate_with_anthropic(
    problems: list[dict[str, Any]],
) -> WorkerResult:
    worker = "Anthropic-validator"

    if settings.anthropic_api_key is None:
        return WorkerResult(
            worker=worker,
            verdict=WorkerVerdict.FAIL,
            reasons=("ANTHROPIC_API_KEY is not configured.",),
        )

    client = AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
    )

    try:
        message = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1200,
            system=VALIDATION_INSTRUCTIONS,
            messages=[
                {
                    "role": "user",
                    "content": _serialize_problems(problems),
                }
            ],
        )

        text = "".join(
            block.text
            for block in message.content
            if getattr(block, "type", None) == "text"
        )
        return _parse_worker_response(worker, text)
    except Exception as exc:
        return WorkerResult(
            worker=worker,
            verdict=WorkerVerdict.FAIL,
            reasons=(
                f"Anthropic validation request failed ({type(exc).__name__}).",
            ),
        )
    finally:
        await client.close()


async def run_trust_chain(
    problems: list[dict[str, Any]],
) -> TrustChainResult:
    """Run both providers concurrently and approve only unanimous PASS."""

    if len(problems) != EXPECTED_BATCH_SIZE:
        reason = (
            f"Trust Chain requires exactly {EXPECTED_BATCH_SIZE} problems; "
            f"received {len(problems)}."
        )
        worker_results = (
            WorkerResult(
                worker="OpenAI-validator",
                verdict=WorkerVerdict.FAIL,
                reasons=(reason,),
            ),
            WorkerResult(
                worker="Anthropic-validator",
                verdict=WorkerVerdict.FAIL,
                reasons=(reason,),
            ),
        )
    else:
        worker_results = tuple(
            await asyncio.gather(
                _validate_with_openai(problems),
                _validate_with_anthropic(problems),
            )
        )

    all_pass = (
        len(worker_results) == 2
        and all(
            result.verdict is WorkerVerdict.PASS
            for result in worker_results
        )
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
