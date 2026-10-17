"""Exercise every opt-in route without credentials, network or paid inference."""
import asyncio
import json
import os
from unittest.mock import patch

import pytest

from app.services import open_model_transport as transport
from app.services.control_plane import evaluate_provider
from app.services.open_model_presets import OPENROUTER_MODELS, PRESET_FLAG, PRESET_FAMILIES


@pytest.mark.parametrize("key,remote", OPENROUTER_MODELS.items())
def test_each_verified_route_sends_exact_model_and_requires_zdr(key, remote):
    env = {PRESET_FLAG: "true", "EDURECOVER_OPEN_MODELS_ENABLED": "true",
           "OPENROUTER_API_KEY": "test-only-not-a-real-key"}
    response = {"choices": [{"message": {"role": "assistant", "content": "2"},
                             "finish_reason": "stop"}]}
    with patch.dict(os.environ, env, clear=True), patch.object(
        transport, "post_json", return_value=response,
    ) as post:
        assert asyncio.run(transport.call_model(key, "숫자만 답하십시오.", "1+1", max_tokens=None)) == "2"
        endpoint, payload, _ = post.call_args.args
        assert endpoint == transport.OPENROUTER_CHAT_ENDPOINT
        assert payload["model"] == remote
        assert payload["provider"] == {"zdr": True, "data_collection": "deny"}
        assert "max_tokens" not in payload
        assert post.call_count == 1


def test_presets_off_by_default_and_missing_models_not_aliased():
    with patch.dict(os.environ, {}, clear=True):
        assert transport._bindings() == {}
    with patch.dict(os.environ, {PRESET_FLAG: "true"}, clear=True):
        bindings = transport._bindings()
        assert set(bindings) == set(OPENROUTER_MODELS)
        for key in ("DeepSeek-R1", "DeepSeek-V3.2", "DeepSeek-V4-Flash",
                    "gemma-4-E2B-it", "gemma-4-E4B-it", "gemma-4-12B-it",
                    "Mistral-Large-3-675B-Base-2512",
                    "NVIDIA-Nemotron-3-Nano-4B-BF16",
                    "NVIDIA-Nemotron-3-Super-120B-A12B-BF16"):
            assert key not in bindings


def test_presets_preserve_custom_binding_deepseek_direct_and_luna_azure():
    custom = {"transport": "bedrock", "region": "us-east-1", "model_id": "existing"}
    env = {PRESET_FLAG: "true", "EDURECOVER_DEEPSEEK_DIRECT_ENABLED": "true",
           "EDURECOVER_GPT6_LUNA_AZURE_ENABLED": "true",
           "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": json.dumps({"Qwen3.6-27B": custom})}
    with patch.dict(os.environ, env, clear=True):
        bindings = transport._bindings()
        assert bindings["Qwen3.6-27B"].model_id == "existing"
        assert bindings["DeepSeek-V4.1-Flash"].endpoint_url == transport.DEEPSEEK_CHAT_ENDPOINT
        assert bindings["GPT-6-Luna"].model_id == transport.GPT6_LUNA_MODEL_ID
        assert bindings["GPT-6-Luna"].endpoint_url == transport.OPENROUTER_CHAT_ENDPOINT


def test_explicit_family_convenience_binding_takes_precedence():
    env = {PRESET_FLAG: "true", "EDURECOVER_OPEN_QWEN_MODEL_KEY": "Qwen3.6-27B",
           "EDURECOVER_OPEN_QWEN_MODEL_ID": "existing-owner-model",
           "EDURECOVER_OPEN_QWEN_REGION": "us-east-1"}
    with patch.dict(os.environ, env, clear=True):
        assert transport._bindings()["Qwen3.6-27B"].model_id == "existing-owner-model"


@pytest.mark.parametrize("family", sorted(PRESET_FAMILIES))
def test_owner_opt_in_grants_family_but_preserves_holds_and_blocks(family):
    env = {PRESET_FLAG: "true", "EDURECOVER_ALLOWED_PROVIDERS": "existing-provider"}
    with patch.dict(os.environ, env, clear=True):
        assert evaluate_provider(family, "generate").allowed
        assert evaluate_provider("existing-provider", "generate").allowed
    for name, value, expected in (
        ("EDURECOVER_AI_HOLD", "true", "HOLD"),
        ("EDURECOVER_BLOCKED_PROVIDERS", family, "BLOCKED"),
        ("EDURECOVER_RESTRICTED_PROVIDERS", family, "RESTRICTED_VERIFY_ONLY"),
        ("EDURECOVER_CONTROL_MANIFEST_JSON", json.dumps({"blocked_providers": [family]}), "BLOCKED"),
    ):
        with patch.dict(os.environ, {**env, name: value}, clear=True):
            decision = evaluate_provider(family, "generate")
            assert not decision.allowed
            assert decision.state == expected


def test_invalid_preset_flag_fails_closed():
    with patch.dict(os.environ, {PRESET_FLAG: "typo"}, clear=True):
        with pytest.raises(transport.OpenModelError):
            transport._bindings()
        with pytest.raises(RuntimeError):
            evaluate_provider("anthropic", "generate")
