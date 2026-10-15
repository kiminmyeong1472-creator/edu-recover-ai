from unittest.mock import AsyncMock, patch

import pytest

from scripts.check_aws_model_connections import check_models, main
from app.services.open_model_transport import OpenModelError


def binding_env():
    return {
        "EDURECOVER_OPEN_MODELS_ENABLED": "true",
        "EDURECOVER_ALLOWED_PROVIDERS": "nova",
        "EDURECOVER_OPEN_NOVA_MODEL_KEY": "Amazon-Nova-2-Lite",
        "EDURECOVER_OPEN_NOVA_MODEL_ID": "global.amazon.nova-2-lite-v1:0",
        "EDURECOVER_OPEN_NOVA_REGION": "ap-northeast-2",
    }


def test_configuration_check_does_not_invoke_or_expose_binding():
    import asyncio
    with patch.dict("os.environ", binding_env(), clear=True), patch(
        "scripts.check_aws_model_connections.call_model", new_callable=AsyncMock
    ) as invoke:
        result = asyncio.run(check_models(["Amazon-Nova-2-Lite"]))
    invoke.assert_not_awaited()
    row = result["models"][0]
    assert row["configured"] and row["enabled"] and row["permitted"]
    assert row["live_call_attempted"] is False
    assert "global.amazon" not in str(result)


def test_live_check_requires_explicit_registered_models():
    import asyncio
    with patch.dict("os.environ", {}, clear=True):
        for keys in ([], ["unregistered"], ["Amazon-Nova-Pro", "Amazon-Nova-Pro"]):
            with pytest.raises(ValueError):
                asyncio.run(check_models(keys, invoke=True))


def test_live_check_uses_synthetic_input_and_hides_output():
    import asyncio
    with patch.dict("os.environ", binding_env(), clear=True), patch(
        "scripts.check_aws_model_connections.call_model", new_callable=AsyncMock, return_value="2"
    ) as invoke:
        result = asyncio.run(check_models(["Amazon-Nova-2-Lite"], invoke=True))
    assert result["models"][0]["answer_matches_expected"] is True
    assert invoke.await_args.args[0] == "Amazon-Nova-2-Lite"
    assert invoke.await_args.kwargs["max_tokens"] == 32
    assert "answer" not in result["models"][0]


def test_error_codes_are_reported_without_raw_error(capsys):
    with patch.dict("os.environ", binding_env(), clear=True), patch(
        "scripts.check_aws_model_connections.call_model", new_callable=AsyncMock,
        side_effect=OpenModelError("private-sdk-value", "OPEN_MODEL_CREDENTIALS_UNAVAILABLE", 503),
    ):
        assert main(["--invoke", "--model-key", "Amazon-Nova-2-Lite"]) == 1
    output = capsys.readouterr().out
    assert "OPEN_MODEL_CREDENTIALS_UNAVAILABLE" in output
    assert "private-sdk-value" not in output
