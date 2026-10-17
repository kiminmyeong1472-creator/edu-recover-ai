"""Reviewed training-use routes stay separate from existing model transports."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import open_model_api as api
from app.services import open_model_transport as transport
from app.services.open_model_http import OpenModelHTTPError
from app.services.owner_basic_auth import owner_auth_router
from app.services.training_model_policy import permitted_model_catalog


FLASH = "DeepSeek-V4.1-Flash"
PRO = "DeepSeek-V4-Pro"
OFFICIAL_MODELS = {FLASH: "deepseek-flash", PRO: "deepseek-v4-pro"}
DEEPSEEK_TOKEN = "private-learning-deepseek-token"
ENV = {
    "EDURECOVER_OPEN_MODELS_ENABLED": "true",
    "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": "{}",
    "EDURECOVER_OWNER_USER": "owner-test",
    "EDURECOVER_OWNER_PASSWORD": "test-password-only",
    "EDURECOVER_ALLOWED_PROVIDERS": "deepseek",
    "EDURECOVER_AI_HOLD": "false",
}


def output(text="검증할 생성 초안"):
    return {"choices": [{"message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}]}


def request(**changes):
    return dict({"model_key": FLASH, "prompt": "시험용 질문", "explicit_authorization": True},
                **changes)


class TrainingModelTransportTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, ENV, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_learning_catalog_preserves_policy_scope_and_never_runs_inference(self):
        expected = permitted_model_catalog()
        with patch.object(transport, "post_json") as post:
            rows = transport.catalog_status(route="learning")
        self.assertEqual({row["model_key"] for row in rows},
                         {row["model_key"] for row in expected})
        self.assertTrue(set(OFFICIAL_MODELS) <= {row["model_key"] for row in rows})
        by_key = {row["model_key"]: row for row in expected}
        self_hosted = []
        for row in rows:
            for field in ("policy_label", "policy_scope", "policy_source"):
                self.assertEqual(row[field], by_key[row["model_key"]][field])
                self.assertTrue(row[field])
            if row["model_key"] in OFFICIAL_MODELS:
                self.assertEqual(row["readiness_state"], "missing_credentials")
                self.assertNotEqual(row["policy_scope"], "self_hosted_weights")
                self.assertEqual(row["route_label"], "DeepSeek 직접 API · OpenRouter 미사용")
            else:
                self_hosted.append(row)
                self.assertEqual(row["policy_scope"], "self_hosted_weights")
                self.assertEqual(row["readiness_state"], "not_configured")
                self.assertFalse(row["configured"])
                self.assertFalse(row["enabled"])
                self.assertNotIn("supports_images", row)
        self.assertTrue(self_hosted, "verified weights must not be misrepresented as hosted APIs")
        self.assertTrue(next(row for row in rows if row["model_key"] == FLASH)["supports_images"])
        self.assertNotIn("supports_images", next(row for row in rows if row["model_key"] == PRO))
        post.assert_not_called()

    def test_only_dedicated_deepseek_key_supplies_official_api_credentials(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-token",
                                     "CHUTES_API_KEY": "chutes-token"}), \
                patch.object(transport, "post_json") as post:
            rows = transport.catalog_status(route="learning")
            for key in OFFICIAL_MODELS:
                self.assertEqual(next(row for row in rows if row["model_key"] == key)["readiness_state"],
                                 "missing_credentials")
                with self.assertRaises(transport.OpenModelError) as error:
                    asyncio.run(transport.call_model(key, "instructions", "prompt", route="learning"))
                self.assertEqual(error.exception.code, "OPEN_MODEL_CREDENTIALS_UNAVAILABLE")
        post.assert_not_called()

    def test_two_official_apis_use_exact_ids_and_endpoint_without_app_token_limit(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": DEEPSEEK_TOKEN}), \
                patch.object(transport, "_bindings", side_effect=AssertionError("legacy read")), \
                patch.object(transport, "chutes_bindings", side_effect=AssertionError("Chutes read")), \
                patch.object(transport, "post_json", return_value=output()) as post:
            for key, model_id in OFFICIAL_MODELS.items():
                post.reset_mock()
                answer = asyncio.run(transport.call_model(
                    key, "instructions", "prompt", max_tokens=None, route="learning"))
                self.assertEqual(answer, "검증할 생성 초안")
                post.assert_called_once()
                endpoint, payload, token = post.call_args.args
                self.assertEqual(endpoint, "https://api.deepseek.com/chat/completions")
                self.assertEqual(token, DEEPSEEK_TOKEN)
                self.assertEqual(payload["model"], model_id)
                self.assertNotIn("provider", payload)
                self.assertNotIn("max_tokens", payload)

    def test_malformed_legacy_settings_do_not_affect_learning_route(self):
        with patch.dict(os.environ, {
            "DEEPSEEK_API_KEY": DEEPSEEK_TOKEN,
            "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": "bad-json",
            "EDURECOVER_DEEPSEEK_DIRECT_ENABLED": "invalid",
            "EDURECOVER_GPT6_LUNA_AZURE_ENABLED": "invalid",
            "EDURECOVER_VERIFIED_MODEL_ROUTES_ENABLED": "invalid",
            "EDURECOVER_OPEN_DEEPSEEK_MODEL_KEY": "incomplete old setting",
        }), patch.object(transport, "post_json", return_value=output()) as post:
            rows = transport.catalog_status(route="learning")
            for key in OFFICIAL_MODELS:
                row = next(row for row in rows if row["model_key"] == key)
                self.assertTrue(row["enabled"])
                self.assertEqual(row["readiness_state"], "configured_not_verified")
            asyncio.run(transport.call_model(FLASH, "instructions", "prompt", route="learning"))
        post.assert_called_once()

    def test_existing_openrouter_and_chutes_configuration_is_not_changed(self):
        with patch.dict(os.environ, {
            "EDURECOVER_DEEPSEEK_DIRECT_ENABLED": "true",
            "EDURECOVER_GPT6_LUNA_AZURE_ENABLED": "true",
            "DEEPSEEK_API_KEY": DEEPSEEK_TOKEN,
            "OPENROUTER_API_KEY": "private-or-token",
            "CHUTES_API_KEY": "private-chutes-token",
        }):
            env_before = dict(os.environ)
            ordinary_before = transport.catalog_status()
            chutes_before = transport.catalog_status(route="chutes")
            bindings_before = transport._bindings()
            transport.catalog_status(route="learning")
            with patch.object(transport, "post_json", return_value=output()):
                asyncio.run(transport.call_model(FLASH, "instructions", "prompt", route="learning"))
            self.assertEqual(transport.catalog_status(), ordinary_before)
            self.assertEqual(transport.catalog_status(route="chutes"), chutes_before)
            self.assertEqual(transport._bindings(), bindings_before)
            self.assertEqual(dict(os.environ), env_before)
            self.assertEqual(bindings_before["GPT-6-Luna"].endpoint_url,
                             "https://openrouter.ai/api/v1/chat/completions")

    def test_self_hosted_weights_and_other_cloud_models_are_not_callable_here(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": DEEPSEEK_TOKEN,
                                     "OPENROUTER_API_KEY": "private-or-token",
                                     "CHUTES_API_KEY": "private-chutes-token"}), \
                patch.object(transport, "post_json") as post:
            keys = {row["model_key"] for row in permitted_model_catalog()} - set(OFFICIAL_MODELS)
            keys |= {"DeepSeek-V3.2", "GPT-6-Luna", "Claude-Haiku-5.5", "Gemini-2.5-Flash-Lite"}
            for key in sorted(keys):
                with self.subTest(key=key), self.assertRaises(transport.OpenModelError) as error:
                    asyncio.run(transport.call_model(key, "instructions", "prompt", route="learning"))
                self.assertEqual(error.exception.code, "OPEN_MODEL_NOT_CONFIGURED")
        post.assert_not_called()

    def test_self_hosted_permission_never_reuses_a_configured_third_party_api(self):
        # A weight licence and a third-party API contract are separate scopes.
        # This synthetic policy row checks that distinction even if the exact
        # production inventory is revised later.
        weight_row = {
            "model_key": "gpt-oss-20b", "family": "gpt-oss", "display_name": "gpt-oss-20b",
            "policy_scope": "self_hosted_weights", "policy_label": "fixture weight permission",
            "policy_source": "https://opensource.org/license/mit",
        }
        legacy_binding = {
            "transport": "openai_compatible",
            "endpoint_url": "https://openrouter.ai/api/v1/chat/completions",
            "model_id": "openai/gpt-oss-20b", "api_key_env": "OPENROUTER_API_KEY",
            "request_format": "chat",
        }
        with patch.dict(os.environ, {
            "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": json.dumps({"gpt-oss-20b": legacy_binding}),
            "OPENROUTER_API_KEY": "private-or-token", "DEEPSEEK_API_KEY": DEEPSEEK_TOKEN,
        }), patch.object(transport, "permitted_model_catalog", return_value=[weight_row]), \
                patch.object(transport, "post_json") as post:
            row = transport.catalog_status(route="learning")[0]
            self.assertEqual(row["readiness_state"], "not_configured")
            self.assertFalse(row["enabled"])
            with self.assertRaises(transport.OpenModelError) as error:
                asyncio.run(transport.call_model("gpt-oss-20b", "instructions", "prompt", route="learning"))
            self.assertEqual(error.exception.code, "OPEN_MODEL_NOT_CONFIGURED")
        post.assert_not_called()

    def test_provider_failure_never_falls_back_to_openrouter_chutes_or_aws(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": DEEPSEEK_TOKEN,
                                     "OPENROUTER_API_KEY": "or-token", "CHUTES_API_KEY": "chutes-token"}), \
                patch.object(transport, "post_json", side_effect=OpenModelHTTPError(
                    "OPEN_MODEL_SERVICE_UNAVAILABLE")) as post, \
                patch.object(transport, "_new_client") as sdk, \
                patch.object(transport, "_bindings", side_effect=AssertionError("legacy fallback")), \
                patch.object(transport, "chutes_bindings", side_effect=AssertionError("Chutes fallback")):
            with self.assertRaises(transport.OpenModelError) as error:
                asyncio.run(transport.call_model(FLASH, "instructions", "prompt", route="learning"))
        self.assertEqual(error.exception.code, "OPEN_MODEL_SERVICE_UNAVAILABLE")
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "https://api.deepseek.com/chat/completions")
        sdk.assert_not_called()

    def test_catalog_policy_metadata_never_exposes_credential_values_or_remote_config(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": DEEPSEEK_TOKEN,
                                     "OPENROUTER_API_KEY": "private-or-token",
                                     "CHUTES_API_KEY": "private-chutes-token"}):
            serialized = json.dumps(transport.catalog_status(route="learning"), ensure_ascii=False)
        for secret in (DEEPSEEK_TOKEN, "private-or-token", "private-chutes-token",
                       '"endpoint_url"', '"api_key"', '"model_id"'):
            self.assertNotIn(secret, serialized)

    def test_learning_route_respects_shared_disabled_feature(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": DEEPSEEK_TOKEN,
                                     "EDURECOVER_OPEN_MODELS_ENABLED": "false"}), \
                patch.object(transport, "post_json") as post:
            rows = transport.catalog_status(route="learning")
            for key in OFFICIAL_MODELS:
                self.assertEqual(next(row for row in rows if row["model_key"] == key)["readiness_state"],
                                 "disabled")
            with self.assertRaises(transport.OpenModelError) as error:
                asyncio.run(transport.call_model(FLASH, "instructions", "prompt", route="learning"))
            self.assertEqual(error.exception.code, "OPEN_MODEL_DISABLED")
        post.assert_not_called()


class TrainingModelAPITests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, ENV, clear=True)
        env.start()
        self.addCleanup(env.stop)
        app = FastAPI()
        app.include_router(owner_auth_router)
        app.include_router(api.router)
        self.client = TestClient(app, base_url="https://testserver")
        self.addCleanup(self.client.close)
        self.auth = ("owner-test", "test-password-only")

    def test_new_routes_require_owner_authentication(self):
        with patch.object(transport, "call_model", new_callable=AsyncMock) as invoke:
            for path in ("/owner/open-models/learning", "/owner/open-models/learning/catalog"):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers["location"], "/owner/login")
            response = self.client.post("/owner/open-models/learning/call", json=request())
            self.assertEqual(response.status_code, 401)
        invoke.assert_not_awaited()

    def test_authorized_catalog_separates_owner_permissions_and_configuration(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": DEEPSEEK_TOKEN}), \
                patch.object(transport, "call_model", new_callable=AsyncMock) as invoke:
            response = self.client.get("/owner/open-models/learning/catalog", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        data = response.json()
        self.assertEqual(data["readiness_basis"], "configuration_only_not_live_provider_success")
        row = next(row for row in data["models"] if row["model_key"] == FLASH)
        self.assertTrue(row["permitted"])
        self.assertEqual(row["permission_state"], "ALLOWED")
        self.assertEqual(row["readiness_state"], "configured_not_verified")
        self.assertIn("policy_scope", row)
        self.assertNotIn(DEEPSEEK_TOKEN, response.text)
        invoke.assert_not_awaited()

    def test_direct_call_uses_the_reviewed_route_without_default_output_cap(self):
        with patch.object(transport, "call_model", new_callable=AsyncMock, return_value="답변") as invoke:
            response = self.client.post("/owner/open-models/learning/call", json=request(), auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "답변")
        invoke.assert_awaited_once_with(FLASH, api._DIRECT_INSTRUCTIONS, "시험용 질문",
                                       max_tokens=None, route="learning")

    def test_attachment_text_is_reused_without_changing_route(self):
        attachment = {"filename": "study.txt", "content_type": "text/plain",
                      "data": base64.b64encode("질문과 함께 보낼 문서".encode()).decode()}
        with patch.object(transport, "call_model", new_callable=AsyncMock, return_value="답변") as invoke:
            response = self.client.post("/owner/open-models/learning/call",
                                        json=request(attachments=[attachment]), auth=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("질문과 함께 보낼 문서", invoke.await_args.args[2])
        self.assertEqual(invoke.await_args.kwargs, {"max_tokens": None, "route": "learning"})

    def test_explicit_authorization_and_provider_holds_precede_any_inference(self):
        with patch.object(transport, "call_model", new_callable=AsyncMock) as invoke:
            for value in (False, 1, "true", None):
                response = self.client.post("/owner/open-models/learning/call",
                                            json=request(explicit_authorization=value), auth=self.auth)
                self.assertEqual(response.status_code, 422)
            for controls in ({"EDURECOVER_AI_HOLD": "true"},
                             {"EDURECOVER_ALLOWED_PROVIDERS": "qwen"},
                             {"EDURECOVER_BLOCKED_PROVIDERS": "deepseek"},
                             {"EDURECOVER_RESTRICTED_PROVIDERS": "deepseek"}):
                with self.subTest(controls=controls), patch.dict(os.environ, controls):
                    response = self.client.post("/owner/open-models/learning/call",
                                                json=request(), auth=self.auth)
                    self.assertEqual(response.status_code, 423)
                    self.assertEqual(response.json()["detail"]["code"], "OPEN_MODEL_POLICY_HELD")
        invoke.assert_not_awaited()

    def test_owner_request_cannot_replace_credential_endpoint_or_route(self):
        with patch.object(transport, "call_model", new_callable=AsyncMock) as invoke:
            for field in ("route", "endpoint_url", "api_key_env", "api_key", "model_id"):
                response = self.client.post("/owner/open-models/learning/call",
                                            json=request(**{field: "private-value"}), auth=self.auth)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("private-value", response.text)
        invoke.assert_not_awaited()

    def test_no_structured_draft_route_is_enabled_implicitly(self):
        response = self.client.post("/owner/open-models/learning/draft", json=request(), auth=self.auth)
        self.assertEqual(response.status_code, 404)
