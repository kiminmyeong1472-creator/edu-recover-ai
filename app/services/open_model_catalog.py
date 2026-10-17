"""Owner-selected names and separately identified cloud service candidates.

This catalogue records a previous selection. It is not a claim that AWS hosts
these names, that a particular account can invoke them, or that a licence has
been rechecked. Deployment identifiers must be configured separately. Nova is
an Amazon model; its addition does not describe it as an open-weight model.
"""

from __future__ import annotations


_SELECTION = (
    ("deepseek", (
        "DeepSeek-V4-Pro", "DeepSeek-V4-Flash", "DeepSeek-R1", "DeepSeek-V4.1-Flash",
    )),
    ("qwen", (
        "Qwen3.6-27B", "Qwen3.6-35B-A3B", "Qwen3.5-397B-A17B", "Qwen3.8-27B",
    )),
    ("gpt-oss", ("gpt-oss-20b", "gpt-oss-120b")),
    ("gemma", (
        "gemma-4-E2B-it", "gemma-4-E4B-it", "gemma-4-12B-it",
        "gemma-4-26B-A4B-it", "gemma-4-31B-it",
    )),
    ("glm", ("GLM-5.1", "GLM-5.2", "GLM-5.3-Flash")),
    ("mistral", (
        "Mistral-Small-3.2-24B-Instruct-2506", "Mistral-Large-3-675B-Base-2512",
        "Mistral-Small-4-119B-2603",
    )),
    ("phi", ("phi-4",)),
    ("nemotron", (
        "NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16",
        "NVIDIA-Nemotron-3-Nano-4B-BF16",
        "NVIDIA-Nemotron-3-Super-120B-A12B-BF16",
    )),
)

SOURCE_MODEL_CATALOG: tuple[dict[str, str], ...] = tuple(
    {"model_key": name, "family": family, "display_name": name}
    for family, names in _SELECTION
    for name in names
)
# These are additional AWS integration candidates, not replacements for or
# claims made by the original 25-model selection document. Runtime IDs and
# source regions still must be chosen and explicitly configured by the owner.
AWS_ADDITIONAL_MODEL_CATALOG: tuple[dict[str, str], ...] = (
    {"model_key": "Qwen3-32B", "family": "qwen", "display_name": "Qwen3-32B"},
    {"model_key": "DeepSeek-V3.2", "family": "deepseek", "display_name": "DeepSeek-V3.2"},
    {"model_key": "Amazon-Nova-2-Lite", "family": "nova", "display_name": "Amazon Nova 2 Lite"},
    {"model_key": "Amazon-Nova-Pro", "family": "nova", "display_name": "Amazon Nova Pro"},
)
# This hosted service is separate from the owner's 25 open-weight selections
# and the historical AWS candidates. The openai family preserves the existing
# owner's provider allow/hold controls; it is not the gpt-oss family.
CLOUD_SERVICE_MODEL_CATALOG: tuple[dict[str, str], ...] = (
    {"model_key": "GPT-6-Luna", "family": "openai", "display_name": "GPT-6 Luna · Azure 경유"},
    {"model_key": "Claude-Haiku-5.5", "family": "anthropic", "display_name": "Claude Haiku 5.5 · ZDR 경로"},
    {"model_key": "Gemini-2.5-Flash-Lite", "family": "gemini", "display_name": "Gemini 2.5 Flash-Lite · ZDR 경로"},
)
MODEL_CATALOG = SOURCE_MODEL_CATALOG + AWS_ADDITIONAL_MODEL_CATALOG + CLOUD_SERVICE_MODEL_CATALOG

# Chutes' dated serving variants are a separate selection, never replacements
# for the owner's original catalogue or ordinary deployment bindings. The
# existing DeepSeek-V3.2 route keeps its historical key; every other key states
# the distinct version/precision explicitly. Metadata describes identity only,
# not licence, output-training, retention or successful live-call guarantees.
_CHUTES_SELECTION = (
    ("DeepSeek-V3.2", "deepseek", "DeepSeek-V3.2", "V3.2", "FP8",
     "원래 DeepSeek-V3.2와 별도로 연결한 Chutes FP8 제공판입니다."),
    ("Qwen3.6-27B-FP8", "qwen", "Qwen3.6-27B", "3.6-27B", "FP8",
     "원래 Qwen3.6-27B와 별도로 연결한 Chutes FP8 제공판입니다."),
    ("Qwen3.5-397B-A17B-FP8", "qwen", "Qwen3.5-397B-A17B", "3.5-397B-A17B", "FP8",
     "원래 Qwen3.5-397B-A17B와 별도로 연결한 Chutes FP8 제공판입니다."),
    ("Qwen3.8-27B-FP8", "qwen", "Qwen3.8-27B", "3.8-27B", "FP8",
     "원래 Qwen3.8-27B와 별도로 연결한 Chutes FP8 제공판입니다."),
    ("Qwen3-32B-FP8", "qwen", "Qwen3-32B", "3-32B", "FP8",
     "원래 Qwen3-32B와 별도로 연결한 Chutes FP8 제공판입니다."),
    ("GLM-5.1-FP8", "glm", "GLM-5.1", "5.1", "FP8",
     "원래 GLM-5.1과 별도로 연결한 Chutes FP8 제공판입니다."),
    ("GLM-5.2-NVFP4", "glm", "GLM-5.2", "5.2", "NVFP4",
     "원래 GLM-5.2와 별도로 연결한 NVIDIA NVFP4 변환 제공판입니다."),
    ("Gemma-4-31B-IT-NVFP4", "gemma", "gemma-4-31B-it", "4-31B-IT", "NVFP4",
     "원래 gemma-4-31B-it와 별도로 연결한 NVIDIA NVFP4 변환 제공판입니다."),
    ("DeepSeek-V4-Flash-0731-FP8-DSpark", "deepseek", "DeepSeek-V4-Flash",
     "V4-Flash-0731", "FP8",
     "원래 DeepSeek-V4-Flash와 별도로 연결한 0731 날짜판의 AtlasCloud DSpark FP8 제공판입니다."),
)
CHUTES_MODEL_CATALOG: tuple[dict[str, str], ...] = tuple(
    {
        "model_key": name, "family": family, "display_name": name,
        "original_model_key": original, "version": version, "precision": precision,
        "difference": difference, "variant_note": difference,
        "catalog_verified_on": "2026-10-17",
    }
    for name, family, original, version, precision, difference in _CHUTES_SELECTION
)
_MODEL_FAMILIES = {
    entry["model_key"]: entry["family"]
    for entry in MODEL_CATALOG + CHUTES_MODEL_CATALOG
}


def model_family(model_key: str) -> str:
    """Resolve ordinary and explicit Chutes selections without aliasing names."""
    if not isinstance(model_key, str) or model_key not in _MODEL_FAMILIES:
        raise ValueError("등록되지 않은 모델 이름입니다.")
    return _MODEL_FAMILIES[model_key]
