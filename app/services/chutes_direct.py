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
    "Qwen3.6-27B-FP8": "Qwen/Qwen3.6-27B-TEE",
    "Qwen3.5-397B-A17B-FP8": "Qwen/Qwen3.5-397B-A17B-TEE",
    "Qwen3.8-27B-FP8": "Qwen/Qwen3.8-27B-TEE",
    "Qwen3-32B-FP8": "Qwen/Qwen3-32B-TEE",
    "GLM-5.1-FP8": "zai-org/GLM-5.1-TEE",
    "GLM-5.2-NVFP4": "zai-org/GLM-5.2-TEE",
    "Gemma-4-31B-IT-NVFP4": "google/gemma-4-31B-turbo-TEE",
    "DeepSeek-V4-Flash-0731-FP8-DSpark": "deepseek-ai/DeepSeek-V4-Flash-0731-TEE",
}
# These exact endpoint identities advertise image input in the public model
# catalogue checked on 2026-10-17. This does not infer image support from a
# family name, precision, or a compatible-looking model on another provider.
CHUTES_IMAGE_MODEL_IDS = frozenset({
    "Qwen/Qwen3.6-27B-TEE",
    "Qwen/Qwen3.5-397B-A17B-TEE",
    "Qwen/Qwen3.8-27B-TEE",
    "google/gemma-4-31B-turbo-TEE",
})


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
