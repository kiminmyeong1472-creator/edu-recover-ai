"""Opt-in registration of one owner-operated Nemotron deployment.

This module never provisions a GPU, verifies server ownership, reads a token,
or makes a request. The owner confirms the operating arrangement separately;
a hostname alone cannot establish ownership, retention, or training rights.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.services.open_model_http import OpenModelHTTPError, validate_endpoint_url


MODEL_KEY = "NVIDIA-Nemotron-3-Nano-4B-BF16"
MODEL_ID = "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16"
ENABLED_ENV = "EDURECOVER_NEMOTRON_SELF_HOSTED_ENABLED"
CONFIRMED_ENV = "EDURECOVER_NEMOTRON_SELF_HOSTED_CONFIRMED"
ENDPOINT_ENV = "EDURECOVER_NEMOTRON_NANO_ENDPOINT_URL"
API_KEY_ENV = "EDURECOVER_NEMOTRON_API_KEY"
ROUTE_LABEL = "소유자가 직접 운영으로 등록한 서버 · ZDR 미확인"

# Reject known shared inference services rather than relabelling their API
# contract as self-hosting. This list cannot prove ownership of other hosts.
_SHARED_INFERENCE_DOMAINS = (
    "openrouter.ai", "chutes.ai", "nvidia.com", "deepseek.com",
    "openai.com", "anthropic.com", "together.ai", "together.xyz",
    "deepinfra.com", "fireworks.ai", "parasail.io", "groq.com",
    "huggingface.co", "api.mistral.ai", "api.cerebras.ai", "api.sambanova.ai",
    "generativelanguage.googleapis.com", "aiplatform.googleapis.com",
)


@dataclass(frozen=True)
class Registration:
    """Only a private binding and stable public status; no token is retained."""

    state: str
    setup_note: str
    binding: dict[str, str] | None = None
    active: bool = True


def registration() -> Registration:
    """Validate optional registration without affecting other model routes."""
    enabled = os.getenv(ENABLED_ENV, "false").strip().lower()
    if enabled == "false":
        # Ignore inactive draft settings, preserving previous route behaviour.
        return Registration(
            "not_configured", "NVIDIA Nano 4B를 직접 실행하는 서버 연결이 필요합니다.",
            active=False,
        )
    if enabled != "true":
        return Registration("configuration_invalid", "NVIDIA 직접 운영 활성화 설정을 확인해야 합니다.")

    confirmed = os.getenv(CONFIRMED_ENV, "false").strip().lower()
    if confirmed not in {"true", "false"}:
        return Registration("configuration_invalid", "NVIDIA 직접 운영 확인 설정을 확인해야 합니다.")
    if confirmed != "true":
        return Registration(
            "self_hosting_confirmation_required",
            "소유자가 직접 운영하는 서버인지 확인한 뒤 직접 운영 등록이 필요합니다. ZDR은 확인하지 않았습니다.",
        )

    endpoint = os.getenv(ENDPOINT_ENV, "")
    if not endpoint:
        return Registration("not_configured", "NVIDIA Nano 4B 직접 운영 서버의 HTTPS 주소를 등록해야 합니다.")
    try:
        endpoint = validate_endpoint_url(endpoint)
        parts = urlsplit(endpoint)
        host = parts.hostname or ""
        if parts.path != "/v1/chat/completions" or any(
            host == domain or host.endswith("." + domain)
            for domain in _SHARED_INFERENCE_DOMAINS
        ):
            raise ValueError("Not a supported owner-operated endpoint.")
    except (ValueError, OpenModelHTTPError):
        return Registration(
            "configuration_invalid",
            "직접 운영 서버 주소는 HTTPS 443의 /v1/chat/completions여야 합니다. 공용 모델 API는 등록할 수 없습니다.",
        )
    return Registration(
        "configured_not_verified",
        "소유자가 직접 운영으로 등록한 서버입니다. 실제 모델 응답과 입력·응답 보관 여부는 아직 확인하지 않았습니다.",
        {
            "transport": "openai_compatible", "endpoint_url": endpoint,
            "model_id": MODEL_ID, "api_key_env": API_KEY_ENV, "request_format": "chat",
        },
    )
