"""Owner-requested, opt-in routes verified against OpenRouter on 2026-10-17.

These are deployment identifiers, not a claim of unrestricted downstream use
or successful account inference. Missing catalogue names are never aliased.
"""

import os

PRESET_FLAG = "EDURECOVER_VERIFIED_MODEL_ROUTES_ENABLED"
OPENROUTER_MODELS = {
    "Qwen3.6-27B": "qwen/qwen3.6-27b",
    "Qwen3.6-35B-A3B": "qwen/qwen3.6-35b-a3b",
    "Qwen3.5-397B-A17B": "qwen/qwen3.5-397b-a17b",
    "Qwen3.8-27B": "qwen/qwen3.8-27b",
    "gpt-oss-20b": "openai/gpt-oss-20b",
    "gpt-oss-120b": "openai/gpt-oss-120b",
    "gemma-4-26B-A4B-it": "google/gemma-4-26b-a4b-it",
    "gemma-4-31B-it": "google/gemma-4-31b-it",
    "GLM-5.1": "z-ai/glm-5.1",
    "GLM-5.2": "z-ai/glm-5.2",
    "GLM-5.3-Flash": "z-ai/glm-5.3-flash",
    "Mistral-Small-3.2-24B-Instruct-2506": "mistralai/mistral-small-3.2-24b-instruct",
    "Mistral-Small-4-119B-2603": "mistralai/mistral-small-2603",
    "phi-4": "microsoft/phi-4",
    "NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16": "nvidia/nemotron-3.5-lightning",
    "Qwen3-32B": "qwen/qwen3-32b",
    "Amazon-Nova-2-Lite": "amazon/nova-2-lite-v1",
    "Amazon-Nova-Pro": "amazon/nova-pro-v1",
    "Claude-Haiku-5.5": "anthropic/claude-haiku-5.5",
    "Gemini-2.5-Flash-Lite": "google/gemini-2.5-flash-lite",
}
PRESET_FAMILIES = frozenset({
    "qwen", "gpt-oss", "gemma", "glm", "mistral", "phi", "nemotron",
    "nova", "anthropic", "gemini",
})


def verified_routes_enabled() -> bool:
    value = os.getenv(PRESET_FLAG, "false").strip().lower()
    if value not in {"true", "false"}:
        raise ValueError("Invalid verified model route flag.")
    return value == "true"
