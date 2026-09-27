"""AI automatic question generation for the teacher platform.

OpenAI, Gemini, and Claude/Anthropic are selectable, provider-isolated channels.
The caller must explicitly select a provider for each request.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from anthropic import AsyncAnthropic
from google import genai
from google.genai import types
from openai import AsyncOpenAI

from app.core.config import settings
from app.services.access_notice import AI_ACCESS_NOTICE
from app.services.authoring_assets import build_authoring_context
from app.services.privacy_guard import assert_provider_zdr


logger = logging.getLogger(__name__)


class GenerationProvider(StrEnum):
    OPENAI = "openai"
    GEMINI = "gemini"
    CLAUDE = "claude"


class GeneratorNotConfigured(RuntimeError):
    """Raised when the selected provider has no server-side API secret."""


class GenerationFailed(RuntimeError):
    """Raised when the provider response cannot be safely used."""


def _gemini_error_metadata(exc: Exception) -> tuple[object, object]:
    """Return non-secret upstream status fields for diagnostics."""
    return getattr(exc, "code", None), getattr(exc, "status", None)


def _log_gemini_error(action: str, exc: Exception) -> None:
    """Log enough upstream detail to diagnose failures without logging the key."""
    code, status = _gemini_error_metadata(exc)
    message = str(getattr(exc, "message", "") or "").replace("\n", " ")[:500]
    secret = (
        settings.gemini_api_key.get_secret_value()
        if settings.gemini_api_key is not None
        else ""
    )
    if secret:
        message = message.replace(secret, "[REDACTED_API_KEY]")
    logger.error(
        "gemini_%s_failed error_type=%s code=%s status=%s message=%s",
        action,
        type(exc).__name__,
        code,
        status,
        message,
    )


def gemini_generation_config(system_instruction: str) -> types.GenerateContentConfig:
    """Return the API equivalent of Gemini Apps Pro extended thinking."""
    normalized = settings.gemini_thinking_level.strip().upper()
    allowed_levels = {
        "LOW": types.ThinkingLevel.LOW,
        "MEDIUM": types.ThinkingLevel.MEDIUM,
        "HIGH": types.ThinkingLevel.HIGH,
    }
    thinking_level = allowed_levels.get(normalized)
    if thinking_level is None:
        raise GenerationFailed(
            "GEMINI_THINKING_LEVEL must be LOW, MEDIUM, or HIGH for Gemini Pro."
        )
    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        thinking_config=types.ThinkingConfig(thinking_level=thinking_level),
    )


@dataclass(frozen=True)
class GenerationResult:
    provider: str
    model: str
    items: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "count": len(self.items),
            "items": list(self.items),
        }


SYSTEM_INSTRUCTIONS = AI_ACCESS_NOTICE + """
You are an EduRecover AI authoring agent.
Create school assessment items using the supplied source material, teacher settings,
and the private EduRecover authoring materials/instructions included in the request.
Do not request or use platform chat history or unrelated customer data.

Rules:
- Do not invent external facts that are not supported by the supplied source.
- Preserve enough source context for a teacher to judge the item fairly.
- Produce one clearly defensible answer for each objective item.
- Explanations must cite the reasoning/evidence from the supplied source.
- Distractors should be plausible but demonstrably wrong from the source.
- Follow the teacher's requested grade, exam type, question types, difficulty,
  and scope as closely as the source permits.
- If the source does not support a requested item type, use the closest
  supported type and say so in the explanation.
- Return JSON only. No markdown fences.

