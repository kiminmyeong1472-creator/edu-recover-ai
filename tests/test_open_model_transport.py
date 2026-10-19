"""Offline contract tests. Every AWS and HTTP runtime is replaced by a fake."""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import types
import unittest
from unittest.mock import patch

from app.services import open_model_transport as transport
from app.services.open_model_catalog import (
    AWS_ADDITIONAL_MODEL_CATALOG, CLOUD_SERVICE_MODEL_CATALOG, MODEL_CATALOG,
    SOURCE_MODEL_CATALOG, model_family,
)


MODEL = "Qwen3.6-27B"
BEDROCK = {"transport": "bedrock", "region": "ap-northeast-2", "model_id": "owner-supplied-id"}
SAGEMAKER = {
    "transport": "sagemaker", "region": "ap-northeast-2", "endpoint_name": "owner-endpoint",
}
LUNA_KEY = "GPT-6-Luna"
LUNA_BINDING = {
    "transport": "openai_compatible",
    "endpoint_url": "https://openrouter.ai/api/v1/chat/completions",
    "model_id": "openai/gpt-6-luna",
    "api_key_env": "OPENROUTER_API_KEY",
    "request_format": "chat",
}


def environment(binding=BEDROCK, enabled="true"):
    return {
        "EDURECOVER_OPEN_MODELS_ENABLED": enabled,
        "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": json.dumps({MODEL: binding}),
    }


def bedrock_output(text="검사된 응답", stop="end_turn"):
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}},
            "stopReason": stop}


def sagemaker_output(text="검사된 응답", finish="stop"):
    return {"choices": [{"message": {"role": "assistant", "content": text},
                         "finish_reason": finish}]}


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.closed = False

    def converse(self, **kwargs):
        self.calls.append(("converse", kwargs))
        if self.error:
            raise self.error
        return self.response

    def invoke_endpoint(self, **kwargs):
        self.calls.append(("invoke_endpoint", kwargs))
        if self.error:
            raise self.error
        return self.response

    def close(self):
        self.closed = True


class ShortReadBody(io.BytesIO):
    def read(self, amount=-1):
        return super().read(min(amount, 7))


