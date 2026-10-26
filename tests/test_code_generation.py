"""Offline draft contracts: no paid inference, shell, VM, or installation."""
import asyncio
import hashlib
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.services import code_generation as generation
from app.services import code_execution_api as api
from app.services.control_plane import ProviderHeld
from app.services.open_model_transport import OpenModelError


class CodeGenerationTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"EDURECOVER_CODE_GENERATION_ENABLED": "true"})
        self.env.start(); self.addCleanup(self.env.stop)
        self.allowed = patch.object(generation, "assert_provider_allowed")
        self.allowed.start(); self.addCleanup(self.allowed.stop)

    def request(self, **updates):
        return generation.CodeGenerationRequest(**({
            "model_key": "DeepSeek-V3.2", "route": "chutes", "prompt": "합계 코드를 작성하십시오.",
            "files": [{"path": "test_sum.py", "content": "assert 1 + 1 == 2"}],
            "explicit_authorization": True,
        } | updates))

    def generate(self, answer, **updates):
        with patch.object(generation.transport, "call_model", new=AsyncMock(return_value=answer)) as call:
            result = asyncio.run(generation.generate_code(self.request(**updates)))
        return result, call

    def test_default_disabled_blocks_before_inference(self):
        for value in ("", "1", "yes", "false"):
            with patch.dict(os.environ, {"EDURECOVER_CODE_GENERATION_ENABLED": value}), \
                    patch.object(generation.transport, "call_model", new=AsyncMock()) as call:
                with self.assertRaises(OpenModelError) as caught:
                    asyncio.run(generation.generate_code(self.request()))
                self.assertEqual(caught.exception.code, "CODE_GENERATION_DISABLED")
                call.assert_not_awaited()

    def test_one_exact_route_returns_reviewable_files_without_execution(self):
        answer = json.dumps({"summary": "합계 초안", "files": [{"path": "main.py", "content": "print(1 + 1)\n"}]})
        result, call = self.generate(answer)
        call.assert_awaited_once()
        args, kwargs = call.await_args
        self.assertEqual(args[0], "DeepSeek-V3.2")
        self.assertEqual(kwargs, {"route": "chutes", "max_tokens": 4096})
        sent = json.loads(args[2]); self.assertEqual(sent["files"], self.request().model_dump()["files"])
        self.assertEqual(result["files"][0]["path"], "test_sum.py")
        self.assertEqual(result["files"][1]["content"], "print(1 + 1)\n")
        self.assertEqual(result["changed_paths"], ["main.py"])
        self.assertEqual(result["manifest"][1]["sha256"], hashlib.sha256(b"print(1 + 1)\n").hexdigest())
        self.assertEqual(result["execution_state"], "not_executed")
        self.assertFalse(result["system_applied"])
        self.assertTrue(result["requires_execution_authorization"])
        self.assertIsNone(result["cost"])

    def test_malformed_response_is_rejected_as_a_whole_without_echoing_code(self):
        responses = [
            '```json\n{"summary":"x","files":[]}\n```',
            '{"summary":"x","summary":"y","files":[]}',
            '{"summary":"x","files":[],"command":"pip install SECRET"}',
            json.dumps({"summary": "x", "files": [{"path": "../app/main.py", "content": "SECRET"}]}),
            json.dumps({"summary": "x", "files": [{"path": ".edurecover/config", "content": "SECRET"}]}),
            json.dumps({"summary": "x", "files": [{"path": "C:/secret", "content": "SECRET"}]}),
            json.dumps({"summary": "x", "files": [{"path": "main.py", "content": "x"}, {"path": "main.py", "content": "y"}]}),
            json.dumps({"summary": "x", "files": [{"path": "main.py", "content": "가" * 50000}]}),
            '{"summary":NaN,"files":[]}',
        ]
        for answer in responses:
            with self.subTest(answer=answer[:50]), self.assertRaises(OpenModelError) as caught:
                self.generate(answer)
            self.assertEqual(caught.exception.code, "CODE_GENERATION_RESPONSE_INVALID")
            self.assertNotIn("SECRET", str(caught.exception))

    def test_combined_project_limits_are_checked_before_returning_any_files(self):
        answer = json.dumps({"summary": "x", "files": [{"path": "new.py", "content": "x"}]})
        existing = [{"path": str(i) + ".py", "content": "x"} for i in range(40)]
        with self.assertRaises(OpenModelError): self.generate(answer, files=existing)

    def test_missing_or_coerced_consent_and_new_connection_options_are_rejected(self):
        for changes in ({"explicit_authorization": False}, {"explicit_authorization": 1},
                        {"explicit_authorization": "true"}, {"endpoint_url": "https://other.invalid"},
                        {"api_key": "SECRET"}, {"route": "aws"}, {"max_tokens": True}, {"prompt": " "}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError): self.request(**changes)

    def test_large_input_fails_before_inference(self):
        with patch.object(generation.transport, "MAX_INPUT_BYTES", 100), \
                patch.object(generation.transport, "call_model", new=AsyncMock()) as call:
            with self.assertRaises(OpenModelError) as caught:
                asyncio.run(generation.generate_code(self.request()))
            self.assertEqual(caught.exception.http_status, 413); call.assert_not_awaited()

    def test_provider_error_is_not_retried_or_sent_to_another_model(self):
        with patch.object(generation.transport, "call_model", new=AsyncMock(side_effect=OpenModelError("잔액 부족", "OPEN_MODEL_INSUFFICIENT_BALANCE", 402))) as call:
            with self.assertRaises(OpenModelError) as caught:
                asyncio.run(generation.generate_code(self.request()))
            self.assertEqual(caught.exception.http_status, 402); call.assert_awaited_once()


