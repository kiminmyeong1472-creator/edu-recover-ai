"""An optional owner-operated Nano endpoint never becomes a hosted API fallback."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import nemotron_self_hosted as nemotron
from app.services import open_model_api as api
from app.services import open_model_transport as transport
from app.services.open_model_http import OpenModelHTTPError
from app.services.owner_basic_auth import owner_auth_router


ENDPOINT = "https://owner-gpu.edurecover-cloud.com/v1/chat/completions"
PRIVATE_TOKEN = "owner-operated-nano-token"
BASE_ENV = {
    "EDURECOVER_OPEN_MODELS_ENABLED": "true",
    "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": "{}",
    "EDURECOVER_OWNER_USER": "owner-test",
    "EDURECOVER_OWNER_PASSWORD": "test-password-only",
    "EDURECOVER_ALLOWED_PROVIDERS": "nemotron,deepseek",
    "EDURECOVER_AI_HOLD": "false",
    "DEEPSEEK_API_KEY": "dedicated-deepseek-token",
}
REGISTERED = {
    nemotron.ENABLED_ENV: "true", nemotron.CONFIRMED_ENV: "true",
    nemotron.ENDPOINT_ENV: ENDPOINT, nemotron.API_KEY_ENV: PRIVATE_TOKEN,
}


def output():
    return {"choices": [{"message": {"role": "assistant", "content": "NVIDIA 생성 초안"},
                         "finish_reason": "stop"}]}


def nano_row():
    return next(row for row in transport.catalog_status(route="learning")
                if row["model_key"] == nemotron.MODEL_KEY)


class NemotronSelfHostedTransportTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, BASE_ENV, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_disabled_by_default_and_ignores_inactive_draft_registration(self):
        before = transport.catalog_status(route="learning")
        with patch.dict(os.environ, {
            nemotron.ENDPOINT_ENV: "not-a-valid-endpoint",
            nemotron.CONFIRMED_ENV: "malformed",
            nemotron.API_KEY_ENV: PRIVATE_TOKEN,
        }):
            self.assertEqual(transport.catalog_status(route="learning"), before)
            self.assertEqual(nano_row()["readiness_state"], "not_configured")
            with self.assertRaises(transport.OpenModelError) as error:
                asyncio.run(transport.call_model(nemotron.MODEL_KEY, "instructions", "prompt", route="learning"))
            self.assertEqual(error.exception.code, "OPEN_MODEL_NOT_CONFIGURED")

    def test_explicit_registration_sends_exact_bf16_identity_with_dedicated_key(self):
        with patch.dict(os.environ, REGISTERED), \
                patch.object(transport, "_bindings", side_effect=AssertionError("legacy binding read")), \
                patch.object(transport, "chutes_bindings", side_effect=AssertionError("Chutes read")), \
                patch.object(transport, "post_json", return_value=output()) as post:
            answer = asyncio.run(transport.call_model(
                nemotron.MODEL_KEY, "instructions", "prompt", route="learning", max_tokens=None))
            row = nano_row()
        self.assertEqual(answer, "NVIDIA 생성 초안")
        post.assert_called_once()
        endpoint, payload, token = post.call_args.args
        self.assertEqual(endpoint, ENDPOINT)
        self.assertEqual(payload["model"], "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16")
        self.assertEqual(token, PRIVATE_TOKEN)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("provider", payload)
        self.assertTrue(row["enabled"])
        self.assertEqual(row["readiness_state"], "configured_not_verified")
        self.assertEqual(row["route_label"], nemotron.ROUTE_LABEL)
        self.assertFalse(row["supports_images"])

    def test_other_provider_keys_never_substitute_for_dedicated_nemotron_key(self):
        settings = dict(REGISTERED)
        settings.pop(nemotron.API_KEY_ENV)
        settings.update(OPENROUTER_API_KEY="or-token", CHUTES_API_KEY="chutes-token",
                        NVIDIA_API_KEY="nvidia-hosted-token", OPENAI_API_KEY="openai-token")
        with patch.dict(os.environ, settings), patch.object(transport, "post_json") as post:
            self.assertEqual(nano_row()["readiness_state"], "missing_credentials")
            with self.assertRaises(transport.OpenModelError) as error:
                asyncio.run(transport.call_model(nemotron.MODEL_KEY, "instructions", "prompt", route="learning"))
        self.assertEqual(error.exception.code, "OPEN_MODEL_CREDENTIALS_UNAVAILABLE")
        post.assert_not_called()

    def test_owner_operating_confirmation_is_required(self):
        with patch.dict(os.environ, dict(REGISTERED, **{nemotron.CONFIRMED_ENV: "false"})), \
                patch.object(transport, "post_json") as post:
            row = nano_row()
            self.assertEqual(row["readiness_state"], "self_hosting_confirmation_required")
            self.assertFalse(row["enabled"])
            with self.assertRaises(transport.OpenModelError) as error:
                asyncio.run(transport.call_model(nemotron.MODEL_KEY, "instructions", "prompt", route="learning"))
        self.assertEqual(error.exception.code, "OPEN_MODEL_SELF_HOSTING_CONFIRMATION_REQUIRED")
        post.assert_not_called()

    def test_missing_endpoint_keeps_only_nano_not_configured(self):
        with patch.dict(os.environ, dict(REGISTERED, **{nemotron.ENDPOINT_ENV: ""})), \
                patch.object(transport, "post_json") as post:
            self.assertEqual(nano_row()["readiness_state"], "not_configured")
            with self.assertRaises(transport.OpenModelError) as error:
                asyncio.run(transport.call_model(nemotron.MODEL_KEY, "instructions", "prompt", route="learning"))
        self.assertEqual(error.exception.code, "OPEN_MODEL_NOT_CONFIGURED")
        post.assert_not_called()

    def test_invalid_flags_and_endpoints_fail_closed_without_stopping_deepseek(self):
        invalid_settings = [
            {nemotron.ENABLED_ENV: "yes"},
            {nemotron.CONFIRMED_ENV: "yes"},
            {nemotron.ENDPOINT_ENV: "http://owner-gpu.edurecover-cloud.com/v1/chat/completions"},
            {nemotron.ENDPOINT_ENV: "https://owner-gpu.edurecover-cloud.com:8443/v1/chat/completions"},
            {nemotron.ENDPOINT_ENV: "https://owner-gpu.edurecover-cloud.com/v1/completions"},
            {nemotron.ENDPOINT_ENV: "https://owner-gpu.edurecover-cloud.com/x/v1/chat/completions"},
            {nemotron.ENDPOINT_ENV: "https://127.0.0.1/v1/chat/completions"},
            {nemotron.ENDPOINT_ENV: "https://user:password@owner-gpu.edurecover-cloud.com/v1/chat/completions"},
            {nemotron.ENDPOINT_ENV: ENDPOINT + "?secret=hidden"},
        ]
        for invalid in invalid_settings:
            with self.subTest(invalid=invalid), patch.dict(os.environ, dict(REGISTERED, **invalid)), \
                    patch.object(transport, "post_json", return_value=output()) as post:
                row = nano_row()
                self.assertEqual(row["readiness_state"], "configuration_invalid")
                self.assertFalse(row["enabled"])
                with self.assertRaises(transport.OpenModelError) as error:
                    asyncio.run(transport.call_model(nemotron.MODEL_KEY, "instructions", "prompt", route="learning"))
                self.assertEqual(error.exception.code, "OPEN_MODEL_CONFIG_INVALID")
                post.assert_not_called()
                asyncio.run(transport.call_model("DeepSeek-V4.1-Flash", "instructions", "prompt", route="learning"))
                post.assert_called_once()
                self.assertEqual(post.call_args.args[0], transport.DEEPSEEK_CHAT_ENDPOINT)

    def test_known_shared_inference_services_cannot_be_registered_as_self_hosting(self):
        for host in ("openrouter.ai", "www.openrouter.ai", "llm.chutes.ai", "api.nvidia.com",
                     "integrate.api.nvidia.com", "api.deepseek.com", "api.openai.com",
                     "api.together.xyz", "api.deepinfra.com", "api.fireworks.ai"):
            with self.subTest(host=host), patch.dict(os.environ, dict(
                REGISTERED, **{nemotron.ENDPOINT_ENV: f"https://{host}/v1/chat/completions"}
            )), patch.object(transport, "post_json") as post:
                self.assertEqual(nano_row()["readiness_state"], "configuration_invalid")
                with self.assertRaises(transport.OpenModelError) as error:
                    asyncio.run(transport.call_model(nemotron.MODEL_KEY, "instructions", "prompt", route="learning"))
                self.assertEqual(error.exception.code, "OPEN_MODEL_CONFIG_INVALID")
                post.assert_not_called()

    def test_registration_does_not_change_legacy_or_chutes_routes(self):
        with patch.dict(os.environ, {"CHUTES_API_KEY": "chutes-token"}):
            ordinary_before = transport.catalog_status()
            chutes_before = transport.catalog_status(route="chutes")
            with patch.dict(os.environ, REGISTERED):
                self.assertEqual(transport.catalog_status(), ordinary_before)
                self.assertEqual(transport.catalog_status(route="chutes"), chutes_before)
                with patch.object(transport, "post_json") as post:
                    for route in (None, "chutes"):
                        with self.assertRaises(transport.OpenModelError) as error:
                            asyncio.run(transport.call_model(nemotron.MODEL_KEY, "instructions", "prompt", route=route))
                        self.assertEqual(error.exception.code, "OPEN_MODEL_NOT_CONFIGURED")
                    post.assert_not_called()

    def test_catalog_contains_no_private_endpoint_or_credentials(self):
        with patch.dict(os.environ, REGISTERED), patch.object(transport, "post_json") as post:
            serialized = json.dumps(transport.catalog_status(route="learning"), ensure_ascii=False)
        for private in (PRIVATE_TOKEN, ENDPOINT, "owner-gpu.edurecover-cloud.com", '"endpoint_url"', '"model_id"'):
            self.assertNotIn(private, serialized)
        self.assertIn(nemotron.ROUTE_LABEL, serialized)
        post.assert_not_called()

    def test_self_hosted_failure_never_retries_or_falls_back(self):
        with patch.dict(os.environ, REGISTERED), \
                patch.object(transport, "post_json", side_effect=OpenModelHTTPError("OPEN_MODEL_TIMEOUT")) as post, \
                patch.object(transport, "_new_client") as sdk:
            with self.assertRaises(transport.OpenModelError) as error:
                asyncio.run(transport.call_model(nemotron.MODEL_KEY, "instructions", "prompt", route="learning"))
        self.assertEqual(error.exception.code, "OPEN_MODEL_TIMEOUT")
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], ENDPOINT)
        sdk.assert_not_called()

    def test_raw_images_are_rejected_before_network_use(self):
        image = {"mime_type": "image/png", "data": base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode("ascii")}
        with patch.dict(os.environ, REGISTERED), patch.object(transport, "post_json") as post:
            with self.assertRaises(transport.OpenModelError) as error:
                asyncio.run(transport.call_model(nemotron.MODEL_KEY, "instructions", "prompt",
                                                 route="learning", images=[image]))
        self.assertEqual(error.exception.code, "OPEN_MODEL_IMAGE_UNSUPPORTED")
        post.assert_not_called()

    def test_global_call_switch_still_blocks_registered_nano(self):
        with patch.dict(os.environ, dict(REGISTERED, EDURECOVER_OPEN_MODELS_ENABLED="false")), \
                patch.object(transport, "post_json") as post:
            self.assertEqual(nano_row()["readiness_state"], "disabled")
            with self.assertRaises(transport.OpenModelError) as error:
                asyncio.run(transport.call_model(nemotron.MODEL_KEY, "instructions", "prompt", route="learning"))
        self.assertEqual(error.exception.code, "OPEN_MODEL_DISABLED")
        post.assert_not_called()


class NemotronSelfHostedAPITests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, dict(BASE_ENV, **REGISTERED), clear=True)
        env.start()
        self.addCleanup(env.stop)
        app = FastAPI()
        app.include_router(owner_auth_router)
        app.include_router(api.router)
        self.client = TestClient(app, base_url="https://testserver")
        self.addCleanup(self.client.close)
        self.auth = ("owner-test", "test-password-only")
        self.request = {"model_key": nemotron.MODEL_KEY, "prompt": "원문을 검토하십시오.",
                        "explicit_authorization": True}

    def test_owner_authentication_and_explicit_authorization_are_preserved(self):
        with patch.object(transport, "post_json") as post:
            self.assertEqual(self.client.get("/owner/open-models/learning/catalog", follow_redirects=False).status_code, 303)
            self.assertEqual(self.client.post("/owner/open-models/learning/call", json=self.request).status_code, 401)
            response = self.client.post("/owner/open-models/learning/call", auth=self.auth,
                                        json=dict(self.request, explicit_authorization=False))
            self.assertEqual(response.status_code, 422)
        post.assert_not_called()

    def test_family_restriction_and_hold_prevent_nano_network_calls(self):
        for settings in ({"EDURECOVER_ALLOWED_PROVIDERS": "deepseek"}, {"EDURECOVER_AI_HOLD": "true"}):
            with self.subTest(settings=settings), patch.dict(os.environ, settings), \
                    patch.object(transport, "post_json") as post:
                response = self.client.post("/owner/open-models/learning/call", auth=self.auth, json=self.request)
                self.assertEqual(response.status_code, 423)
                post.assert_not_called()

    def test_authorized_call_reuses_document_extraction_and_no_token_ceiling(self):
        request = dict(self.request, attachments=[{
            "filename": "reference.txt", "content_type": "text/plain",
            "data": base64.b64encode("첨부한 교육 자료".encode()).decode("ascii"),
        }])
        with patch.object(transport, "post_json", return_value=output()) as post:
            response = self.client.post("/owner/open-models/learning/call", auth=self.auth, json=request)
        self.assertEqual(response.status_code, 200, response.text)
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], ENDPOINT)
        payload = post.call_args.args[1]
        self.assertIn("첨부한 교육 자료", json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("max_tokens", payload)
