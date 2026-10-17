"""Separate Chutes route contracts; every inference request is mocked offline."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import chutes_direct as chutes
from app.services import open_model_api as api
from app.services import open_model_transport as transport
from app.services.open_model_http import OpenModelHTTPError
from app.services.owner_basic_auth import owner_auth_router


MODEL = "DeepSeek-V3.2"
REMOTE = "deepseek-ai/DeepSeek-V3.2-TEE"
CHUTES_TOKEN = "private-chutes-test-token"
ENV = {
    "EDURECOVER_OPEN_MODELS_ENABLED": "true",
    "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": "{}",
    "EDURECOVER_OWNER_USER": "owner-test",
    "EDURECOVER_OWNER_PASSWORD": "test-password-only",
    "EDURECOVER_ALLOWED_PROVIDERS": "deepseek",
    "EDURECOVER_AI_HOLD": "false",
}


def output(text="직접 응답"):
    return {"choices": [{"message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}]}


def request(**changes):
    return dict({"model_key": MODEL, "prompt": "질문입니다.",
                 "explicit_authorization": True}, **changes)


class ChutesTransportTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, ENV, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_catalog_exposes_only_exact_verified_model_and_missing_key(self):
        with patch.object(transport, "post_json") as post:
            rows = transport.catalog_status(route="chutes")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["model_key"], MODEL)
        self.assertEqual(rows[0]["family"], "deepseek")
        self.assertEqual(rows[0]["display_name"], "DeepSeek-V3.2 · Chutes 직접")
        self.assertEqual(rows[0]["route_label"], "Chutes 직접 API · OpenRouter 미사용")
        self.assertEqual(rows[0]["required_api_key_env"], "CHUTES_API_KEY")
        self.assertEqual(rows[0]["readiness_state"], "missing_credentials")
        self.assertFalse(rows[0]["configured"])
        self.assertFalse(rows[0]["enabled"])
        self.assertNotIn("supports_images", rows[0])
        self.assertNotIn(REMOTE, json.dumps(rows))
        self.assertNotIn("llm.chutes.ai", json.dumps(rows))
        post.assert_not_called()

    def test_openrouter_key_cannot_supply_chutes_credentials(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "private-or-token",
                                     "DEEPSEEK_API_KEY": "private-deepseek-token"}), \
                patch.object(transport, "post_json") as post:
            self.assertEqual(transport.catalog_status(route="chutes")[0]["readiness_state"],
                             "missing_credentials")
            with self.assertRaises(transport.OpenModelError) as error:
                asyncio.run(transport.call_model(MODEL, "instructions", "prompt", route="chutes"))
            self.assertEqual(error.exception.code, "OPEN_MODEL_CREDENTIALS_UNAVAILABLE")
        post.assert_not_called()

    def test_direct_route_ignores_legacy_bindings_flags_and_credentials(self):
        poisoned_legacy = {
            "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": "malformed legacy JSON",
            "EDURECOVER_DEEPSEEK_DIRECT_ENABLED": "malformed",
            "EDURECOVER_GPT6_LUNA_AZURE_ENABLED": "malformed",
            "EDURECOVER_VERIFIED_MODEL_ROUTES_ENABLED": "malformed",
            "EDURECOVER_OPEN_DEEPSEEK_MODEL_KEY": MODEL,
            "OPENROUTER_API_KEY": "private-or-token",
            "DEEPSEEK_API_KEY": "private-deepseek-token",
            "CHUTES_API_KEY": CHUTES_TOKEN,
        }
        with patch.dict(os.environ, poisoned_legacy), \
                patch.object(transport, "_bindings", side_effect=AssertionError("legacy route read")), \
                patch.object(transport, "post_json", return_value=output()) as post, \
                patch.object(transport, "_new_client") as sdk:
            row = transport.catalog_status(route="chutes")[0]
            self.assertEqual(row["readiness_state"], "configured_not_verified")
            self.assertTrue(row["enabled"])
            answer = asyncio.run(transport.call_model(
                MODEL, "instructions", "prompt", max_tokens=None, route="chutes"))
        self.assertEqual(answer, "직접 응답")
        post.assert_called_once()
        endpoint, payload, token = post.call_args.args
        self.assertEqual(endpoint, "https://llm.chutes.ai/v1/chat/completions")
        self.assertEqual(token, CHUTES_TOKEN)
        self.assertEqual(payload["model"], REMOTE)
        self.assertNotIn("provider", payload)
        self.assertNotIn("max_tokens", payload)
        self.assertFalse(payload["stream"])
        sdk.assert_not_called()

    def test_original_openrouter_and_deepseek_bindings_and_env_are_unchanged(self):
        with patch.dict(os.environ, {
            "EDURECOVER_DEEPSEEK_DIRECT_ENABLED": "true",
            "EDURECOVER_GPT6_LUNA_AZURE_ENABLED": "true",
            "OPENROUTER_API_KEY": "private-or-token",
            "DEEPSEEK_API_KEY": "private-deepseek-token",
            "CHUTES_API_KEY": CHUTES_TOKEN,
        }):
            env_before = dict(os.environ)
            bindings_before = transport._bindings()
            catalogue_before = transport.catalog_status()
            transport.catalog_status(route="chutes")
            with patch.object(transport, "post_json", return_value=output()):
                asyncio.run(transport.call_model(MODEL, "instructions", "prompt", route="chutes"))
            self.assertEqual(transport._bindings(), bindings_before)
            self.assertEqual(transport.catalog_status(), catalogue_before)
            self.assertEqual(dict(os.environ), env_before)
            self.assertEqual(bindings_before["GPT-6-Luna"].endpoint_url,
                             transport.OPENROUTER_CHAT_ENDPOINT)
            self.assertEqual(bindings_before["DeepSeek-V4.1-Flash"].endpoint_url,
                             transport.DEEPSEEK_CHAT_ENDPOINT)
            self.assertNotIn(MODEL, bindings_before)

    def test_chutes_key_never_enables_or_reroutes_the_ordinary_call(self):
        with patch.dict(os.environ, {"CHUTES_API_KEY": CHUTES_TOKEN}), \
                patch.object(transport, "post_json") as post:
            row = next(row for row in transport.catalog_status() if row["model_key"] == MODEL)
            self.assertEqual(row["readiness_state"], "not_configured")
            with self.assertRaises(transport.OpenModelError) as error:
                asyncio.run(transport.call_model(MODEL, "instructions", "prompt"))
            self.assertEqual(error.exception.code, "OPEN_MODEL_NOT_CONFIGURED")
        post.assert_not_called()

    def test_no_automatic_fallback_after_chutes_failure(self):
        with patch.dict(os.environ, {"CHUTES_API_KEY": CHUTES_TOKEN,
                                     "OPENROUTER_API_KEY": "or-token"}), \
                patch.object(transport, "post_json", side_effect=OpenModelHTTPError(
                    "OPEN_MODEL_SERVICE_UNAVAILABLE")) as post, \
                patch.object(transport, "_bindings", side_effect=AssertionError("fallback")), \
                patch.object(transport, "_new_client") as sdk:
            with self.assertRaises(transport.OpenModelError) as error:
                asyncio.run(transport.call_model(MODEL, "instructions", "prompt", route="chutes"))
        self.assertEqual(error.exception.code, "OPEN_MODEL_SERVICE_UNAVAILABLE")
        self.assertNotIn("private upstream body", str(error.exception))
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], chutes.CHUTES_CHAT_ENDPOINT)
        sdk.assert_not_called()

    def test_other_registered_models_are_not_aliased_to_the_selected_model(self):
        with patch.dict(os.environ, {"CHUTES_API_KEY": CHUTES_TOKEN}), \
                patch.object(transport, "post_json") as post:
            for key in ("DeepSeek-V4.1-Flash", "Qwen3.6-27B", "GPT-6-Luna", "Claude-Haiku-5.5"):
                with self.subTest(key=key), self.assertRaises(transport.OpenModelError) as error:
                    asyncio.run(transport.call_model(key, "instructions", "prompt", route="chutes"))
                self.assertEqual(error.exception.code, "OPEN_MODEL_NOT_CONFIGURED")
        post.assert_not_called()

    def test_unknown_route_fails_closed_instead_of_selecting_default(self):
        with patch.object(transport, "_bindings", side_effect=AssertionError("default route")), \
                patch.object(transport, "post_json") as post:
            for route in ("Chutes", "chute", "", "openrouter"):
                with self.subTest(route=route):
                    with self.assertRaises(transport.OpenModelError) as error:
                        transport.catalog_status(route=route)
                    self.assertEqual(error.exception.code, "OPEN_MODEL_ROUTE_UNKNOWN")
                    with self.assertRaises(transport.OpenModelError) as error:
                        asyncio.run(transport.call_model(MODEL, "instructions", "prompt", route=route))
                    self.assertEqual(error.exception.code, "OPEN_MODEL_ROUTE_UNKNOWN")
        post.assert_not_called()

    def test_shared_feature_flag_and_unverified_images_remain_enforced(self):
        with patch.dict(os.environ, {"CHUTES_API_KEY": CHUTES_TOKEN,
                                     "EDURECOVER_OPEN_MODELS_ENABLED": "false"}), \
                patch.object(transport, "post_json") as post:
            self.assertEqual(transport.catalog_status(route="chutes")[0]["readiness_state"], "disabled")
            with self.assertRaises(transport.OpenModelError) as error:
                asyncio.run(transport.call_model(MODEL, "instructions", "prompt", route="chutes"))
            self.assertEqual(error.exception.code, "OPEN_MODEL_DISABLED")
        post.assert_not_called()
        image = {"mime_type": "image/png", "data": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()}
        with patch.dict(os.environ, {"CHUTES_API_KEY": CHUTES_TOKEN}), \
                patch.object(transport, "post_json") as post:
            with self.assertRaises(transport.OpenModelError) as error:
                asyncio.run(transport.call_model(MODEL, "instructions", "prompt",
                                                images=[image], route="chutes"))
            self.assertEqual(error.exception.code, "OPEN_MODEL_IMAGE_UNSUPPORTED")
        post.assert_not_called()


class ChutesAPITests(unittest.TestCase):
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

    def test_every_new_route_retains_owner_authentication(self):
        with patch.object(transport, "call_model", new_callable=AsyncMock) as invoke:
            for path in ("/owner/open-models/chutes", "/owner/open-models/chutes/catalog"):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers["location"], "/owner/login")
            response = self.client.post("/owner/open-models/chutes/call", json=request())
            self.assertEqual(response.status_code, 401)
        invoke.assert_not_awaited()

    def test_catalogue_reports_credentials_and_permission_separately_without_inference(self):
        with patch.object(transport, "call_model", new_callable=AsyncMock) as invoke:
            response = self.client.get("/owner/open-models/chutes/catalog", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["readiness_basis"], "configuration_only_not_live_provider_success")
        row = data["models"][0]
        self.assertEqual(row["permission_state"], "ALLOWED")
        self.assertTrue(row["permitted"])
        self.assertEqual(row["readiness_state"], "missing_credentials")
        self.assertEqual(response.headers["cache-control"], "no-store")
        invoke.assert_not_awaited()

    def test_authorized_call_uses_only_explicit_chutes_route_and_has_no_app_token_cap(self):
        with patch.object(transport, "call_model", new_callable=AsyncMock, return_value="답변") as invoke:
            response = self.client.post("/owner/open-models/chutes/call",
                                        json=request(), auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "답변")
        invoke.assert_awaited_once_with(MODEL, api._DIRECT_INSTRUCTIONS, "질문입니다.",
                                       max_tokens=None, route="chutes")
        self.assertNotIn("CHUTES_API_KEY", response.text)
        self.assertNotIn(REMOTE, response.text)

    def test_text_file_extraction_is_reused_in_the_separate_direct_route(self):
        attachment = {"filename": "note.txt", "content_type": "text/plain",
                      "data": base64.b64encode("첨부한 한국어 자료".encode()).decode()}
        with patch.object(transport, "call_model", new_callable=AsyncMock, return_value="답변") as invoke:
            response = self.client.post("/owner/open-models/chutes/call",
                                        json=request(attachments=[attachment]), auth=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("첨부한 한국어 자료", invoke.await_args.args[2])
        self.assertEqual(invoke.await_args.kwargs, {"max_tokens": None, "route": "chutes"})

    def test_explicit_authorization_and_provider_holds_precede_inference(self):
        with patch.object(transport, "call_model", new_callable=AsyncMock) as invoke:
            for value in (False, 1, "true", None):
                response = self.client.post("/owner/open-models/chutes/call",
                                            json=request(explicit_authorization=value), auth=self.auth)
                self.assertEqual(response.status_code, 422)
            for controls in ({"EDURECOVER_AI_HOLD": "true"},
                             {"EDURECOVER_ALLOWED_PROVIDERS": "qwen"},
                             {"EDURECOVER_BLOCKED_PROVIDERS": "deepseek"},
                             {"EDURECOVER_RESTRICTED_PROVIDERS": "deepseek"}):
                with self.subTest(controls=controls), patch.dict(os.environ, controls):
                    response = self.client.post("/owner/open-models/chutes/call",
                                                json=request(), auth=self.auth)
                    self.assertEqual(response.status_code, 423)
                    self.assertEqual(response.json()["detail"]["code"], "OPEN_MODEL_POLICY_HELD")
        invoke.assert_not_awaited()

    def test_request_cannot_override_route_endpoint_identifier_or_credential(self):
        with patch.object(transport, "call_model", new_callable=AsyncMock) as invoke:
            for field in ("route", "endpoint_url", "model_id", "api_key_env", "api_key"):
                with self.subTest(field=field):
                    response = self.client.post("/owner/open-models/chutes/call",
                                                json=request(**{field: "private-submit-value"}), auth=self.auth)
                    self.assertEqual(response.status_code, 422)
                    self.assertNotIn("private-submit-value", response.text)
        invoke.assert_not_awaited()

    def test_missing_key_is_not_presented_as_a_successful_model_response(self):
        with patch.object(transport, "post_json") as post:
            response = self.client.post("/owner/open-models/chutes/call", json=request(), auth=self.auth)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "OPEN_MODEL_CREDENTIALS_UNAVAILABLE")
        self.assertNotIn("answer", response.json())
        post.assert_not_called()

    def test_no_separate_structured_draft_route_is_exposed(self):
        response = self.client.post("/owner/open-models/chutes/draft", json=request(), auth=self.auth)
        self.assertEqual(response.status_code, 404)