class CodeGenerationAPITests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"EDURECOVER_CODE_GENERATION_ENABLED": "true",
            "EDURECOVER_OWNER_USER": "owner-test", "EDURECOVER_OWNER_PASSWORD": "test-only"})
        self.env.start(); self.addCleanup(self.env.stop)
        app = FastAPI(); app.include_router(api.router)
        self.client = TestClient(app, base_url="https://testserver"); self.addCleanup(self.client.close)
        self.auth = ("owner-test", "test-only")
        self.payload = {"model_key": "DeepSeek-V3.2", "route": "chutes", "prompt": "합계 코드",
                        "files": [], "explicit_authorization": True}

    def post(self, **kwargs):
        return self.client.post("/owner/code-workspace/generate", auth=self.auth,
                                json=self.payload, **kwargs)

    def test_anonymous_and_missing_consent_never_contact_provider(self):
        with patch.object(api, "generate_code", new=AsyncMock()) as call:
            r = self.client.post("/owner/code-workspace/generate", json=self.payload)
            self.assertEqual(r.status_code, 401)
            self.payload.pop("explicit_authorization")
            self.assertEqual(self.post().status_code, 422); call.assert_not_awaited()

    def test_draft_route_never_creates_vm_or_submits_a_worker(self):
        with patch.object(api, "generate_code", new=AsyncMock(return_value={"execution_state": "not_executed"})), \
                patch.object(api.runtime, "RailwayCodeSession") as session, patch.object(api._pool, "submit") as submit:
            r = self.post(); self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["execution_state"], "not_executed")
            session.assert_not_called(); submit.assert_not_called()
            self.assertEqual(r.headers["cache-control"], "no-store")

    def test_error_body_never_exposes_provider_exception_or_submitted_code(self):
        with patch.object(api, "generate_code", new=AsyncMock(side_effect=RuntimeError("SECRET"))):
            r = self.post(); self.assertEqual(r.status_code, 502); self.assertNotIn("SECRET", r.text)
        with patch.object(api, "generate_code", new=AsyncMock(side_effect=ProviderHeld("held"))):
            self.assertEqual(self.post().status_code, 423)

    def test_private_validation_and_disabled_status(self):
        self.payload["files"] = [{"path": "../SECRET", "content": "PRIVATE"}]
        r = self.post(); self.assertEqual(r.status_code, 422)
        self.assertNotIn("PRIVATE", r.text); self.assertNotIn("SECRET", r.text)
        with patch.dict(os.environ, {"EDURECOVER_CODE_GENERATION_ENABLED": "false"}), \
                patch.object(api.runtime, "runtime_status", return_value={"configured": False}):
            r = self.client.get("/owner/code-workspace/status", auth=self.auth)
            self.assertFalse(r.json()["external_generation"]["enabled"])
            self.assertTrue(r.json()["external_generation"]["requires_separate_execution_authorization"])
