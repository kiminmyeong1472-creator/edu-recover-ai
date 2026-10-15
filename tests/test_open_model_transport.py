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
    AWS_ADDITIONAL_MODEL_CATALOG, MODEL_CATALOG, SOURCE_MODEL_CATALOG, model_family,
)


MODEL = "Qwen3.6-27B"
BEDROCK = {"transport": "bedrock", "region": "ap-northeast-2", "model_id": "owner-supplied-id"}
SAGEMAKER = {
    "transport": "sagemaker", "region": "ap-northeast-2", "endpoint_name": "owner-endpoint",
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
        self.assertEqual(29, len(MODEL_CATALOG))
        self.assertEqual(29, len({entry["model_key"] for entry in MODEL_CATALOG}))
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
                 (MODEL, "system", "x" * (transport.MAX_INPUT_BYTES + 1), 2),
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
        # Raw bytes are under 256 KiB, but the JSON/base64 response exceeds it.
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


class HTTPTransportTests(unittest.IsolatedAsyncioTestCase):
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
        self.assertEqual("최종 결과", answer)
        post.assert_called_once_with(binding["endpoint_url"], {
            "model": binding["model_id"], "max_tokens": 100, "stream": False,
            "prompt": "instructions\n\nprompt",
        }, "private-test-token")
        sdk.assert_not_called()

    async def test_missing_or_invalid_credentials_never_call_http_or_aws(self):
        for token in (None, "", "secret\r\nHeader: value", " leading-space", "x" * 8193):
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


if __name__ == "__main__":
    unittest.main()
