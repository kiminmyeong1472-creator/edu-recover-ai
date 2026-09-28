"""Single-provider cross verification for EduRecover-generated items."""

from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic
from google import genai
from openai import AsyncOpenAI

from app.core.config import settings
from app.services.access_notice import AI_ACCESS_NOTICE
from app.services.context_overlay import build_verification_context
from app.services.privacy_guard import assert_provider_zdr
from app.services.auto_generator import (
    GenerationFailed,
    GeneratorNotConfigured,
    GenerationProvider,
    provider_failure,
    _log_gemini_error,
    gemini_generation_config,
)


VERIFICATION_INSTRUCTIONS = AI_ACCESS_NOTICE + """
You are an independent EduRecover AI assessment verifier.
Review the supplied generated assessment item(s) against the supplied passage/evidence.

Check conservatively:
- answer and explanation consistency,
- whether the answer is supported by the supplied passage/evidence,
- ambiguity or multiple-answer risk,
- unsupported or hallucinated claims,
- whether distractors are clearly distinguishable from the correct answer,
- whether the stated difficulty/question type is plausible.

Do not rewrite the item. Return only valid JSON:
{"verdict":"PASS","reasons":[]}
or
{"verdict":"FAIL","reasons":["brief reason 1","brief reason 2"]}
"""


def _serialize(problems: list[dict[str, Any]], provider: str = "") -> str:
    # Verification needs the submitted learning content, not browser state,
    # tokens, execution receipts or arbitrary attached internal metadata.
    fields = {"problem_id", "question_type", "difficulty", "question", "passage",
              "choices", "answer", "explanation", "evidence"}
    problems = [{key: value for key, value in problem.items() if key in fields}
                for problem in problems]
    return json.dumps(
        {
            "problem_count": len(problems),
            "problems": problems,
            "edurecover_verification_context": build_verification_context(problems, provider=provider),
        },
        ensure_ascii=False,
        separators=(",", ":"),
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


def _parse_result(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(_clean_json_text(text))
    except (json.JSONDecodeError, TypeError) as exc:
        raise GenerationFailed("Verifier returned non-JSON output.") from exc

    if not isinstance(payload, dict):
        raise GenerationFailed("Verifier returned an invalid JSON object.")
    verdict = str(payload.get("verdict", "")).strip().upper()
    reasons = payload.get("reasons", [])
    if verdict not in {"PASS", "FAIL"}:
        raise GenerationFailed("Verifier returned an invalid verdict.")
    if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons):
        raise GenerationFailed("Verifier returned an invalid reasons field.")
    reasons = [reason.strip() for reason in reasons if reason.strip()]
    if verdict == "PASS" and reasons:
        raise GenerationFailed("Verifier returned PASS with unresolved concerns.")
    if verdict == "FAIL" and not reasons:
        reasons = ["Verifier rejected the batch without a reason."]

    return {
        "verdict": verdict,
        "reasons": [str(reason).strip() for reason in reasons if str(reason).strip()],
    }


async def _verify_payload(
    provider: str,
    problems: list[dict[str, Any]],
    payload: str,
) -> dict[str, Any]:
    if not problems:
        raise GenerationFailed("At least one generated problem is required.")

    normalized = provider.strip().lower()

    if normalized == GenerationProvider.OPENAI.value:
        assert_provider_zdr("openai")
        if settings.openai_api_key is None:
            raise GeneratorNotConfigured("OPENAI_API_KEY is not configured.")
        client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        try:
            response = await client.responses.create(
                model=settings.openai_model,
                instructions=VERIFICATION_INSTRUCTIONS,
                input=payload,
                store=False,
            )
            result = _parse_result(response.output_text)
            return {
                "provider": normalized,
                **result,
            }
        except GenerationFailed:
            raise
        except Exception as exc:
            raise provider_failure("openai", exc) from exc
        finally:
            await client.close()

    if normalized == GenerationProvider.GEMINI.value:
        assert_provider_zdr("gemini")
        if settings.gemini_api_key is None:
            raise GeneratorNotConfigured("GEMINI_API_KEY is not configured.")
        client = None
        try:
            client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
            response = await client.aio.models.generate_content(
                model=settings.gemini_model,
                contents=payload,
                config=gemini_generation_config(VERIFICATION_INSTRUCTIONS),
            )
            result = _parse_result(response.text)
            return {
                "provider": normalized,
                **result,
            }
        except GeneratorNotConfigured:
            raise
        except GenerationFailed:
            raise
        except Exception as exc:
            _log_gemini_error("verification", exc)
            raise provider_failure("gemini", exc) from exc
        finally:
            if client is not None:
                await client.aio.aclose()
                client.close()

    if normalized == GenerationProvider.CLAUDE.value:
        assert_provider_zdr("claude")
        if settings.anthropic_api_key is None:
            raise GeneratorNotConfigured("ANTHROPIC_API_KEY is not configured.")
        client = AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
        )
        try:
            message = await client.messages.create(
                model=settings.anthropic_model,
                max_tokens=1200,
                system=VERIFICATION_INSTRUCTIONS,
                messages=[{"role": "user", "content": payload}],
            )
            text = "".join(
                block.text
                for block in message.content
                if getattr(block, "type", None) == "text"
            )
            result = _parse_result(text)
            return {
                "provider": normalized,
                **result,
            }
        except GeneratorNotConfigured:
            raise
        except GenerationFailed:
            raise
        except Exception as exc:
            raise provider_failure("claude", exc) from exc
        finally:
            await client.close()

    raise GenerationFailed(f"Unsupported verification provider: {provider}")


async def verify_with_provider(provider: str, problems: list[dict[str, Any]]) -> dict[str, Any]:
    from app.services.secret_vault import SecretVault
    assert_provider_zdr(provider.strip().lower())
    payload = _serialize(problems, provider=provider.strip().lower())
    selection = json.loads(payload)["edurecover_verification_context"]["selection"]
    if selection["selection_state"] == "NO_MATCH":
        raise GenerationFailed("검증 작업에 허용된 영업비밀 구간이 없습니다.", code="AUTHORIZED_ASSET_NOT_FOUND", http_status=409)
    try:
        result = await _verify_payload(provider, problems, payload)
    except BaseException:
        SecretVault().mark(selection["request_id"], "FAILED_OR_UNCERTAIN")
        raise
    SecretVault().mark(selection["request_id"], "COMPLETED")
    return result