Return exactly this shape:
{
  "items": [
    {
      "question_type": "type",
      "difficulty": "easy|medium|hard",
      "question": "question stem",
      "passage": "source passage/context used for the item",
      "choices": ["choice 1", "choice 2", "choice 3", "choice 4", "choice 5"],
      "answer": "correct answer or choice label",
      "explanation": "teacher-facing detailed explanation",
      "evidence": "specific source evidence supporting the answer"
    }
  ]
}
"""


def _build_request_text(
    *,
    source_text: str,
    count: int,
    subject: str,
    school_level: str,
    grade: str,
    exam_type: str,
    question_types: list[str],
    difficulty: str,
    scope_memo: str,
    extra_instructions: str,
) -> str:
    return json.dumps(
        {
            "task": "Generate assessment items for a school teacher.",
            "requested_count": count,
            "subject": subject,
            "school_level": school_level,
            "grade": grade,
            "exam_type": exam_type,
            "question_types": question_types,
            "difficulty": difficulty,
            "scope_memo": scope_memo,
            "extra_instructions": extra_instructions,
            "source_text": source_text,
            "edurecover_authoring_context": build_authoring_context(
                subject=subject,
                grade=grade,
                source_text=source_text,
                question_types=question_types,
            ),
        },
        ensure_ascii=False,
    )


def _clean_json_text(text: str) -> str:
    cleaned = (text or "").strip()
    fence = chr(96) * 3
    if cleaned.startswith(fence):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith(fence):
            lines = lines[1:]
        if lines and lines[-1].strip() == fence:
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _parse_and_validate_items(text: str, expected_count: int) -> tuple[dict[str, Any], ...]:
    try:
        payload = json.loads(_clean_json_text(text))
    except (json.JSONDecodeError, TypeError) as exc:
        raise GenerationFailed("AI provider returned non-JSON output.") from exc

    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise GenerationFailed("AI provider response is missing an items array.")
    if len(items) != expected_count:
        raise GenerationFailed(
            f"AI provider returned {len(items)} items; {expected_count} were requested."
        )

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise GenerationFailed(f"Generated item {index} is not an object.")

        for required in ("question", "answer", "explanation"):
            value = item.get(required)
            if not isinstance(value, str) or not value.strip():
                raise GenerationFailed(
                    f"Generated item {index} is missing required field: {required}."
                )

        choices = item.get("choices", [])
        if choices is None:
            choices = []
        if not isinstance(choices, list):
            raise GenerationFailed(f"Generated item {index} has invalid choices.")

        normalized.append(
            {
                "problem_id": index,
                "question_type": str(item.get("question_type", "")).strip(),
                "difficulty": str(item.get("difficulty", "")).strip(),
                "question": item["question"].strip(),
                "passage": str(item.get("passage", "")).strip(),
                "choices": [str(choice).strip() for choice in choices if str(choice).strip()],
                "answer": item["answer"].strip(),
                "explanation": item["explanation"].strip(),
                "evidence": str(item.get("evidence", "")).strip(),
            }
        )

    return tuple(normalized)


async def _generate_with_openai(prompt: str, count: int) -> GenerationResult:
    assert_provider_zdr("openai")
    if settings.openai_api_key is None:
        raise GeneratorNotConfigured(
            "OPENAI_API_KEY is not configured in the server secret store."
        )

    client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
    try:
        response = await client.responses.create(
            model=settings.openai_model,
            instructions=SYSTEM_INSTRUCTIONS,
            input=prompt,
            store=False,
        )
        items = _parse_and_validate_items(response.output_text, count)
        return GenerationResult(
            provider=GenerationProvider.OPENAI.value,
            model=settings.openai_model,
            items=items,
        )
    except GeneratorNotConfigured:
        raise
    except GenerationFailed:
        raise
    except Exception as exc:
        raise GenerationFailed(
            f"OpenAI generation failed ({type(exc).__name__})."
        ) from exc
    finally:
        await client.close()


async def _generate_with_gemini(prompt: str, count: int) -> GenerationResult:
    assert_provider_zdr("gemini")
    if settings.gemini_api_key is None:
        raise GeneratorNotConfigured(
            "GEMINI_API_KEY is not configured in the server secret store."
        )

    try:
        client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        response = await client.aio.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=gemini_generation_config(SYSTEM_INSTRUCTIONS),
        )
        items = _parse_and_validate_items(response.text, count)
        return GenerationResult(
            provider=GenerationProvider.GEMINI.value,
            model=settings.gemini_model,
            items=items,
        )
    except GeneratorNotConfigured:
        raise
    except GenerationFailed:
        raise
    except Exception as exc:
        _log_gemini_error("generation", exc)
        code, status = _gemini_error_metadata(exc)
        raise GenerationFailed(
            f"Gemini generation failed ({type(exc).__name__}; "
            f"code={code}; status={status})."
        ) from exc


async def _generate_with_anthropic(prompt: str, count: int) -> GenerationResult:
    assert_provider_zdr("claude")
    if settings.anthropic_api_key is None:
        raise GeneratorNotConfigured(
            "ANTHROPIC_API_KEY is not configured in the server secret store."
        )

    client = AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
    )
    try:
        message = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=8000,
            system=SYSTEM_INSTRUCTIONS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text
            for block in message.content
            if getattr(block, "type", None) == "text"
        )
        items = _parse_and_validate_items(text, count)
        return GenerationResult(
            provider=GenerationProvider.CLAUDE.value,
            model=settings.anthropic_model,
            items=items,
        )
    except GeneratorNotConfigured:
        raise
    except GenerationFailed:
        raise
    except Exception as exc:
        raise GenerationFailed(
            f"Claude generation failed ({type(exc).__name__})."
        ) from exc
    finally:
        await client.close()


async def generate_questions(
    *,
    provider: str,
    source_text: str,
    count: int,
    subject: str,
    school_level: str,
    grade: str,
    exam_type: str,
    question_types: list[str],
    difficulty: str,
    scope_memo: str,
    extra_instructions: str,
) -> GenerationResult:
    """Generate teacher-editable draft assessment items using one provider."""

    source_text = source_text.strip()
    if not source_text:
        raise GenerationFailed("Source text is required for automatic generation.")
    if not 1 <= count <= 50:
        raise GenerationFailed("Question count must be between 1 and 50.")

    prompt = _build_request_text(
        source_text=source_text,
        count=count,
        subject=subject,
        school_level=school_level,
        grade=grade,
        exam_type=exam_type,
        question_types=question_types,
        difficulty=difficulty,
        scope_memo=scope_memo,
        extra_instructions=extra_instructions,
    )

    normalized_provider = provider.strip().lower()
    if normalized_provider == GenerationProvider.OPENAI.value:
        return await _generate_with_openai(prompt, count)
    if normalized_provider == GenerationProvider.GEMINI.value:
        return await _generate_with_gemini(prompt, count)
    if normalized_provider == GenerationProvider.CLAUDE.value:
        return await _generate_with_anthropic(prompt, count)

    raise GenerationFailed(f"Unsupported generation provider: {provider}")
