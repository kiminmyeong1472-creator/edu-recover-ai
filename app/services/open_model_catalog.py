"""Owner-selected names and separately identified AWS integration candidates.

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
MODEL_CATALOG = SOURCE_MODEL_CATALOG + AWS_ADDITIONAL_MODEL_CATALOG
_MODEL_FAMILIES = {entry["model_key"]: entry["family"] for entry in MODEL_CATALOG}


def model_family(model_key: str) -> str:
    """Return a selected model's family; names are exact and case-sensitive."""
    if not isinstance(model_key, str) or model_key not in _MODEL_FAMILIES:
        raise ValueError("등록되지 않은 모델 이름입니다.")
    return _MODEL_FAMILIES[model_key]
