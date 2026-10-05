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
from google.oauth2 import service_account
from openai import AsyncOpenAI

from app.core.config import settings
from app.services.access_notice import AI_ACCESS_NOTICE
from app.services.context_overlay import build_authoring_context
from app.services.privacy_guard import assert_provider_zdr


logger = logging.getLogger(__name__)


class GenerationProvider(StrEnum):
    OPENAI = "openai"
    GEMINI = "gemini"
    CLAUDE = "claude"


class GeneratorNotConfigured(RuntimeError):
    """Raised when the selected provider has no server-side API secret."""


class GenerationFailed(RuntimeError):
    """A safe, classified failure; raw provider response bodies are never exposed."""

    def __init__(self, message: str, *, code: str = "INVALID_PROVIDER_OUTPUT", http_status: int = 502):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _gemini_error_metadata(exc: Exception) -> tuple[int | None, str | None]:
    raw_code = getattr(exc, "code", None)
    code = raw_code if type(raw_code) is int and 100 <= raw_code <= 599 else None
    raw_status = getattr(exc, "status", None)
    status = raw_status if isinstance(raw_status, str) and raw_status in {
        "UNAUTHENTICATED", "PERMISSION_DENIED", "RESOURCE_EXHAUSTED",
        "UNAVAILABLE", "INTERNAL", "INVALID_ARGUMENT", "NOT_FOUND",
        "DEADLINE_EXCEEDED",
    } else None
    return code, status


def _log_gemini_error(action: str, exc: Exception) -> None:
    # Provider messages may echo source passages or secrets, so log only
    # bounded status metadata, never the exception message or response body.
    code, status = _gemini_error_metadata(exc)
    logger.error("gemini_%s_failed code=%s status=%s", action, code, status)


def provider_failure(provider: str, exc: Exception) -> GenerationFailed:
    """Classify upstream failures without disclosing its body, headers or key."""
    code = getattr(exc, "status_code", None)
    gemini_code, _ = _gemini_error_metadata(exc)
    if type(code) is not int:
        code = gemini_code
    error_type = type(exc).__name__
    label = {"openai": "OpenAI", "gemini": "Gemini", "claude": "Claude"}.get(provider, "AI")
    if code == 401 or error_type == "AuthenticationError":
        return GenerationFailed(
            f"{label} API 인증이 거부되었습니다. 관리자에게 서버 API 인증 설정 확인을 요청해 주세요. 다른 AI로 자동 전환하지 않았습니다.",
            code="PROVIDER_AUTHENTICATION_FAILED", http_status=502,
        )
    if code == 429 or error_type == "RateLimitError":
        return GenerationFailed(
            f"{label} API 할당량 또는 호출 한도에 도달했습니다. 관리자에게 해당 API 프로젝트의 사용 한도·결제 상태 확인을 요청해 주세요. 앱 구독과 API 이용 한도는 별개입니다.",
            code="PROVIDER_QUOTA_OR_RATE_LIMIT", http_status=429,
        )
    if code == 403 or error_type == "PermissionDeniedError":
        return GenerationFailed(f"{label} API 프로젝트 또는 모델 접근 권한을 확인해 주세요.", code="PROVIDER_PERMISSION_DENIED")
    if code == 404 or error_type == "NotFoundError":
        return GenerationFailed(f"{label}의 설정된 모델 또는 요청 경로를 확인해 주세요.", code="PROVIDER_MODEL_OR_ENDPOINT_UNAVAILABLE")
    if code in {408, 504} or error_type in {"APITimeoutError", "TimeoutError"}:
        return GenerationFailed(f"{label} API 응답 시간이 초과되었습니다. 잠시 후 같은 AI로 다시 시도해 주세요.", code="PROVIDER_TIMEOUT", http_status=504)
    return GenerationFailed(f"{label} API 요청을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.", code="PROVIDER_REQUEST_FAILED")


def _vertex_gemini_client():
    """Create a Google Cloud Vertex AI Gemini client from server-side credentials."""
    if settings.google_credentials_json is None:
        raise GeneratorNotConfigured(
            "GOOGLE_CREDENTIALS_JSON is not configured in the server secret store."
        )
    try:
        info = json.loads(settings.google_credentials_json.get_secret_value())
    except (json.JSONDecodeError, TypeError) as exc:
        raise GeneratorNotConfigured("GOOGLE_CREDENTIALS_JSON is invalid.") from exc
    project = (settings.google_cloud_project or info.get("project_id") or "").strip()
    location = (settings.gemini_vertex_location or "global").strip()
    if not project:
        raise GeneratorNotConfigured("Google Cloud project id is not configured.")
    if not location:
        raise GeneratorNotConfigured("Gemini Vertex AI location is not configured.")
    try:
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
    except Exception as exc:
        raise GeneratorNotConfigured("Google Cloud service account credentials are invalid.") from exc
    return genai.Client(
        vertexai=True,
        project=project,
        location=location,
        credentials=credentials,
    )


def gemini_generation_config(system_instruction: str) -> types.GenerateContentConfig:
    """Configure the API thinking level independently of any consumer subscription."""
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
    provider: str = "",
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
                provider=provider,
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
        raise provider_failure("openai", exc) from exc
    finally:
        await client.close()


async def _generate_with_gemini(prompt: str, count: int) -> GenerationResult:
    assert_provider_zdr("gemini")

    client = None
    try:
        client = _vertex_gemini_client()
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
        raise provider_failure("gemini", exc) from exc
    finally:
        if client is not None:
            await client.aio.aclose()
            client.close()


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
        raise provider_failure("claude", exc) from exc
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

    assert_provider_zdr(provider.strip().lower())
    prompt = _build_request_text(
        provider=provider.strip().lower(),
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

    from app.services.secret_vault import SecretVault
    selection = json.loads(prompt)["edurecover_authoring_context"]["selection"]
    if selection["selection_state"] == "NO_MATCH":
        raise GenerationFailed("이 작업에 허용된 영업비밀 구간이 없습니다. 영업비밀 관리창에서 적용 범위를 확인해 주세요.",
                               code="AUTHORIZED_ASSET_NOT_FOUND", http_status=409)
    normalized_provider = provider.strip().lower()
    try:
        runner = {"openai": _generate_with_openai, "gemini": _generate_with_gemini,
                  "claude": _generate_with_anthropic}.get(normalized_provider)
        if runner is None:
            raise GenerationFailed("Unsupported generation provider.")
        result = await runner(prompt, count)
    except BaseException:
        SecretVault().mark(selection["request_id"], "FAILED_OR_UNCERTAIN")
        raise
    SecretVault().mark(selection["request_id"], "COMPLETED")
    return GenerationResult(provider=result.provider, model=result.model,
        items=tuple(dict(item, subject=subject, grade=grade) for item in result.items))
