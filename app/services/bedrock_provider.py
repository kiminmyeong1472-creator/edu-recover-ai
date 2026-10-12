"""Private Bedrock transport for Claude and OpenAI with explicit model selection.

No model selection defaults, credentials, prompts, responses or raw SDK errors
are logged here. IAM credentials use the SDK chain, including an AWS task/EC2
role. The owner disabled mandatory ZDR checks; retention remains an optional
diagnostic and is never changed here. Direct-provider fallback is never attempted.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from app.core.config import settings
from app.services.privacy_guard import assert_provider_zdr


class BedrockProviderError(RuntimeError):
    """Bounded, public-safe error; its original SDK body is never included."""

    def __init__(self, message: str, *, code: str, http_status: int = 423):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def uses_bedrock(provider: str) -> bool:
    if provider not in {"openai", "claude"}:
        return False
    transport = settings.provider_transport.strip().lower()
    if transport not in {"direct", "bedrock"}:
        raise BedrockProviderError(
            "서버의 AI 호출 경로 설정을 확인해 주세요.",
            code="PROVIDER_TRANSPORT_CONFIGURATION_INVALID", http_status=503,
        )
    return transport == "bedrock"


def _inference_profile(provider: str) -> str:
    profile = {
        "openai": settings.bedrock_openai_inference_profile,
        "claude": settings.bedrock_claude_inference_profile,
    }.get(provider)
    profile = (profile or "").strip()
    # Only an explicitly configured inference profile is allowed. Base model
    # IDs and foundation-model ARNs caused the earlier on-demand rejection.
    is_system_id = bool(re.fullmatch(
        r"(?:global|us|eu|apac|au|jp)\.(?:openai|anthropic)\.[A-Za-z0-9._:-]+", profile,
    ))
    is_profile_arn = bool(re.fullmatch(
        r"arn:(?:aws|aws-us-gov|aws-cn):bedrock:[a-z0-9-]+:\d{12}:"
        r"(?:application-)?inference-profile/[A-Za-z0-9._:-]+", profile,
    ))
    if not (is_system_id or is_profile_arn):
        raise BedrockProviderError(
            "AWS AI 추론 프로필을 서버에 명시적으로 설정해 주세요.",
            code="BEDROCK_INFERENCE_PROFILE_NOT_CONFIGURED", http_status=503,
        )
    expected_vendor = "openai" if provider == "openai" else "anthropic"
    if is_system_id and not profile.split(".", 2)[1] == expected_vendor:
        raise BedrockProviderError(
            "선택한 AI와 서버 추론 프로필의 공급자가 일치하지 않습니다.",
            code="BEDROCK_PROFILE_PROVIDER_MISMATCH", http_status=503,
        )
    return profile


def configured(provider: str) -> bool:
    """Configuration only: no IAM/connection/ZDR success is claimed."""
    try:
        return bool(uses_bedrock(provider) and re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d", (settings.aws_region or "").strip())
                    and _inference_profile(provider))
    except BedrockProviderError:
        return False


def _client(service: str, region: str):
    # Optional until the AWS requirements are installed. Do not make Railway's
    # non-AI functions depend on the AWS SDK being present.
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise BedrockProviderError(
            "AWS 호출 SDK를 설치하거나 업데이트해 주세요.",
            code="BEDROCK_SDK_NOT_AVAILABLE", http_status=503,
        ) from exc
    return boto3.client(
        service, region_name=region,
        config=Config(connect_timeout=5, read_timeout=120,
                      retries={"total_max_attempts": 1, "mode": "standard"}),
    )


def _close(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            # Cleanup must not replace a bounded failure with an SDK body.
            pass


def _check_profile(control: Any, provider: str, profile: str) -> None:
    getter = getattr(control, "get_inference_profile", None)
    if not callable(getter):
        raise BedrockProviderError(
            "현재 AWS SDK가 추론 프로필 조회를 지원하지 않습니다.",
            code="BEDROCK_PROFILE_SDK_UPGRADE_REQUIRED", http_status=503,
        )
    try:
        response = getter(inferenceProfileIdentifier=profile)
    except Exception as exc:
        raise BedrockProviderError(
            "AWS 추론 프로필의 사용 상태를 확인하지 못해 호출을 보류했습니다.",
            code="BEDROCK_PROFILE_CHECK_FAILED",
        ) from exc
    if not isinstance(response, dict) or response.get("status") != "ACTIVE":
        raise BedrockProviderError(
            "AWS 추론 프로필이 사용 가능한 상태로 확인되지 않았습니다.",
            code="BEDROCK_PROFILE_NOT_ACTIVE",
        )
    models = response.get("models")
    vendor = "openai" if provider == "openai" else "anthropic"
    if not isinstance(models, list) or not models or any(
        not isinstance(model, dict) or not isinstance(model.get("modelArn"), str)
        or ":foundation-model/" + vendor + "." not in model["modelArn"]
        for model in models
    ):
        raise BedrockProviderError(
            "추론 프로필의 모델 공급자를 확인하지 못했습니다.",
            code="BEDROCK_PROFILE_PROVIDER_MISMATCH",
        )
    # This transport is currently used by assessment authoring and verification,
    # not student tutoring. An application profile must not hide a Luna target.
    if provider == "openai" and any("luna" in model["modelArn"].lower() for model in models):
        raise BedrockProviderError(
            "튜터 전용 모델은 출제·검증 작업에 사용할 수 없습니다.",
            code="BEDROCK_MODEL_TASK_NOT_ALLOWED",
        )


def _runtime_failure(exc: Exception) -> BedrockProviderError:
    response = getattr(exc, "response", None)
    error = response.get("Error", {}) if isinstance(response, dict) else {}
    name = error.get("Code") if isinstance(error, dict) else None
    if name in {"AccessDeniedException", "UnrecognizedClientException", "InvalidSignatureException"} or type(exc).__name__ in {
        "NoCredentialsError", "PartialCredentialsError",
    }:
        return BedrockProviderError(
            "AWS AI 호출 인증 또는 접근 권한을 확인해 주세요.",
            code="BEDROCK_AUTHENTICATION_OR_PERMISSION_FAILED", http_status=502,
        )
    if name in {"ThrottlingException", "ServiceQuotaExceededException"}:
        return BedrockProviderError(
            "AWS AI 호출 한도에 도달했습니다. 같은 AI로 다시 시도해 주세요.",
            code="PROVIDER_QUOTA_OR_RATE_LIMIT", http_status=429,
        )
    if name in {"ModelTimeoutException"} or type(exc).__name__ in {"ReadTimeoutError", "ConnectTimeoutError"}:
        return BedrockProviderError(
            "AWS AI 응답 시간이 초과되었습니다.", code="PROVIDER_TIMEOUT", http_status=504,
        )
    if name in {"ValidationException", "ResourceNotFoundException"}:
        return BedrockProviderError(
            "AWS AI 모델 접근·추론 프로필을 확인해 주세요.",
            code="BEDROCK_MODEL_OR_POLICY_UNAVAILABLE", http_status=502,
        )
    return BedrockProviderError(
        "AWS AI 요청을 완료하지 못했습니다.", code="BEDROCK_REQUEST_FAILED", http_status=502,
    )


def _converse(provider: str, instructions: str, prompt: str, max_tokens: int) -> tuple[str, str]:
    profile = _inference_profile(provider)
    region = (settings.aws_region or "").strip()
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d", region):
        raise BedrockProviderError(
            "AWS 호출 리전을 서버에 명시적으로 설정해 주세요.",
            code="BEDROCK_REGION_NOT_CONFIGURED", http_status=503,
        )
    control = runtime = None
    try:
        control = _client("bedrock", region)
        _check_profile(control, provider, profile)
        runtime = _client("bedrock-runtime", region)
        response = runtime.converse(
            modelId=profile,
            system=[{"text": instructions}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            # No temperature, topP, stop sequences or thinking override: current
            # adaptive/always-on thinking models reject some of these fields.
            inferenceConfig={"maxTokens": max_tokens},
        )
        blocks = response.get("output", {}).get("message", {}).get("content", [])
        if not isinstance(blocks, list):
            raise BedrockProviderError(
                "AWS AI 응답 형식을 확인하지 못했습니다.",
                code="INVALID_PROVIDER_OUTPUT", http_status=502,
            )
        text = "".join(block["text"] for block in blocks
                       if isinstance(block, dict) and isinstance(block.get("text"), str))
        if not text.strip():
            raise BedrockProviderError(
                "AWS AI가 사용할 수 있는 응답을 반환하지 않았습니다.",
                code="INVALID_PROVIDER_OUTPUT", http_status=502,
            )
        return text, profile
    except BedrockProviderError:
        raise
    except Exception as exc:
        raise _runtime_failure(exc) from exc
    finally:
        if runtime is not None:
            _close(runtime)
        if control is not None:
            _close(control)


async def converse_text(provider: str, instructions: str, prompt: str, *, max_tokens: int) -> tuple[str, str]:
    """Return text and a private profile ID; ZDR enforcement is owner opt-in."""
    if not uses_bedrock(provider):
        raise BedrockProviderError(
            "AWS AI 호출 경로가 선택되지 않았습니다.",
            code="BEDROCK_TRANSPORT_NOT_SELECTED", http_status=503,
        )
    assert_provider_zdr(provider)
    return await asyncio.to_thread(_converse, provider, instructions, prompt, max_tokens)
