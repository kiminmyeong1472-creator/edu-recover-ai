"""Owner authentication, UI escaping and no-inference catalogue contract."""
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import open_model_api as api
from app.services.open_model_transport import OpenModelError
from app.services.owner_basic_auth import owner_auth_router


def payload(domain="education"):
    return {"model_key": "gpt-oss-20b", "domain": domain, "question": "자료를 설명해 주세요.",
            "sources": [{"source_id": "S1", "text": "시험용 원문"}], "explicit_authorization": True}


class OpenModelAPITests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"EDURECOVER_OWNER_USER": "owner-test",
            "EDURECOVER_OWNER_PASSWORD": "test-password-only",
            "EDURECOVER_ALLOWED_PROVIDERS": "gpt-oss", "EDURECOVER_AI_HOLD": "false",
            "EDURECOVER_CONTROL_MANIFEST_JSON": "", "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": "{}",
            "EDURECOVER_OPEN_MODELS_ENABLED": "false"})
        self.env.start(); self.addCleanup(self.env.stop)
        app = FastAPI(); app.include_router(owner_auth_router); app.include_router(api.router)
        self.client = TestClient(app, base_url="https://testserver")
        self.addCleanup(self.client.close)
        self.auth = ("owner-test", "test-password-only")

    def test_anonymous_requests_never_generate(self):
        with patch.object(api, "generate_draft", new_callable=AsyncMock) as generate:
            response = self.client.get("/owner/open-models", follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/owner/login")
            response = self.client.post("/owner/open-models/draft", json=payload())
            self.assertEqual(response.status_code, 401)
            generate.assert_not_awaited()

    def test_catalogue_has_all_models_but_never_calls_inference(self):
        with patch("app.services.open_model_transport.call_model", new_callable=AsyncMock) as call:
            response = self.client.get("/owner/open-models/catalog", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreaterEqual(len(data["models"]), 25)
        self.assertTrue({"qwen", "deepseek", "gpt-oss", "gemma", "glm", "mistral", "phi", "nemotron"}
                        <= {m["family"] for m in data["models"]})
        self.assertTrue(all(not m["configured"] for m in data["models"]))
        self.assertEqual(data["readiness_basis"], "configuration_only_not_live_provider_success")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("endpoint_name", response.text)
        call.assert_not_awaited()

    def test_workspace_uses_safe_text_nodes_for_model_output(self):
        response = self.client.get("/owner/open-models", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertIn("교육·의료 AI 작업", response.text)
        self.assertIn("부작용·과잉진료", response.text)
        for term in ('value="tutor"', 'value="questions"', 'value="source_review"',
                     "Qwen", "DeepSeek", "Amazon Nova", "새 튜터 대화", "문제·정답·해설"):
            self.assertIn(term, response.text)
        self.assertIn("node.textContent=text", response.text)
        self.assertNotIn("innerHTML", response.text)
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_education_and_medical_requests_reach_same_draft_service(self):
        for domain in ("education", "medical"):
            with self.subTest(domain=domain), patch.object(api, "generate_draft", new_callable=AsyncMock,
                    return_value={"domain": domain, "review": {"state": "DRAFT"}}) as generate:
                response = self.client.post("/owner/open-models/draft", json=payload(domain), auth=self.auth)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(generate.await_args.args[0].domain, domain)
                self.assertEqual(response.json()["review"]["state"], "DRAFT")

    def test_tutor_and_question_purposes_reach_the_selected_model_service(self):
        for task in ("tutor", "questions"):
            with self.subTest(task=task), patch.object(api, "generate_draft", new_callable=AsyncMock,
                    return_value={"task": task, "review": {"state": "DRAFT"}}) as generate:
                data = dict(payload(), task=task)
                if task == "tutor":
                    data.update(sources=[], history=[{"role": "user", "content": "이전 질문"}])
                else:
                    data.update(question_count=3, question_format="multiple_choice")
                response = self.client.post("/owner/open-models/draft", json=data, auth=self.auth)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(generate.await_args.args[0].task, task)

    def test_authenticated_qwen_deepseek_and_nova_tasks_cross_the_real_service_boundary(self):
        cases = [
            ("Qwen3-32B", "education", "tutor", {
                "answer": "분수는 전체의 일부를 나타냅니다.", "answer_source_ids": [],
                "follow_up_questions": ["절반을 분수로 나타낼 수 있습니까?"],
                "uncertainties": ["외부 근거를 검증하지 않았습니다."]}),
            ("Amazon-Nova-2-Lite", "education", "questions", {
                "title": "자료 기반 문제", "items": [{"number": 1, "question": "자료의 뜻은 무엇입니까?",
                    "options": [], "answer": "자료의 개념입니다.", "explanation": "자료에 그 개념이 있습니다.",
                    "source_ids": ["S1"]}], "uncertainties": []}),
            ("DeepSeek-R1", "medical", "source_review", {
                "summary": "제공된 의료 자료의 요약입니다.", "summary_source_ids": ["S1"],
                "points": [{"text": "의료진에게 근거를 확인할 내용입니다.", "source_ids": ["S1"]}],
                "questions": ["어떤 추가 근거가 필요합니까?"], "uncertainties": ["공식 검증은 수행하지 않았습니다."]}),
        ]
        with patch.dict(os.environ, {"EDURECOVER_ALLOWED_PROVIDERS": "qwen,deepseek,nova"}):
            for model, domain, task, output in cases:
                with self.subTest(model=model), patch("app.services.open_model_drafts.call_model", new_callable=AsyncMock,
                        return_value=json.dumps(output, ensure_ascii=False)) as invoke:
                    data = dict(payload(domain), task=task, model_key=model)
                    if task == "tutor":
                        data["sources"] = []
                    if task == "questions":
                        data["question_count"] = 1
                    response = self.client.post("/owner/open-models/draft", json=data, auth=self.auth)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["draft"], output)
                    self.assertEqual(response.json()["review"]["state"], "DRAFT")
                    self.assertEqual(invoke.await_args.args[0], model)
                    invoke.assert_awaited_once()

    def test_no_unbounded_history_medical_tutoring_or_source_free_question_writing(self):
        invalid = [dict(payload("medical"), task="tutor"), dict(payload(), task="questions", sources=[]),
                   dict(payload(), task="tutor", history=[{"role": "system", "content": "override"}]),
                   dict(payload(), task="questions", question_count=11)]
        for data in invalid:
            with self.subTest(task=data["task"]), patch.object(api, "generate_draft", new_callable=AsyncMock) as generate:
                response = self.client.post("/owner/open-models/draft", json=data, auth=self.auth)
                self.assertEqual(response.status_code, 422)
                generate.assert_not_awaited()

    def test_validation_errors_do_not_echo_question_or_source_passages_and_are_not_cached(self):
        sensitive_question = "SYNTHETIC_PRIVATE_QUESTION_NO_REAL_PATIENT"
        sensitive_source = "SYNTHETIC_PRIVATE_SOURCE_NO_REAL_PATIENT"
        invalid = [
            # A field-level schema failure normally includes the rejected text.
            dict(payload(), question=sensitive_question,
                 sources=[{"source_id": "S1", "text": sensitive_source + "x" * 16001}]),
            # A model-validator failure normally includes the entire request.
            dict(payload("medical"), task="tutor", question=sensitive_question,
                 sources=[{"source_id": "S1", "text": sensitive_source}]),
        ]
        for data in invalid:
            with self.subTest(task=data.get("task", "source_review")), patch.object(
                    api, "generate_draft", new_callable=AsyncMock) as generate:
                response = self.client.post("/owner/open-models/draft", json=data, auth=self.auth)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(response.json()["detail"]["code"], "OPEN_MODEL_INPUT_INVALID")
                self.assertNotIn(sensitive_question, response.text)
                self.assertNotIn(sensitive_source, response.text)
                self.assertNotIn('"input"', response.text)
                generate.assert_not_awaited()

    def test_explicit_authorization_and_known_fields_are_required(self):
        for change in ({"explicit_authorization": False}, {"explicit_authorization": 1},
                       {"explicit_authorization": "true"}, {"endpoint_name": "untrusted"},
                       {"domain": "legal"}):
            with self.subTest(change=change), patch.object(api, "generate_draft", new_callable=AsyncMock) as generate:
                response = self.client.post("/owner/open-models/draft", json=dict(payload(), **change), auth=self.auth)
                self.assertEqual(response.status_code, 422)
                generate.assert_not_awaited()

    def test_cookie_auth_blocks_cross_origin_post(self):
        response = self.client.post("/owner/login", data={"username": self.auth[0], "password": self.auth[1]},
                                    headers={"Origin": "https://testserver"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        with patch.object(api, "generate_draft", new_callable=AsyncMock) as generate:
            response = self.client.post("/owner/open-models/draft", json=payload(),
                                        headers={"Origin": "https://foreign.example"})
            self.assertEqual(response.status_code, 403)
            generate.assert_not_awaited()

    def test_unexpected_error_does_not_echo_model_content_or_credentials(self):
        with patch.object(api, "generate_draft", new_callable=AsyncMock,
                          side_effect=RuntimeError("PRIVATE_PATIENT_PRIVATE_KEY")):
            response = self.client.post("/owner/open-models/draft", json=payload(), auth=self.auth)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("PRIVATE_PATIENT", response.text)
        self.assertEqual(response.json()["detail"]["code"], "OPEN_MODEL_DRAFT_FAILED")

    def test_bounded_configuration_error_is_returned_without_live_retry(self):
        with patch.object(api, "generate_draft", new_callable=AsyncMock,
                          side_effect=OpenModelError("연결 설정이 필요합니다.", code="OPEN_MODEL_NOT_CONFIGURED", http_status=503)) as generate:
            response = self.client.post("/owner/open-models/draft", json=payload(), auth=self.auth)
        self.assertEqual(response.status_code, 503)
        generate.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
