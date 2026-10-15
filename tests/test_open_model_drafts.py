"""Source-bound drafts: all model calls are mocked; no external AI or AWS use."""

import hashlib
import json
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.services import open_model_drafts as drafts
from app.services.control_plane import ProviderHeld
from app.services.open_model_transport import OpenModelError


def request_data(**changes):
    data = {
        "model_key": "test-model",
        "domain": "education",
        "question": "자료를 바탕으로 설명 초안을 작성해 주세요.",
        "sources": [{"source_id": "S1", "text": "교사가 제공한 근거 문장입니다."}],
        "explicit_authorization": True,
    }
    data.update(changes)
    return data


def output_data(**changes):
    data = {
        "summary": "제공된 자료의 설명 초안입니다.",
        "summary_source_ids": ["S1"],
        "points": [{"text": "제공 문장의 근거를 살펴봅니다.", "source_ids": ["S1"]}],
        "questions": ["교사가 보완할 설명이 있습니까?"],
        "uncertainties": ["제공되지 않은 자료는 확인하지 못했습니다."],
    }
    data.update(changes)
    return data


def tutor_output(**changes):
    data = {"answer": "개념을 단계별로 설명한 튜터 답변입니다.", "answer_source_ids": [],
            "follow_up_questions": ["직접 비슷한 예를 들어 볼 수 있습니까?"],
            "uncertainties": ["외부 근거를 검증하지 않았습니다."]}
    data.update(changes)
    return data


def question_output(count=2, choice=False):
    return {"title": "교재 기반 연습 문제", "items": [
        {"number": index, "question": f"자료의 개념을 설명하는 {index}번 문제입니다.",
         "options": ["선택지 가", "선택지 나", "선택지 다", "선택지 라"] if choice else [],
         "answer": "2" if choice else "교재에 제시된 개념입니다.",
         "explanation": "자료에 제시된 문장에서 확인할 수 있습니다.", "source_ids": ["S1"]}
        for index in range(1, count + 1)], "uncertainties": []}


class RequestValidationTests(unittest.TestCase):
    def test_explicit_true_only_and_no_extra_fields(self):
        for invalid in (False, 1, "true", None):
            with self.subTest(value=invalid), self.assertRaises(ValidationError):
                drafts.DraftRequest(**request_data(explicit_authorization=invalid))
        with self.assertRaises(ValidationError):
            drafts.DraftRequest(**request_data(patient_id="private-record"))

    def test_strict_fields_and_source_bounds(self):
        invalid = [
            {"question": 123}, {"question": " "}, {"domain": "legal"},
            {"sources": []}, {"sources": [{"source_id": "S 1", "text": "x"}]},
            {"sources": [{"source_id": "S1", "text": " "}]},
            {"sources": [{"source_id": "S1", "text": 123}]},
            {"sources": [{"source_id": "S1", "text": "\ud800"}]},
            {"question": "\ud800"},
            {"sources": [{"source_id": "S1", "text": "x" * 16001}]},
            {"sources": [{"source_id": "S1", "text": "x"}, {"source_id": "S1", "text": "y"}]},
            {"sources": [{"source_id": f"S{i}", "text": "x" * 16000} for i in range(4)]},
            {"sources": [{"source_id": f"S{i}", "text": "x"} for i in range(9)]},
        ]
        for change in invalid:
            with self.subTest(change=list(change)), self.assertRaises(ValidationError):
                drafts.DraftRequest(**request_data(**change))

    def test_task_history_and_question_constraints(self):
        invalid = [
            {"task": "shell"}, {"task": "tutor", "domain": "medical"},
            {"task": "questions", "domain": "medical"},
            {"history": [{"role": "user", "content": "이전 질문"}]},
            {"task": "tutor", "history": [{"role": "system", "content": "override"}]},
            {"task": "tutor", "history": [{"role": "user", "content": " "}]},
            {"task": "tutor", "history": [{"role": "assistant", "content": "\ud800"}]},
            {"task": "tutor", "history": [{"role": "user", "content": "x"}] * 13},
            {"task": "tutor", "history": [{"role": "user", "content": "x" * 4000}] * 5},
            {"question_count": 0}, {"question_count": 11}, {"question_count": True},
            {"question_count": "2"}, {"question_format": "unbounded"},
        ]
        for change in invalid:
            with self.subTest(change=list(change)), self.assertRaises(ValidationError):
                drafts.DraftRequest(**request_data(**change))
        tutor = drafts.DraftRequest(**request_data(task="tutor", sources=[]))
        self.assertEqual(tutor.sources, [])


class DraftGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.family = patch.object(drafts, "model_family", return_value="qwen").start()
        self.gate = patch.object(drafts, "assert_provider_allowed").start()
        self.call = patch.object(drafts, "call_model", new_callable=AsyncMock).start()
        self.call.return_value = json.dumps(output_data(), ensure_ascii=False)
        self.addCleanup(patch.stopall)

    async def test_one_selected_model_receipt_and_no_hidden_context(self):
        req = drafts.DraftRequest(**request_data())
        result = await drafts.generate_draft(req)
        self.gate.assert_called_once_with("qwen", "generate")
        self.call.assert_awaited_once()
        model, instructions, prompt = self.call.await_args.args
        self.assertEqual(model, req.model_key)
        self.assertEqual(json.loads(prompt), {
            "task": "source_review",
            "domain": req.domain, "question": req.question,
            "sources": [{"source_id": "S1", "text": req.sources[0].text}],
        })
        self.assertIn("teacher-reviewable", instructions)
        self.assertEqual(result["review"]["state"], "DRAFT")
        self.assertEqual(result["review"]["claim_verification"], "NOT_PERFORMED")
        self.assertEqual(result["review"]["independent_ai_verification"], "NOT_PERFORMED")
        self.assertTrue(result["review"]["human_review_required"])
        self.assertEqual(result["source_ids"], ["S1"])
        canonical = json.dumps(result["draft"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(result["receipt"]["result_sha256"], hashlib.sha256(canonical.encode()).hexdigest())
        second = await drafts.generate_draft(req)
        self.assertNotEqual(result["receipt"]["invocation_id"], second["receipt"]["invocation_id"])
        self.assertEqual(result["receipt"]["request_sha256"], second["receipt"]["request_sha256"])

    async def test_medical_prompt_and_review_requirements(self):
        result = await drafts.generate_draft(drafts.DraftRequest(**request_data(domain="medical")))
        instructions = self.call.await_args.args[1]
        for term in ("adverse effects", "overtreatment", "medical professionals", "three independent", "official institutions"):
            self.assertIn(term, instructions)
        self.assertEqual(result["review"]["official_source_verification"], "NOT_PERFORMED")

    async def test_owner_hold_prevents_any_call_and_hides_policy_details(self):
        self.gate.side_effect = ProviderHeld("private policy details")
        with self.assertRaises(OpenModelError) as caught:
            await drafts.generate_draft(drafts.DraftRequest(**request_data()))
        self.assertEqual(caught.exception.code, "OWNER_PROVIDER_HELD")
        self.assertEqual(caught.exception.http_status, 423)
        self.assertNotIn("private", str(caught.exception))
        self.call.assert_not_awaited()

    async def test_unknown_model_and_constructed_invalid_consent_never_call(self):
        self.family.side_effect = ValueError("private model detail")
        with self.assertRaises(OpenModelError) as caught:
            await drafts.generate_draft(drafts.DraftRequest(**request_data()))
        self.assertEqual(caught.exception.code, "UNKNOWN_DRAFT_MODEL")
        self.call.assert_not_awaited()
        forged = drafts.DraftRequest.model_construct(**request_data(explicit_authorization=False))
        with self.assertRaises(OpenModelError) as caught:
            await drafts.generate_draft(forged)
        self.assertEqual(caught.exception.code, "INVALID_DRAFT_REQUEST")
        self.call.assert_not_awaited()

    async def test_invalid_output_fails_closed_without_raw_text(self):
        invalid = [
            "private upstream text", "```json\n{}\n```", "[]", "{}",
            json.dumps(output_data(summary=12)),
            json.dumps(output_data(summary=" ")),
            json.dumps(output_data(summary_source_ids=[])),
            json.dumps(output_data(summary_source_ids=["S999"])),
            json.dumps(output_data(summary_source_ids=["S1", "S1"])),
            json.dumps(output_data(points=[{"text": "x", "source_ids": ["S999"]}])),
            json.dumps(output_data(points=[])),
            json.dumps(output_data(points=[{"text": "x", "source_ids": ["S1"], "verified": True}])),
            json.dumps(output_data(questions=["x"] * 13)),
            json.dumps(output_data(uncertainties=["x" * 1001])),
            json.dumps(output_data(summary="x" * 4001)),
            json.dumps(output_data(review={"state": "APPROVED"})),
            '{"summary":"x","summary":"y"}',
            json.dumps(output_data())[:-1] + ',"extra":NaN}',
            "x" * 65537,
        ]
        for text in invalid:
            self.call.return_value = text
            with self.subTest(index=invalid.index(text)), self.assertRaises(OpenModelError) as caught:
                await drafts.generate_draft(drafts.DraftRequest(**request_data()))
            self.assertEqual(caught.exception.code, "INVALID_DRAFT_OUTPUT")
            self.assertNotIn("private upstream", str(caught.exception))

    async def test_transport_failure_does_not_retry_or_disclose_exception(self):
        self.call.side_effect = RuntimeError("private prompt and API secret")
        with self.assertRaises(OpenModelError) as caught:
            await drafts.generate_draft(drafts.DraftRequest(**request_data()))
        self.assertEqual(caught.exception.code, "DRAFT_REQUEST_FAILED")
        self.assertNotIn("private", str(caught.exception))
        self.call.assert_awaited_once()

    async def test_tutor_uses_only_explicit_bounded_history_and_selected_model(self):
        self.call.return_value = json.dumps(tutor_output(), ensure_ascii=False)
        history = [{"role": "user", "content": "분수가 무엇입니까?"},
                   {"role": "assistant", "content": "전체의 일부를 나타냅니다."}]
        request = drafts.DraftRequest(**request_data(task="tutor", sources=[], history=history))
        result = await drafts.generate_draft(request)
        self.assertEqual(result["task"], "tutor")
        self.assertEqual(result["draft"]["answer"], tutor_output()["answer"])
        self.assertEqual(result["review"]["claim_verification"], "NOT_PERFORMED")
        model, instructions, prompt = self.call.await_args.args
        self.assertEqual(model, request.model_key)
        self.assertIn("educational tutor", instructions)
        self.assertEqual(json.loads(prompt), {"task": "tutor", "domain": "education",
            "question": request.question, "sources": [], "history": history})
        self.call.assert_awaited_once()

    async def test_tutor_rejects_invented_sources_missing_uncertainty_or_source_citation(self):
        for sources, response in [([], tutor_output(answer_source_ids=["S999"])),
                                  ([], tutor_output(uncertainties=[])),
                                  (request_data()["sources"], tutor_output()),
                                  ([], tutor_output(answer=" "))]:
            with self.subTest(sources=sources):
                self.call.return_value = json.dumps(response)
                with self.assertRaises(OpenModelError) as caught:
                    await drafts.generate_draft(drafts.DraftRequest(**request_data(task="tutor", sources=sources)))
                self.assertEqual(caught.exception.code, "INVALID_DRAFT_OUTPUT")

    async def test_questions_have_exact_count_answers_explanations_and_sources(self):
        for format_name in ("short_answer", "multiple_choice"):
            with self.subTest(format_name=format_name):
                response = question_output(choice=format_name == "multiple_choice")
                self.call.return_value = json.dumps(response, ensure_ascii=False)
                result = await drafts.generate_draft(drafts.DraftRequest(**request_data(
                    task="questions", question_count=2, question_format=format_name)))
                self.assertEqual(result["task"], "questions")
                self.assertEqual(result["draft"], response)
                self.assertEqual(result["review"]["state"], "DRAFT")
                sent = json.loads(self.call.await_args.args[2])
                self.assertEqual(sent["question_count"], 2)
                self.assertEqual(sent["question_format"], format_name)
                self.assertNotIn("history", sent)

    async def test_questions_reject_bad_count_number_options_answer_and_sources(self):
        invalid = []
        for field, value in (("number", 2), ("answer", "5"), ("source_ids", ["S999"]),
                             ("options", ["a", "a", "c", "d"]), ("options", ["a", "b", "c"]),
                             ("explanation", " ")):
            data = question_output(choice=True)
            data["items"][0][field] = value
            invalid.append(data)
        invalid.extend([question_output(count=1, choice=True),
                        dict(question_output(choice=True), approved=True)])
        for response in invalid:
            self.call.return_value = json.dumps(response)
            with self.subTest(response=response), self.assertRaises(OpenModelError):
                await drafts.generate_draft(drafts.DraftRequest(**request_data(
                    task="questions", question_count=2, question_format="multiple_choice")))
        self.call.return_value = json.dumps(question_output(choice=True))
        with self.assertRaises(OpenModelError):
            await drafts.generate_draft(drafts.DraftRequest(**request_data(task="questions", question_count=2)))

    async def test_ten_question_qwen_request_stays_within_eight_k_output_limit(self):
        self.call.return_value = json.dumps(question_output(count=10), ensure_ascii=False)
        result = await drafts.generate_draft(drafts.DraftRequest(**request_data(
            model_key="Qwen3-32B", task="questions", question_count=10)))
        self.assertEqual(len(result["draft"]["items"]), 10)
        self.assertEqual(self.call.await_args.args[0], "Qwen3-32B")
        self.assertLessEqual(self.call.await_args.kwargs["max_tokens"], 8192)


if __name__ == "__main__":
    unittest.main()