class CatalogTests(unittest.TestCase):
    def test_source_names_families_and_public_flags_only(self):
        self.assertEqual(25, len(SOURCE_MODEL_CATALOG))
        self.assertEqual(4, len(AWS_ADDITIONAL_MODEL_CATALOG))
        self.assertEqual(3, len(CLOUD_SERVICE_MODEL_CATALOG))
        self.assertEqual(LUNA_KEY, CLOUD_SERVICE_MODEL_CATALOG[0]["model_key"])
        self.assertEqual("openai", CLOUD_SERVICE_MODEL_CATALOG[0]["family"])
        self.assertEqual(32, len(MODEL_CATALOG))
        self.assertEqual(32, len({entry["model_key"] for entry in MODEL_CATALOG}))
        self.assertEqual("openai", model_family(LUNA_KEY))
        self.assertEqual({"deepseek", "qwen", "gpt-oss", "gemma", "glm", "mistral", "phi", "nemotron"},
                         {entry["family"] for entry in SOURCE_MODEL_CATALOG})
        self.assertEqual({"qwen", "deepseek", "nova"},
                         {entry["family"] for entry in AWS_ADDITIONAL_MODEL_CATALOG})
        with patch.dict(os.environ, environment(), clear=True):
            rows = transport.catalog_status()
        self.assertEqual({"model_key", "family", "display_name", "configured", "enabled", "readiness_state"}, set(rows[0]))
        selected = next(row for row in rows if row["model_key"] == MODEL)
        self.assertTrue(selected["configured"])
        self.assertTrue(selected["enabled"])
        self.assertNotIn("owner-supplied-id", json.dumps(rows))
        self.assertNotIn("ap-northeast-2", json.dumps(rows))
        with self.assertRaises(ValueError):
            model_family(MODEL.lower())

    def test_default_disabled_and_unconfigured_does_not_import_sdk(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(transport, "_new_client") as client:
            self.assertTrue(all(not row["enabled"] and not row["configured"]
                                for row in transport.catalog_status()))
            with self.assertRaises(transport.OpenModelError) as error:
                asyncio.run(transport.call_model(MODEL, "instructions", "prompt"))
            self.assertEqual("OPEN_MODEL_DISABLED", error.exception.code)
            client.assert_not_called()

    def test_invalid_configuration_is_fail_closed(self):
        invalid = [
            "[]", "null", "42", "{", '{"Unknown":{}}',
            '{"Qwen3.6-27B":{},"Qwen3.6-27B":{}}',
            json.dumps({MODEL: []}),
            json.dumps({MODEL: {**BEDROCK, "region": None}}),
            json.dumps({MODEL: {**BEDROCK, "region": "https://evil.example"}}),
            json.dumps({MODEL: {**BEDROCK, "transport": "http"}}),
            json.dumps({MODEL: {"transport": "bedrock", "region": "ap-northeast-2"}}),
            json.dumps({MODEL: {**BEDROCK, "model_id": 1}}),
            json.dumps({MODEL: {**BEDROCK, "endpoint_url": "https://evil.example"}}),
            json.dumps({MODEL: {**SAGEMAKER, "endpoint_name": "a/b"}}),
            json.dumps({MODEL: {**SAGEMAKER, "model_id": None}}),
            '{"Qwen3.6-27B":{"transport":"bedrock","transport":"sagemaker"}}',
            '{"Qwen3.6-27B":NaN}',
        ]
        for raw in invalid:
            with self.subTest(raw=raw), patch.dict(os.environ, {
                "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": raw,
                "EDURECOVER_OPEN_MODELS_ENABLED": "false",
            }, clear=True):
                with self.assertRaises(transport.OpenModelError) as error:
                    transport.catalog_status()
                self.assertEqual("OPEN_MODEL_CONFIG_INVALID", error.exception.code)
                self.assertNotIn(raw, str(error.exception))

    def test_feature_flag_is_not_truthy_by_accident(self):
        for value in ("1", "yes", "enable", ""):
            with self.subTest(value=value), patch.dict(os.environ, environment(enabled=value), clear=True):
                with self.assertRaises(transport.OpenModelError):
                    transport.catalog_status()

    def test_luna_preset_is_not_added_by_default_or_when_disabled(self):
        for flag in (None, "false"):
            env = {"OPENROUTER_API_KEY": "private-preset-token"}
            if flag is not None:
                env["EDURECOVER_GPT6_LUNA_AZURE_ENABLED"] = flag
            with self.subTest(flag=flag), patch.dict(os.environ, env, clear=True), \
                    patch.object(transport, "post_json") as post, \
                    patch.object(transport, "_new_client") as sdk:
                self.assertNotIn(LUNA_KEY, transport._bindings())
                row = next(row for row in transport.catalog_status() if row["model_key"] == LUNA_KEY)
                self.assertEqual("not_configured", row["readiness_state"])
                post.assert_not_called()
                sdk.assert_not_called()

    def test_luna_preset_flag_refuses_truthy_or_malformed_values(self):
        for flag in ("", "1", "yes", "enable", "null"):
            with self.subTest(flag=flag), patch.dict(os.environ, {
                "EDURECOVER_GPT6_LUNA_AZURE_ENABLED": flag,
            }, clear=True), patch.object(transport, "post_json") as post, \
                    patch.object(transport, "_new_client") as sdk:
                with self.assertRaises(transport.OpenModelError) as error:
                    transport.catalog_status()
                self.assertEqual("OPEN_MODEL_CONFIG_INVALID", error.exception.code)
                post.assert_not_called()
                sdk.assert_not_called()

    def test_luna_preset_and_json_binding_cannot_compete_even_if_identical(self):
        with patch.dict(os.environ, {
            "EDURECOVER_GPT6_LUNA_AZURE_ENABLED": "true",
            "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": json.dumps({LUNA_KEY: LUNA_BINDING}),
        }, clear=True):
            with self.assertRaises(transport.OpenModelError) as error:
                transport.catalog_status()
        self.assertEqual("OPEN_MODEL_CONFIG_INVALID", error.exception.code)

    def test_exact_explicit_luna_json_binding_is_allowed_without_automatic_preset(self):
        with patch.dict(os.environ, {
            "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": json.dumps({LUNA_KEY: LUNA_BINDING}),
        }, clear=True):
            binding = transport._bindings()[LUNA_KEY]
        self.assertEqual("openai/gpt-6-luna", binding.model_id)
        self.assertEqual("OPENROUTER_API_KEY", binding.api_key_env)
        self.assertEqual("https://openrouter.ai/api/v1/chat/completions", binding.endpoint_url)

    def test_luna_preset_preserves_existing_deepseek_and_hides_connection_details(self):
        deepseek = {**LUNA_BINDING, "model_id": "deepseek/deepseek-v4.1-flash"}
        env = {
            "EDURECOVER_GPT6_LUNA_AZURE_ENABLED": "true",
            "EDURECOVER_OPEN_MODELS_ENABLED": "true",
            "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": json.dumps({"DeepSeek-V4.1-Flash": deepseek}),
            "OPENROUTER_API_KEY": "private-preset-token",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(transport, "post_json") as post, \
                patch.object(transport, "_new_client") as sdk:
            bindings = transport._bindings()
            rows = transport.catalog_status()
        self.assertEqual({"DeepSeek-V4.1-Flash", LUNA_KEY}, set(bindings))
        self.assertEqual("deepseek/deepseek-v4.1-flash", bindings["DeepSeek-V4.1-Flash"].model_id)
        self.assertEqual(2, sum(row["enabled"] for row in rows))
        for private in ("private-preset-token", "openrouter.ai", "OPENROUTER_API_KEY", "openai/gpt-6-luna"):
            self.assertNotIn(private, json.dumps(rows))
        post.assert_not_called()
        sdk.assert_not_called()

    def test_family_bindings_require_explicit_ids_and_regions_without_public_leakage(self):
        env = {"EDURECOVER_OPEN_MODELS_ENABLED": "true"}
        for family, key in (("QWEN", "Qwen3-32B"), ("DEEPSEEK", "DeepSeek-R1"),
                            ("NOVA", "Amazon-Nova-2-Lite")):
            env.update({
                "EDURECOVER_OPEN_" + family + "_MODEL_KEY": key,
                "EDURECOVER_OPEN_" + family + "_MODEL_ID": "private-" + family,
                "EDURECOVER_OPEN_" + family + "_REGION": "ap-northeast-1",
            })
        with patch.dict(os.environ, env, clear=True):
            rows = transport.catalog_status()
            bindings = transport._bindings()
        self.assertEqual({"Qwen3-32B", "DeepSeek-R1", "Amazon-Nova-2-Lite"}, set(bindings))
        self.assertEqual(3, sum(row["enabled"] for row in rows))
        self.assertNotIn("private-", json.dumps(rows))
        self.assertTrue(all(binding.transport == "bedrock" for binding in bindings.values()))

    def test_incomplete_conflicting_wrong_family_or_blank_binding_is_not_guessed(self):
        good = {
            "EDURECOVER_OPEN_QWEN_MODEL_KEY": MODEL,
            "EDURECOVER_OPEN_QWEN_MODEL_ID": "explicit-id",
            "EDURECOVER_OPEN_QWEN_REGION": "ap-northeast-1",
        }
        cases = [
            {"EDURECOVER_OPEN_QWEN_MODEL_KEY": MODEL},
            {**good, "EDURECOVER_OPEN_QWEN_MODEL_ID": ""},
            {**good, "EDURECOVER_OPEN_QWEN_MODEL_KEY": "DeepSeek-R1"},
            {**good, "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": json.dumps({MODEL: BEDROCK})},
            {**good, "EDURECOVER_OPEN_QWEN_REGION": ""},
        ]
        for env in cases:
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                with self.assertRaises(transport.OpenModelError) as error:
                    transport.catalog_status()
                self.assertEqual("OPEN_MODEL_CONFIG_INVALID", error.exception.code)

    def test_sdk_uses_explicit_region_default_credentials_and_no_retries(self):
        calls = []
        client = FakeClient()
        fake_boto = types.ModuleType("boto3")
        fake_boto.client = lambda *args, **kwargs: calls.append((args, kwargs)) or client
        fake_config = types.ModuleType("botocore.config")
        fake_config.Config = lambda **kwargs: kwargs
        fake_config.Config.OPTION_DEFAULTS = {"ignore_configured_endpoint_urls": None}
        with patch.dict(sys.modules, {"boto3": fake_boto, "botocore.config": fake_config}):
            result = transport._new_client(transport._Binding("bedrock", "ap-northeast-2", "private-id"))
        self.assertIs(client, result)
        args, kwargs = calls[0]
        self.assertEqual(("bedrock-runtime",), args)
        self.assertEqual("ap-northeast-2", kwargs["region_name"])
        self.assertEqual({"mode": "standard", "total_max_attempts": 1}, kwargs["config"]["retries"])
        self.assertTrue(kwargs["config"]["ignore_configured_endpoint_urls"])
        self.assertGreater(kwargs["config"]["read_timeout"], 0)
        self.assertEqual({"region_name", "config"}, set(kwargs))

    def test_old_sdk_without_endpoint_protection_is_refused_before_client_creation(self):
        fake_boto = types.ModuleType("boto3")
        calls = []
        fake_boto.client = lambda *args, **kwargs: calls.append((args, kwargs))
        fake_config = types.ModuleType("botocore.config")
        fake_config.Config = lambda **kwargs: kwargs
        fake_config.Config.OPTION_DEFAULTS = {}
        with patch.dict(sys.modules, {"boto3": fake_boto, "botocore.config": fake_config}):
            with self.assertRaises(transport.OpenModelError) as error:
                transport._new_client(transport._Binding("bedrock", "ap-northeast-2", "id"))
        self.assertEqual("OPEN_MODEL_SDK_UNAVAILABLE", error.exception.code)
        self.assertEqual([], calls)
        self.assertIn("업데이트", str(error.exception))


class InvocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_response_whitespace_and_code_indentation_are_preserved(self):
        text = "    print('첫줄 들여쓰기')\n\n마지막\n  "
        self.assertEqual(transport._bedrock_text(bedrock_output(text=text)), text)
        self.assertEqual(transport._openai_text(sagemaker_output(text=text), "chat"), text)
        self.assertEqual(transport._openai_text(
            {"choices": [{"text": text, "finish_reason": "stop"}]}, "completion"), text)
        body = ShortReadBody(json.dumps(sagemaker_output(text=text)).encode())
        self.assertEqual(transport._sagemaker_text({"Body": body}), text)
        self.assertTrue(body.closed)

    async def test_prompt_above_config_limit_is_forwarded_in_full(self):
        prompt = "  첫줄\n" + "가" * (transport.MAX_INPUT_BYTES // 3 + 1) + "\n마지막  "
        self.assertGreater(len(prompt.encode("utf-8")), transport.MAX_INPUT_BYTES)
        self.assertLess(len(prompt.encode("utf-8")), transport.MAX_PROMPT_BYTES)
        client = FakeClient(bedrock_output())
        with patch.dict(os.environ, environment(), clear=True), patch.object(
                transport, "_new_client", return_value=client):
            await transport.call_model(MODEL, "  지시\n  ", prompt)
        self.assertEqual(client.calls[0][1]["messages"][0]["content"][0]["text"], prompt)
        self.assertEqual(client.calls[0][1]["system"][0]["text"], "  지시\n  ")

    async def test_bedrock_sends_only_the_documented_text_fields(self):
        client = FakeClient(bedrock_output())
        with patch.dict(os.environ, environment(), clear=True), patch.object(transport, "_new_client", return_value=client):
            result = await transport.call_model(MODEL, "system instruction", "user prompt", max_tokens=123)
        self.assertEqual("검사된 응답", result)
        self.assertTrue(client.closed)
        self.assertEqual([("converse", {
            "modelId": "owner-supplied-id",
            "system": [{"text": "system instruction"}],
            "messages": [{"role": "user", "content": [{"text": "user prompt"}]}],
            "inferenceConfig": {"maxTokens": 123},
        })], client.calls)

    async def test_sagemaker_contract_and_stream_are_closed(self):
        body = ShortReadBody(json.dumps(sagemaker_output(), ensure_ascii=False).encode())
        client = FakeClient({"Body": body})
        with patch.dict(os.environ, environment({**SAGEMAKER, "model_id": "container-model"}), clear=True), patch.object(transport, "_new_client", return_value=client):
            result = await transport.call_model(MODEL, "system", "prompt")
        self.assertEqual("검사된 응답", result)
        self.assertTrue(body.closed)
        self.assertTrue(client.closed)
        _, request = client.calls[0]
        self.assertEqual("owner-endpoint", request["EndpointName"])
        payload = json.loads(request["Body"])
        self.assertEqual("container-model", payload["model"])
        self.assertEqual(False, payload["stream"])
        self.assertEqual([{"role": "system", "content": "system"}, {"role": "user", "content": "prompt"}], payload["messages"])

    async def test_sagemaker_optional_model_is_not_guessed(self):
        body = io.BytesIO(json.dumps(sagemaker_output()).encode())
        client = FakeClient({"Body": body})
        with patch.dict(os.environ, environment(SAGEMAKER), clear=True), patch.object(transport, "_new_client", return_value=client):
            await transport.call_model(MODEL, "system", "prompt")
        self.assertNotIn("model", json.loads(client.calls[0][1]["Body"]))

    async def test_model_without_binding_never_gets_a_client(self):
        with patch.dict(os.environ, environment(), clear=True), patch.object(transport, "_new_client") as client:
            with self.assertRaises(transport.OpenModelError) as error:
                await transport.call_model("DeepSeek-R1", "system", "prompt")
            self.assertEqual("OPEN_MODEL_NOT_CONFIGURED", error.exception.code)
            client.assert_not_called()

    async def test_sdk_error_is_sanitized_and_never_retried(self):
        client = FakeClient(error=RuntimeError("SECRET role ARN private endpoint account 123456789012"))
        with patch.dict(os.environ, environment(), clear=True), patch.object(transport, "_new_client", return_value=client):
            with self.assertRaises(transport.OpenModelError) as error:
                await transport.call_model(MODEL, "system", "prompt")
        self.assertEqual("OPEN_MODEL_INVOCATION_FAILED", error.exception.code)
        self.assertNotIn("SECRET", str(error.exception))
        self.assertEqual(1, len(client.calls))
        self.assertTrue(client.closed)

    async def test_known_aws_failures_have_distinct_korean_codes_without_raw_data(self):
        cases = [
            ("AccessDeniedException", "OPEN_MODEL_ACCESS_DENIED", 403),
            ("ResourceNotFoundException", "OPEN_MODEL_RESOURCE_NOT_FOUND", 502),
            ("ValidationException", "OPEN_MODEL_REQUEST_REJECTED", 400),
            ("ThrottlingException", "OPEN_MODEL_QUOTA_EXCEEDED", 429),
            ("ServiceQuotaExceededException", "OPEN_MODEL_QUOTA_EXCEEDED", 429),
            ("ModelTimeoutException", "OPEN_MODEL_TIMEOUT", 504),
            ("ServiceUnavailableException", "OPEN_MODEL_SERVICE_UNAVAILABLE", 503),
            ("UnrecognizedClientException", "OPEN_MODEL_AUTHENTICATION_FAILED", 403),
            ("ModelNotReadyException", "OPEN_MODEL_NOT_READY", 503),
        ]
        for aws_code, expected_code, status in cases:
            error = RuntimeError("RAW_SECRET_ACCOUNT_ID_ENDPOINT")
            error.response = {"Error": {"Code": aws_code, "Message": "RAW_SECRET_ACCOUNT_ID_ENDPOINT"}}
            client = FakeClient(error=error)
            with self.subTest(aws_code=aws_code), patch.dict(os.environ, environment(), clear=True), patch.object(transport, "_new_client", return_value=client):
                with self.assertRaises(transport.OpenModelError) as result:
                    await transport.call_model(MODEL, "system", "prompt")
            self.assertEqual(expected_code, result.exception.code)
            self.assertEqual(status, result.exception.http_status)
            self.assertRegex(str(result.exception), "[가-힣]")
            self.assertNotIn("RAW_SECRET", str(result.exception))
            self.assertEqual(1, len(client.calls))
            self.assertTrue(client.closed)

    async def test_missing_credentials_is_separate_from_model_permission_denial(self):
        missing_credentials = type("NoCredentialsError", (RuntimeError,), {})
        client = FakeClient(error=missing_credentials("RAW_SECRET"))
        with patch.dict(os.environ, environment(), clear=True), patch.object(transport, "_new_client", return_value=client):
            with self.assertRaises(transport.OpenModelError) as result:
                await transport.call_model(MODEL, "system", "prompt")
        self.assertEqual("OPEN_MODEL_CREDENTIALS_UNAVAILABLE", result.exception.code)
        self.assertEqual(503, result.exception.http_status)
        self.assertIn("자격증명", str(result.exception))
        self.assertNotIn("RAW_SECRET", str(result.exception))

    async def test_input_invalid_before_client_creation(self):
        cases = [(MODEL, "", "prompt", 2), (MODEL, "system", "", 2),
                 (MODEL, "system", "prompt", True), (MODEL, "system", "prompt", 0),
                 (MODEL, "system", "x" * (transport.MAX_PROMPT_BYTES + 1), 2),
                 ("unknown", "system", "prompt", 2)]
        for model, system, prompt, count in cases:
            with self.subTest(model=model, count=count), patch.object(transport, "_new_client") as client:
                with self.assertRaises(transport.OpenModelError):
                    await transport.call_model(model, system, prompt, max_tokens=count)
                client.assert_not_called()

    async def test_bedrock_tool_calls_truncation_and_invalid_results_rejected(self):
        tool = bedrock_output()
        tool["output"]["message"]["content"].append({"toolUse": {"name": "execute"}})
        cases = [bedrock_output(stop="max_tokens"), bedrock_output(stop="tool_use"),
                 bedrock_output(stop="content_filtered"), tool,
                 bedrock_output(text=""), bedrock_output(text={"unexpected": "object"}),
                 bedrock_output(text="x" * (transport.MAX_RESPONSE_BYTES + 1)), {}]
        for output in cases:
            client = FakeClient(output)
            with self.subTest(output=str(output)[:80]), patch.dict(os.environ, environment(), clear=True), patch.object(transport, "_new_client", return_value=client):
                with self.assertRaises(transport.OpenModelError):
                    await transport.call_model(MODEL, "system", "prompt")
            self.assertTrue(client.closed)

    async def test_bedrock_reasoning_is_discarded_while_final_text_is_returned(self):
        output = bedrock_output()
        output["output"]["message"]["content"].insert(0, {
            "reasoningContent": {"reasoningText": {"text": "PRIVATE_REASONING_DO_NOT_RETURN", "signature": "PRIVATE_SIGNATURE"}},
        })
        client = FakeClient(output)
        with patch.dict(os.environ, environment(), clear=True), patch.object(transport, "_new_client", return_value=client):
            result = await transport.call_model(MODEL, "system", "prompt")
        self.assertEqual("검사된 응답", result)
        self.assertNotIn("PRIVATE", result)
        self.assertTrue(client.closed)

    async def test_bedrock_redacted_binary_reasoning_allows_final_answer_without_leaking(self):
        output = bedrock_output("최종 답변")
        output["output"]["message"]["content"].insert(0, {
            "reasoningContent": {"redactedContent": b"PRIVATE_REDACTED_REASONING"},
        })
        client = FakeClient(output)
        with patch.dict(os.environ, environment(), clear=True), patch.object(transport, "_new_client", return_value=client):
            result = await transport.call_model(MODEL, "system", "prompt")
        self.assertEqual("최종 답변", result)
        self.assertNotIn("PRIVATE", result)
        self.assertTrue(client.closed)

    async def test_redacted_reasoning_cannot_bypass_response_size_cap(self):
        # Raw bytes are below the cap, but the JSON/base64 response exceeds it.
        output = bedrock_output("최종 답변")
        output["output"]["message"]["content"].insert(0, {
            "reasoningContent": {"redactedContent": b"x" * (transport.MAX_RESPONSE_BYTES * 3 // 4)},
        })
        client = FakeClient(output)
        with patch.dict(os.environ, environment(), clear=True), patch.object(transport, "_new_client", return_value=client):
            with self.assertRaises(transport.OpenModelError) as error:
                await transport.call_model(MODEL, "system", "prompt")
        self.assertEqual("OPEN_MODEL_RESPONSE_TOO_LARGE", error.exception.code)
        self.assertTrue(client.closed)

    async def test_reasoning_only_and_reasoning_disguised_tool_blocks_are_rejected(self):
        cases = [
            [{"reasoningContent": {"reasoningText": {"text": "PRIVATE"}}}],
            [{"reasoningContent": {}, "toolUse": {"name": "execute"}}, {"text": "answer"}],
            [{"reasoningContent": {}, "text": "answer"}],
        ]
        for content in cases:
            output = bedrock_output()
            output["output"]["message"]["content"] = content
            client = FakeClient(output)
            with self.subTest(content=content), patch.dict(os.environ, environment(), clear=True), patch.object(transport, "_new_client", return_value=client):
                with self.assertRaises(transport.OpenModelError) as error:
                    await transport.call_model(MODEL, "system", "prompt")
            self.assertEqual("OPEN_MODEL_RESPONSE_INVALID", error.exception.code)
            self.assertNotIn("PRIVATE", str(error.exception))
            self.assertTrue(client.closed)

    async def test_sagemaker_tool_calls_truncation_bad_json_and_size_rejected(self):
        tools = sagemaker_output()
        tools["choices"][0]["message"]["tool_calls"] = [{"type": "function"}]
        stop_reason = sagemaker_output()
        stop_reason["stop_reason"] = "max_tokens"
        cases = [sagemaker_output(finish="length"), sagemaker_output(finish="tool_calls"), tools,
                 sagemaker_output(text=["not", "a", "string"]), stop_reason,
                 b"not-json", b'{"choices":[],"choices":[]}',
                 b"x" * (transport.MAX_RESPONSE_BYTES + 1)]
        for output in cases:
            raw = output if isinstance(output, bytes) else json.dumps(output).encode()
            body = io.BytesIO(raw)
            client = FakeClient({"Body": body})
            with self.subTest(output=str(output)[:80]), patch.dict(os.environ, environment(SAGEMAKER), clear=True), patch.object(transport, "_new_client", return_value=client):
                with self.assertRaises(transport.OpenModelError):
                    await transport.call_model(MODEL, "system", "prompt")
            self.assertTrue(body.closed)
            self.assertTrue(client.closed)


HTTP = {
    "transport": "openai_compatible",
    "endpoint_url": "https://inference.provider.com/v1/chat/completions",
    "model_id": "explicit-checkpoint-id",
    "api_key_env": "SELECTED_MODEL_API_KEY",
    "request_format": "chat",
}


def http_environment(binding=None, enabled="true"):
    return {**environment(HTTP if binding is None else binding, enabled),
            "SELECTED_MODEL_API_KEY": "private-test-token"}


class LunaAzureTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_outer_key_whitespace_is_trimmed_without_changing_prompt(self):
        env = {"EDURECOVER_OPEN_MODELS_ENABLED": "true", "DEEPSEEK_API_KEY": " \t direct-private-token\r\n"}
        prompt = "  원문\n끝  "
        with patch.dict(os.environ, env, clear=True), patch.object(
                transport, "post_json", return_value=sagemaker_output()) as post:
            await transport.call_model("DeepSeek-V4.1-Flash", "지시", prompt, route="learning")
        self.assertEqual(post.call_args.args[2], "direct-private-token")
        self.assertEqual(post.call_args.args[1]["messages"][1]["content"], prompt)

    async def test_preset_routes_luna_only_to_azure_without_relaxing_zdr(self):
        for strict_zdr in ("true", "false"):
            with self.subTest(strict_zdr=strict_zdr), patch.dict(os.environ, {
                "EDURECOVER_GPT6_LUNA_AZURE_ENABLED": "true",
                "EDURECOVER_OPEN_MODELS_ENABLED": "true",
                "EDURECOVER_REQUIRE_ZDR": strict_zdr,
                "OPENROUTER_API_KEY": "private-preset-token",
            }, clear=True), patch.object(transport, "post_json", return_value=sagemaker_output()) as post, \
                    patch.object(transport, "_new_client") as sdk:
                answer = await transport.call_model(LUNA_KEY, "instruction", "question", max_tokens=64)
            self.assertEqual("검사된 응답", answer)
            post.assert_called_once_with("https://openrouter.ai/api/v1/chat/completions", {
                "model": "openai/gpt-6-luna", "max_tokens": 64, "stream": False,
                "messages": [{"role": "system", "content": "instruction"},
                             {"role": "user", "content": "question"}],
                "provider": {"zdr": True, "data_collection": "deny",
                             "only": ["azure"], "allow_fallbacks": False},
            }, "private-preset-token")
            sdk.assert_not_called()

    async def test_explicit_json_luna_binding_also_enforces_azure_and_zdr(self):
        with patch.dict(os.environ, {
            "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": json.dumps({LUNA_KEY: LUNA_BINDING}),
            "EDURECOVER_OPEN_MODELS_ENABLED": "true",
            "EDURECOVER_GPT6_LUNA_AZURE_ENABLED": "false",
            "OPENROUTER_API_KEY": "private-json-token",
        }, clear=True), patch.object(transport, "post_json", return_value=sagemaker_output()) as post:
            await transport.call_model(LUNA_KEY, "instruction", "question")
        post.assert_called_once()
        self.assertEqual({"zdr": True, "data_collection": "deny", "only": ["azure"],
                          "allow_fallbacks": False}, post.call_args.args[1]["provider"])

    async def test_preset_does_not_change_deepseek_routing(self):
        deepseek = {**LUNA_BINDING, "model_id": "deepseek/deepseek-v4.1-flash"}
        with patch.dict(os.environ, {
            "EDURECOVER_GPT6_LUNA_AZURE_ENABLED": "true",
            "EDURECOVER_OPEN_MODELS_ENABLED": "true",
            "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": json.dumps({"DeepSeek-V4.1-Flash": deepseek}),
            "OPENROUTER_API_KEY": "private-shared-token",
        }, clear=True), patch.object(transport, "post_json", return_value=sagemaker_output()) as post:
            await transport.call_model("DeepSeek-V4.1-Flash", "instruction", "question")
        post.assert_called_once()
        self.assertEqual("deepseek/deepseek-v4.1-flash", post.call_args.args[1]["model"])
        self.assertEqual({"zdr": True, "data_collection": "deny"}, post.call_args.args[1]["provider"])

    async def test_tampered_luna_configuration_is_rejected_before_any_network_attempt(self):
        invalid = [
            BEDROCK, SAGEMAKER,
            {**LUNA_BINDING, "transport": "sagemaker"},
            {**LUNA_BINDING, "model_id": "openai/gpt-6"},
            {**LUNA_BINDING, "endpoint_url": "https://api.openai.com/v1/chat/completions"},
            {**LUNA_BINDING, "endpoint_url": "https://openrouter.ai.other-provider.com/api/v1/chat/completions"},
            {**LUNA_BINDING, "endpoint_url": "https://openrouter.ai/other/chat/completions"},
            {**LUNA_BINDING, "request_format": "completion"},
            {**LUNA_BINDING, "api_key_env": "OPENAI_API_KEY"},
            {**LUNA_BINDING, "api_key_env": "private-forbidden-value"},
            {**LUNA_BINDING, "provider": {"only": ["openai"], "zdr": False}},
        ]
        for binding in invalid:
            with self.subTest(binding=binding), patch.dict(os.environ, {
                "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": json.dumps({LUNA_KEY: binding}),
                "EDURECOVER_OPEN_MODELS_ENABLED": "true",
                "OPENROUTER_API_KEY": "private-preset-token",
                "OPENAI_API_KEY": "private-other-token",
            }, clear=True), patch.object(transport, "post_json") as post, \
                    patch.object(transport, "_new_client") as sdk:
                with self.assertRaises(transport.OpenModelError) as error:
                    await transport.call_model(LUNA_KEY, "instruction", "private-question")
            self.assertEqual("OPEN_MODEL_CONFIG_INVALID", error.exception.code)
            for private in ("private-", "openrouter.ai", "api.openai.com", "OPENAI_API_KEY"):
                self.assertNotIn(private, str(error.exception))
            post.assert_not_called()
            sdk.assert_not_called()

    async def test_luna_no_eligible_azure_endpoint_fails_once_without_privacy_off_retry(self):
        with patch.dict(os.environ, {
            "EDURECOVER_GPT6_LUNA_AZURE_ENABLED": "true",
            "EDURECOVER_OPEN_MODELS_ENABLED": "true",
            "OPENROUTER_API_KEY": "private-preset-token",
        }, clear=True), patch.object(transport, "post_json", side_effect=transport.OpenModelHTTPError(
                "OPEN_MODEL_RESOURCE_NOT_FOUND")) as post, patch.object(transport, "_new_client") as sdk:
            with self.assertRaises(transport.OpenModelError) as error:
                await transport.call_model(LUNA_KEY, "instruction", "question")
        self.assertEqual("OPEN_MODEL_RESOURCE_NOT_FOUND", error.exception.code)
        post.assert_called_once()
        self.assertEqual({"zdr": True, "data_collection": "deny", "only": ["azure"],
                          "allow_fallbacks": False}, post.call_args.args[1]["provider"])
        sdk.assert_not_called()

    async def test_luna_without_credentials_or_feature_enable_never_calls_a_provider(self):
        for token, feature in ((None, "true"), ("private-preset-token", "false")):
            env = {"EDURECOVER_GPT6_LUNA_AZURE_ENABLED": "true",
                   "EDURECOVER_OPEN_MODELS_ENABLED": feature}
            if token is not None:
                env["OPENROUTER_API_KEY"] = token
            with self.subTest(token=bool(token), feature=feature), patch.dict(os.environ, env, clear=True), \
                    patch.object(transport, "post_json") as post, patch.object(transport, "_new_client") as sdk:
                with self.assertRaises(transport.OpenModelError) as error:
                    await transport.call_model(LUNA_KEY, "instruction", "question")
            self.assertEqual("OPEN_MODEL_CREDENTIALS_UNAVAILABLE" if token is None else "OPEN_MODEL_DISABLED",
                             error.exception.code)
            post.assert_not_called()
            sdk.assert_not_called()


class DeepSeekDirectTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_presets_migrate_deepseek_only_and_keep_luna_strict(self):
        legacy = {**LUNA_BINDING, "model_id": "deepseek/legacy"}
        env = {
            "EDURECOVER_DEEPSEEK_DIRECT_ENABLED": "true",
            "EDURECOVER_GPT6_LUNA_AZURE_ENABLED": "true",
            "EDURECOVER_OPEN_MODELS_ENABLED": "true",
            "DEEPSEEK_API_KEY": "direct-private-token", "OPENROUTER_API_KEY": "luna-private-token",
            "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": json.dumps({
                "DeepSeek-V4.1-Flash": legacy,
                "DeepSeek-R1": {**legacy, "endpoint_url": "https://OPENROUTER.AI/other/chat/completions"},
                MODEL: BEDROCK,
            }),
            "EDURECOVER_OPEN_DEEPSEEK_MODEL_KEY": "DeepSeek-R1",
            "EDURECOVER_OPEN_DEEPSEEK_MODEL_ID": "former-bedrock-id",
            "EDURECOVER_OPEN_DEEPSEEK_REGION": "ap-northeast-2",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(
            transport, "post_json", return_value=sagemaker_output()
        ) as post:
            bindings = transport._bindings()
            self.assertNotIn("DeepSeek-R1", bindings)
            self.assertEqual("bedrock", bindings[MODEL].transport)
            for key, model_id in (("DeepSeek-V4.1-Flash", "deepseek-flash"),
                                  ("DeepSeek-V4-Pro", "deepseek-v4-pro")):
                await transport.call_model(key, "instruction", "question", max_tokens=64)
                self.assertEqual("https://api.deepseek.com/chat/completions", post.call_args.args[0])
                self.assertEqual(model_id, post.call_args.args[1]["model"])
                self.assertNotIn("provider", post.call_args.args[1])
                self.assertEqual("direct-private-token", post.call_args.args[2])
            await transport.call_model(LUNA_KEY, "instruction", "question")
            self.assertEqual({"zdr": True, "data_collection": "deny", "only": ["azure"],
                              "allow_fallbacks": False}, post.call_args.args[1]["provider"])
            self.assertEqual(3, post.call_count)

    async def test_missing_direct_key_is_visible_and_never_uses_openrouter_key(self):
        with patch.dict(os.environ, {
            "EDURECOVER_DEEPSEEK_DIRECT_ENABLED": "true", "EDURECOVER_OPEN_MODELS_ENABLED": "true",
            "OPENROUTER_API_KEY": "unrelated-private-token",
        }, clear=True), patch.object(transport, "post_json") as post:
            rows = {row["model_key"]: row for row in transport.catalog_status()}
            for key in ("DeepSeek-V4.1-Flash", "DeepSeek-V4-Pro"):
                self.assertEqual("missing_credentials", rows[key]["readiness_state"])
                self.assertFalse(rows[key]["enabled"])
                self.assertEqual("DEEPSEEK_API_KEY", rows[key]["required_api_key_env"])
            with self.assertRaises(transport.OpenModelError) as error:
                await transport.call_model("DeepSeek-V4.1-Flash", "instruction", "question")
            self.assertEqual("OPEN_MODEL_CREDENTIALS_UNAVAILABLE", error.exception.code)
            post.assert_not_called()

    async def test_direct_failure_does_not_retry_or_fallback(self):
        with patch.dict(os.environ, {
            "EDURECOVER_DEEPSEEK_DIRECT_ENABLED": "true", "EDURECOVER_OPEN_MODELS_ENABLED": "true",
            "DEEPSEEK_API_KEY": "direct-private-token", "OPENROUTER_API_KEY": "unrelated-private-token",
        }, clear=True), patch.object(transport, "post_json", side_effect=transport.OpenModelHTTPError(
            "OPEN_MODEL_QUOTA_EXCEEDED"
        )) as post:
            with self.assertRaises(transport.OpenModelError) as error:
                await transport.call_model("DeepSeek-V4.1-Flash", "instruction", "question")
            self.assertEqual("OPEN_MODEL_QUOTA_EXCEEDED", error.exception.code)
            post.assert_called_once()
            self.assertEqual("https://api.deepseek.com/chat/completions", post.call_args.args[0])

    async def test_invalid_direct_flag_fails_before_any_network(self):
        with patch.dict(os.environ, {"EDURECOVER_DEEPSEEK_DIRECT_ENABLED": "treu"}, clear=True), \
                patch.object(transport, "post_json") as post:
            with self.assertRaises(transport.OpenModelError) as error:
                transport.catalog_status()
            self.assertEqual("OPEN_MODEL_CONFIG_INVALID", error.exception.code)
            post.assert_not_called()


class HTTPTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_openrouter_always_requires_zdr_and_keeps_the_exact_selected_model(self):
        key = "DeepSeek-V4.1-Flash"
        binding = {**HTTP, "endpoint_url": "https://openrouter.ai/api/v1/chat/completions",
                   "model_id": "deepseek/deepseek-v4.1-flash"}
        env = {**http_environment(), "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": json.dumps({key: binding})}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(transport, "post_json", return_value=sagemaker_output()) as post, \
                patch.object(transport, "_new_client") as sdk:
            answer = await transport.call_model(key, "instruction", "question", max_tokens=64)
        self.assertEqual("검사된 응답", answer)
        post.assert_called_once_with(binding["endpoint_url"], {
            "model": "deepseek/deepseek-v4.1-flash", "max_tokens": 64, "stream": False,
            "messages": [{"role": "system", "content": "instruction"},
                         {"role": "user", "content": "question"}],
            "provider": {"zdr": True, "data_collection": "deny"},
        }, "private-test-token")
        sdk.assert_not_called()

    async def test_openrouter_zdr_failure_does_not_retry_without_privacy_controls(self):
        binding = {**HTTP, "endpoint_url": "https://openrouter.ai/api/v1/chat/completions"}
        with patch.dict(os.environ, http_environment(binding), clear=True), \
                patch.object(transport, "post_json", side_effect=transport.OpenModelHTTPError(
                    "OPEN_MODEL_RESOURCE_NOT_FOUND")) as post:
            with self.assertRaises(transport.OpenModelError):
                await transport.call_model(MODEL, "instruction", "question")
        post.assert_called_once()
        self.assertEqual({"zdr": True, "data_collection": "deny"}, post.call_args.args[1]["provider"])

    async def test_openrouter_policy_matches_only_the_exact_official_endpoint(self):
        for endpoint in ("https://openrouter.ai.other-provider.com/api/v1/chat/completions",
                         "https://other-provider.com/openrouter.ai/api/v1/chat/completions",
                         "https://openrouter.ai/other/chat/completions"):
            with self.subTest(endpoint=endpoint), \
                    patch.dict(os.environ, http_environment({**HTTP, "endpoint_url": endpoint}), clear=True), \
                    patch.object(transport, "post_json", return_value=sagemaker_output()) as post:
                await transport.call_model(MODEL, "instruction", "question")
            self.assertNotIn("provider", post.call_args.args[1])

    async def test_all_25_exact_names_use_their_explicit_binding_without_aws(self):
        entries = {
            entry["model_key"]: {**HTTP, "model_id": "checkpoint-" + str(i),
                                 "api_key_env": "MODEL_" + str(i) + "_KEY"}
            for i, entry in enumerate(SOURCE_MODEL_CATALOG)
        }
        env = {"EDURECOVER_OPEN_MODEL_BINDINGS_JSON": json.dumps(entries),
               "EDURECOVER_OPEN_MODELS_ENABLED": "true"}
        env.update({"MODEL_" + str(i) + "_KEY": "token-" + str(i) for i in range(25)})
        with patch.dict(os.environ, env, clear=True), \
                patch.object(transport, "post_json", return_value=sagemaker_output()) as post, \
                patch.object(transport, "_new_client") as sdk:
            rows = transport.catalog_status()
            self.assertEqual(25, sum(row["enabled"] for row in rows))
            for i, entry in enumerate(SOURCE_MODEL_CATALOG):
                with self.subTest(model_key=entry["model_key"]):
                    answer = await transport.call_model(entry["model_key"], "instruction", "question", max_tokens=64)
                    self.assertEqual("검사된 응답", answer)
                    post.assert_called_with(HTTP["endpoint_url"], {
                        "model": "checkpoint-" + str(i), "max_tokens": 64, "stream": False,
                        "messages": [{"role": "system", "content": "instruction"},
                                     {"role": "user", "content": "question"}],
                    }, "token-" + str(i))
            self.assertEqual(25, post.call_count)
            sdk.assert_not_called()
        serialized = json.dumps(rows)
        for private in ("token-", "provider.com", "checkpoint-", "MODEL_0_KEY"):
            self.assertNotIn(private, serialized)

    async def test_pretrained_base_uses_completion_contract_without_chat_template_guess(self):
        key = "Mistral-Large-3-675B-Base-2512"
        binding = {**HTTP, "request_format": "completion",
                   "endpoint_url": "https://inference.provider.com/v1/completions",
                   "model_id": "mistralai/" + key}
        env = {**http_environment(), "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": json.dumps({key: binding})}
        response = {"choices": [{"text": " 최종 결과 ", "finish_reason": "stop"}]}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(transport, "post_json", return_value=response) as post, \
                patch.object(transport, "_new_client") as sdk:
            answer = await transport.call_model(key, "instructions", "prompt", max_tokens=100)
        self.assertEqual(" 최종 결과 ", answer)
        post.assert_called_once_with(binding["endpoint_url"], {
            "model": binding["model_id"], "max_tokens": 100, "stream": False,
            "prompt": "instructions\n\nprompt",
        }, "private-test-token")
        sdk.assert_not_called()

    async def test_missing_or_invalid_credentials_never_call_http_or_aws(self):
        for token in (None, "", " \t\n", "secret\r\nHeader: value", "embedded space", "x" * 8193):
            env = http_environment()
            if token is None:
                env.pop("SELECTED_MODEL_API_KEY")
            else:
                env["SELECTED_MODEL_API_KEY"] = token
            with self.subTest(token=str(token)[:30]), patch.dict(os.environ, env, clear=True), \
                    patch.object(transport, "post_json") as post, \
                    patch.object(transport, "_new_client") as sdk:
                row = next(row for row in transport.catalog_status() if row["model_key"] == MODEL)
                self.assertFalse(row["configured"])
                self.assertFalse(row["enabled"])
                self.assertEqual("missing_credentials", row["readiness_state"])
                with self.assertRaises(transport.OpenModelError) as error:
                    await transport.call_model(MODEL, "instructions", "prompt")
                self.assertEqual("OPEN_MODEL_CREDENTIALS_UNAVAILABLE", error.exception.code)
                post.assert_not_called()
                sdk.assert_not_called()

    async def test_disabled_feature_never_calls_http(self):
        with patch.dict(os.environ, http_environment(enabled="false"), clear=True), \
                patch.object(transport, "post_json") as post:
            row = next(row for row in transport.catalog_status() if row["model_key"] == MODEL)
            self.assertTrue(row["configured"])
            self.assertFalse(row["enabled"])
            self.assertEqual("disabled", row["readiness_state"])
            with self.assertRaises(transport.OpenModelError) as error:
                await transport.call_model(MODEL, "instructions", "prompt")
            self.assertEqual("OPEN_MODEL_DISABLED", error.exception.code)
            post.assert_not_called()

    async def test_http_errors_use_only_stable_public_codes(self):
        cases = {
            "OPEN_MODEL_ACCESS_DENIED": 403,
            "OPEN_MODEL_AUTHENTICATION_FAILED": 403,
            "OPEN_MODEL_QUOTA_EXCEEDED": 429,
            "OPEN_MODEL_TIMEOUT": 504,
            "OPEN_MODEL_RESOURCE_NOT_FOUND": 502,
            "OPEN_MODEL_REQUEST_REJECTED": 400,
            "OPEN_MODEL_INPUT_INVALID": 400,
            "OPEN_MODEL_INPUT_TOO_LARGE": 413,
            "OPEN_MODEL_CONFIG_INVALID": 503,
            "OPEN_MODEL_RESPONSE_TOO_LARGE": 502,
            "OPEN_MODEL_RESPONSE_INVALID": 502,
            "OPEN_MODEL_SERVICE_UNAVAILABLE": 503,
            "PRIVATE_UNKNOWN_CODE": 503,
        }
        for code, expected_status in cases.items():
            error = transport.OpenModelHTTPError(code)
            with self.subTest(code=code), patch.dict(os.environ, http_environment(), clear=True), \
                    patch.object(transport, "post_json", side_effect=error) as post:
                with self.assertRaises(transport.OpenModelError) as result:
                    await transport.call_model(MODEL, "instructions", "prompt")
                expected_code = code if code != "PRIVATE_UNKNOWN_CODE" else "OPEN_MODEL_SERVICE_UNAVAILABLE"
                self.assertEqual(expected_code, result.exception.code)
                self.assertEqual(expected_status, result.exception.http_status)
                self.assertNotIn("PRIVATE", str(result.exception))
                self.assertNotIn("private-test-token", str(result.exception))
                post.assert_called_once()

    def test_http_binding_is_checked_offline_and_never_guesses_missing_fields(self):
        invalid = [
            {**HTTP, "api_key": "DO_NOT_USE_LITERAL_SECRET"},
            {**HTTP, "api_key_env": "lower_case"},
            {**HTTP, "api_key_env": "BAD-KEY"},
            {**HTTP, "api_key_env": ""},
            {**HTTP, "request_format": "auto"},
            {**HTTP, "request_format": []},
            {**HTTP, "request_format": "completion"},
            {**HTTP, "endpoint_url": "https://inference.provider.com/v1/completions"},
            {**HTTP, "endpoint_url": "https://inference.provider.com/v1"},
            {**HTTP, "endpoint_url": "http://inference.provider.com/v1/chat/completions"},
            {**HTTP, "endpoint_url": "https://127.0.0.1/v1/chat/completions"},
            {**HTTP, "endpoint_url": "https://inference.provider.com/v1/chat/completions?api_key=private"},
            {**HTTP, "endpoint_url": "https://model-01.example.com/v1/chat/completions"},
            {**HTTP, "model_id": None},
        ]
        invalid.extend({k: v for k, v in HTTP.items() if k != field} for field in HTTP)
        for binding in invalid:
            with self.subTest(binding=binding), patch.dict(os.environ, http_environment(binding), clear=True), \
                    patch("socket.getaddrinfo") as dns, patch.object(transport, "post_json") as post:
                with self.assertRaises(transport.OpenModelError) as error:
                    transport.catalog_status()
                self.assertEqual("OPEN_MODEL_CONFIG_INVALID", error.exception.code)
                self.assertNotIn("DO_NOT_USE_LITERAL_SECRET", str(error.exception))
                post.assert_not_called()
                dns.assert_not_called()

    def test_http_status_checks_do_not_resolve_dns_or_disclose_runtime_values(self):
        with patch.dict(os.environ, http_environment(), clear=True), \
                patch("socket.getaddrinfo") as dns, patch.object(transport, "post_json") as post:
            row = next(row for row in transport.catalog_status() if row["model_key"] == MODEL)
            self.assertEqual("configured_not_verified", row["readiness_state"])
            dns.assert_not_called()
            post.assert_not_called()

    async def test_invalid_or_incomplete_http_responses_are_not_returned_as_answers(self):
        tool = sagemaker_output()
        tool["choices"][0]["message"]["tool_calls"] = [{"type": "function"}]
        malformed_stop = sagemaker_output()
        malformed_stop["choices"][0]["stop_reason"] = {"private": "not-a-reason"}
        malformed_stop_list = sagemaker_output()
        malformed_stop_list["stop_reason"] = []
        cases = [None, {}, {"choices": []},
                 {"choices": sagemaker_output()["choices"] * 2},
                 sagemaker_output(finish="length"), tool, malformed_stop,
                 malformed_stop_list, sagemaker_output(text=""),
                 sagemaker_output(text=[{"type": "text", "text": "no"}]),
                 sagemaker_output(text="x" * (transport.MAX_RESPONSE_BYTES + 1))]
        for response in cases:
            with self.subTest(response=str(response)[:80]), patch.dict(os.environ, http_environment(), clear=True), \
                    patch.object(transport, "post_json", return_value=response):
                with self.assertRaises(transport.OpenModelError):
                    await transport.call_model(MODEL, "instructions", "prompt")

    async def test_chat_final_answer_does_not_expose_reasoning_fields(self):
        response = sagemaker_output("최종 답변")
        response["choices"][0]["message"]["reasoning_content"] = "PRIVATE_REASONING"
        with patch.dict(os.environ, http_environment(), clear=True), \
                patch.object(transport, "post_json", return_value=response):
            answer = await transport.call_model(MODEL, "instructions", "prompt")
        self.assertEqual("최종 답변", answer)
        self.assertNotIn("PRIVATE", answer)


class UnboundedOutputTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_none_omits_app_token_and_thinking_fields(self):
        with patch.dict(os.environ, {
            "EDURECOVER_OPEN_MODELS_ENABLED": "true",
            "EDURECOVER_DEEPSEEK_DIRECT_ENABLED": "true",
            "DEEPSEEK_API_KEY": "direct-private-token",
        }, clear=True), patch.object(transport, "post_json", return_value=sagemaker_output()) as post:
            for key in ("DeepSeek-V4.1-Flash", "DeepSeek-V4-Pro"):
                await transport.call_model(key, "instructions", "write code", max_tokens=None)
                payload = post.call_args.args[1]
                self.assertNotIn("max_tokens", payload)
                self.assertNotIn("max_completion_tokens", payload)
                self.assertNotIn("thinking", payload)
                self.assertNotIn("reasoning_effort", payload)
                self.assertFalse(payload["stream"])
        self.assertEqual(2, post.call_count)

    async def test_explicit_large_output_budget_reaches_provider_unchanged(self):
        with patch.dict(os.environ, http_environment(), clear=True), patch.object(
            transport, "post_json", return_value=sagemaker_output()
        ) as post:
            await transport.call_model(MODEL, "instructions", "write code", max_tokens=131072)
        self.assertEqual(131072, post.call_args.args[1]["max_tokens"])

    async def test_legacy_default_is_retained_for_structured_callers(self):
        with patch.dict(os.environ, http_environment(), clear=True), patch.object(
            transport, "post_json", return_value=sagemaker_output()
        ) as post:
            await transport.call_model(MODEL, "instructions", "write code")
        self.assertEqual(2048, post.call_args.args[1]["max_tokens"])

    async def test_bedrock_none_omits_inference_limit(self):
        client = FakeClient(bedrock_output())
        with patch.dict(os.environ, environment(), clear=True), patch.object(
            transport, "_new_client", return_value=client
        ):
            await transport.call_model(MODEL, "instructions", "write code", max_tokens=None)
        self.assertNotIn("inferenceConfig", client.calls[0][1])
        self.assertTrue(client.closed)

    async def test_sagemaker_none_omits_token_limit(self):
        body = io.BytesIO(json.dumps(sagemaker_output()).encode())
        client = FakeClient({"Body": body})
        with patch.dict(os.environ, environment(SAGEMAKER), clear=True), patch.object(
            transport, "_new_client", return_value=client
        ):
            await transport.call_model(MODEL, "instructions", "write code", max_tokens=None)
        self.assertNotIn("max_tokens", json.loads(client.calls[0][1]["Body"]))
        self.assertTrue(body.closed)
        self.assertTrue(client.closed)

    async def test_long_complete_code_is_not_rejected_at_former_response_limit(self):
        answer = "print('long complete code')\n" * 12000
        self.assertGreater(len(answer.encode()), 256 * 1024)
        with patch.dict(os.environ, http_environment(), clear=True), patch.object(
            transport, "post_json", return_value=sagemaker_output(answer)
        ):
            actual = await transport.call_model(MODEL, "instructions", "write code", max_tokens=None)
        self.assertEqual(answer, actual)

    def test_output_limit_failures_are_distinct_from_other_incomplete_answers(self):
        for reason in ("length", "max_tokens", "model_context_window_exceeded"):
            outputs = [sagemaker_output("PRIVATE_TRUNCATED_CODE", finish=reason)]
            root_stop = sagemaker_output("PRIVATE_TRUNCATED_CODE")
            root_stop["stop_reason"] = reason
            outputs.append(root_stop)
            choice_stop = sagemaker_output("PRIVATE_TRUNCATED_CODE")
            choice_stop["choices"][0]["stop_reason"] = reason
            outputs.append(choice_stop)
            for response in outputs:
                with self.subTest(reason=reason, response=response), self.assertRaises(
                    transport.OpenModelError
                ) as error:
                    transport._openai_text(response, "chat")
                self.assertEqual("OPEN_MODEL_OUTPUT_LIMIT_REACHED", error.exception.code)
                self.assertIn("한도", str(error.exception))
                self.assertNotIn("PRIVATE", str(error.exception))
        with self.assertRaises(transport.OpenModelError) as error:
            transport._openai_text(sagemaker_output(finish="content_filter"), "chat")
        self.assertEqual("OPEN_MODEL_INCOMPLETE_RESPONSE", error.exception.code)
        self.assertNotIn("한도", str(error.exception))
        with self.assertRaises(transport.OpenModelError) as error:
            transport._bedrock_text(bedrock_output(stop="max_tokens"))
        self.assertEqual("OPEN_MODEL_OUTPUT_LIMIT_REACHED", error.exception.code)


class ImageTransportTests(unittest.IsolatedAsyncioTestCase):
    # A real 1x1 PNG, carried inline. No network or filesystem image fetch occurs.
    IMAGE = {
        "mime_type": "image/png",
        "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
    }
    ENV = {
        "EDURECOVER_OPEN_MODELS_ENABLED": "true",
        "EDURECOVER_DEEPSEEK_DIRECT_ENABLED": "true",
        "DEEPSEEK_API_KEY": "direct-private-token",
    }

    async def test_exact_flash_direct_route_sends_documented_image_content(self):
        with patch.dict(os.environ, self.ENV, clear=True), patch.object(
            transport, "post_json", return_value=sagemaker_output("사진 답변")
        ) as post:
            answer = await transport.call_model(
                "DeepSeek-V4.1-Flash", "instructions", "사진을 설명해 주십시오.",
                max_tokens=None, images=[self.IMAGE],
            )
            rows = {row["model_key"]: row for row in transport.catalog_status()}
        self.assertEqual("사진 답변", answer)
        post.assert_called_once()
        endpoint, payload, token = post.call_args.args
        self.assertEqual("https://api.deepseek.com/chat/completions", endpoint)
        self.assertEqual("direct-private-token", token)
        self.assertEqual("deepseek-flash", payload["model"])
        self.assertEqual([
            {"type": "text", "text": "사진을 설명해 주십시오."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + self.IMAGE["data"]}},
        ], payload["messages"][1]["content"])
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("thinking", payload)
        self.assertNotIn("provider", payload)
        self.assertTrue(rows["DeepSeek-V4.1-Flash"]["supports_images"])
        self.assertFalse(rows["DeepSeek-V4-Pro"].get("supports_images", False))
        self.assertNotIn(self.IMAGE["data"], json.dumps(rows))

    async def test_unsupported_models_never_receive_raw_images(self):
        cases = [
            ("DeepSeek-V4-Pro", self.ENV),
            (MODEL, environment()),
            (MODEL, http_environment()),
            (LUNA_KEY, {"EDURECOVER_OPEN_MODELS_ENABLED": "true",
                        "EDURECOVER_GPT6_LUNA_AZURE_ENABLED": "true",
                        "OPENROUTER_API_KEY": "private-luna-token"}),
        ]
        for key, env in cases:
            with self.subTest(model=key), patch.dict(os.environ, env, clear=True), patch.object(
                transport, "post_json"
            ) as post, patch.object(transport, "_new_client") as sdk:
                with self.assertRaises(transport.OpenModelError) as error:
                    await transport.call_model(key, "instructions", "question", images=[self.IMAGE])
                self.assertEqual("OPEN_MODEL_IMAGE_UNSUPPORTED", error.exception.code)
                self.assertNotIn(self.IMAGE["data"], str(error.exception))
                post.assert_not_called()
                sdk.assert_not_called()

    async def test_invalid_images_are_rejected_before_paid_call(self):
        invalid_images = [
            "https://private.example/image.png", self.IMAGE, [self.IMAGE] * 5,
            [{**self.IMAGE, "url": "https://private.example/image.png"}],
            [{"mime_type": "image/png", "data": "https://private.example/image.png"}],
            [{"mime_type": "image/png", "data": "/etc/private-image.png"}],
            [{"mime_type": [], "data": self.IMAGE["data"]}],
            [{"mime_type": "image/gif", "data": self.IMAGE["data"]}],
            [{"mime_type": "image/jpeg", "data": self.IMAGE["data"]}],
            [{"mime_type": "image/png", "data": self.IMAGE["data"] + "\n"}],
            [{"mime_type": "image/png", "data": "eA=="}],
            [{"mime_type": "image/png", "data": b"not-a-string"}],
        ]
        for images in invalid_images:
            with self.subTest(images=str(images)[:100]), patch.dict(os.environ, self.ENV, clear=True), patch.object(
                transport, "post_json"
            ) as post:
                with self.assertRaises(transport.OpenModelError) as error:
                    await transport.call_model("DeepSeek-V4.1-Flash", "instructions", "question", images=images)
                self.assertEqual("OPEN_MODEL_IMAGE_INVALID", error.exception.code)
                self.assertNotIn("private", str(error.exception))
                post.assert_not_called()

    async def test_image_size_limit_is_checked_before_provider_call(self):
        with patch.dict(os.environ, self.ENV, clear=True), patch.object(
            transport, "MAX_IMAGE_BYTES", 16
        ), patch.object(transport, "post_json") as post:
            with self.assertRaises(transport.OpenModelError) as error:
                await transport.call_model(
                    "DeepSeek-V4.1-Flash", "instructions", "question", images=[self.IMAGE],
                )
        self.assertEqual("OPEN_MODEL_IMAGE_INVALID", error.exception.code)
        post.assert_not_called()

    async def test_empty_images_preserve_text_contract(self):
        with patch.dict(os.environ, self.ENV, clear=True), patch.object(
            transport, "post_json", return_value=sagemaker_output()
        ) as post:
            await transport.call_model(
                "DeepSeek-V4.1-Flash", "instructions", "question", images=[],
            )
        self.assertEqual("question", post.call_args.args[1]["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
