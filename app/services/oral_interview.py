"""LLM evaluation service for multi-turn oral-interview practice."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings
from app.services.auto_generator import GenerationFailed, GeneratorNotConfigured, provider_failure
from app.services.control_plane import assert_provider_allowed
from app.services.interview_prompts import SUBJECT_PROMPTS, SUBJECT_RUBRICS
from app.services.privacy_guard import assert_provider_zdr


_llm_slots = asyncio.Semaphore(settings.interview_llm_concurrency)


def _validate_result(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise GenerationFailed("Interview evaluator returned non-JSON output.") from exc
    if not isinstance(payload, dict):
        raise GenerationFailed("Interview evaluator returned an invalid object.")
    level = payload.get("diagnostic_level")
    feedback = payload.get("feedback_message")
    fallback = payload.get("fallback_hint")
    if type(level) is not int or not 0 <= level <= 3:
        raise GenerationFailed("Interview evaluator returned an invalid diagnostic level.")
    if not isinstance(feedback, str) or not feedback.strip():
        raise GenerationFailed("Interview evaluator returned empty feedback.")
    if not isinstance(fallback, str) or not fallback.strip():
        raise GenerationFailed("Interview evaluator returned an empty follow-up question.")
    return {
        "diagnostic_level": level,
        "feedback_message": feedback.strip(),
        "fallback_hint": fallback.strip(),
    }


async def evaluate_oral_interview(
    *,
    current_step: int,
    answer_reference: str,
    context: list[dict[str, str]],
    student_answer: str,
) -> dict[str, Any]:
    assert_provider_allowed("openai", "generate")
    assert_provider_zdr("openai")
    if settings.openai_api_key is None:
        raise GeneratorNotConfigured("OPENAI_API_KEY is not configured in the server secret store.")

    system_prompt = (
        SUBJECT_PROMPTS["서울대_구술면접"]
        + "\n\n[평가 루브릭]\n"
        + SUBJECT_RUBRICS["서울대_구술면접"]
        + f"\n\n[현재 단계] Step {current_step}"
        + f"\n[교사용 평가 참고자료] {answer_reference}"
        + "\n학생의 과거 발언과 현재 답변을 대조하되, 참고자료의 문장을 학생에게 정답처럼 노출하지 않는다."
    )
    messages = [
        *context,
        {"role": "user", "content": f"[Step {current_step} 답안 제출] {student_answer}"},
    ]
    client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
    try:
        async with _llm_slots:
            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[{"role": "system", "content": system_prompt}, *messages],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
        return _validate_result(response.choices[0].message.content or "")
    except (GeneratorNotConfigured, GenerationFailed):
        raise
    except Exception as exc:
        raise provider_failure("openai", exc) from exc
    finally:
        await client.close()
