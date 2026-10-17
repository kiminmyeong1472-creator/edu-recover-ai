"""Separate, fixed Chutes inference routes, without an OpenRouter fallback.

Identifiers were matched to Chutes' public model catalogue on 2026-10-17.
Only the documented exact selection is admitted here. A missing Chutes key
must never be replaced with an OpenRouter or another provider's credential.
This module records connection identity, not a legal-use or ZDR guarantee.
"""

from __future__ import annotations


CHUTES_CHAT_ENDPOINT = "https://llm.chutes.ai/v1/chat/completions"
CHUTES_API_KEY_ENV = "CHUTES_API_KEY"
CHUTES_ROUTE_LABEL = "Chutes 직접 API · OpenRouter 미사용"
CHUTES_MODEL_IDS = {
    "DeepSeek-V3.2": "deepseek-ai/DeepSeek-V3.2-TEE",
}


def fixed_bindings() -> dict[str, dict[str, str]]:
    """Build new private bindings; callers cannot supply endpoints or aliases."""
    return {
        key: {
            "transport": "openai_compatible",
            "endpoint_url": CHUTES_CHAT_ENDPOINT,
            "model_id": model_id,
            "api_key_env": CHUTES_API_KEY_ENV,
            "request_format": "chat",
        }
        for key, model_id in CHUTES_MODEL_IDS.items()
    }
