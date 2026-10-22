"""Fictional fixtures only: these are not university or official scoring rubrics."""

from dataclasses import replace
from decimal import Decimal, localcontext
import unittest

from app.services.dreamkeeper_essay import (
    AnswerEvidence, CriterionScore, EngineSourceLink, EssayAnswer, EssayProblem,
    GuidanceProgress, RubricCriterion, ScoreBinding, ScoreHold, TeacherContext,
    TeacherRubric, confirm_score, propose_score, review_confirmation, review_score,
    source_documents, submit_score_proposal, text_hash,
)


class CapturingPort:
    def __init__(self, sources=None):
        self.calls = []
        self.sources = sources or {}

    def read_contract(self):
        return {"sources": self.sources}

    def submit_candidate(self, **kwargs):
        self.calls.append(kwargs)
        return "candidate-1"


class EssayWorkflowTest(unittest.TestCase):
    def setUp(self):
        # These numbers are supplied by an imaginary teacher for this test.
        self.problem = EssayProblem("fictional-problem", 1,
                                    "도서관의 운영 시간을 늘리는 제안을 논증하십시오.", "confirmed")
        self.context = TeacherContext("class-1", 1, "teacher-1",
                                      "학생의 근거와 대안 관점을 구분하여 검토합니다.", self.problem)
        self.answer = EssayAnswer("answer-1", 1, "student-1",
                                  "야간 이용자를 조사해야 한다. 이동 도서관도 대안이다.", "confirmed")
        self.rubric = TeacherRubric(
            "fictional-rubric", 1, self.problem.problem_id, 1,
            (RubricCriterion("perspective", "대안 관점", "creativity", "3"),
             RubricCriterion("reasoning", "주장과 근거의 연결", "argument_validity", "7")),
            "teacher-1", "confirmed")
        self.evidence = AnswerEvidence.from_answer(self.answer, 0, 17)
        self.rows = (
            CriterionScore("perspective", "2", (self.evidence,), "가상 교사 검토용 제안"),
            CriterionScore("reasoning", "5", (self.evidence,), "근거와 주장 연결은 교사가 확인"),
        )
        self.proposal = propose_score("proposal-1", self.context, self.answer,
                                      self.rubric, self.rows)

    def codes(self, proposal=None, context=None, answer=None, rubric=None):
        review = review_score(proposal or self.proposal, context or self.context,
                              answer or self.answer, rubric or self.rubric)
        return {issue.code for issue in review.issues}

    def links(self):
        return {role: EngineSourceLink("source-" + role, "snapshot", 1, fingerprint)
                for role, fingerprint in self.proposal.binding.document().items()}

    def port(self):
        return CapturingPort({"source-" + role: {"fields": {"snapshot": {"revision": 1,
                               "value": document}}} for role, document in
                              source_documents(self.context, self.answer, self.rubric).items()})

    def test_valid_proposal_stays_ai_proposed_until_explicit_teacher_decision(self):
        review = review_score(self.proposal, self.context, self.answer, self.rubric)
        self.assertTrue(review.valid)
        self.assertEqual(review.status, "ai_proposed")
        self.assertEqual(self.proposal.status, "ai_proposed")
        self.assertEqual(self.proposal.calculated_total, Decimal("7"))
        confirmed = confirm_score(self.proposal, self.context, self.answer, self.rubric,
                                  teacher_id="teacher-1", confirmation_receipt="teacher-click-1")
        self.assertEqual(confirmed.status, "teacher_confirmed")
        self.assertTrue(review_confirmation(confirmed, self.context, self.answer,
                                            self.rubric).valid)

    def test_changed_answer_with_unchanged_revision_is_detected(self):
        answer = replace(self.answer, text=self.answer.text + " 추가 문장.")
        self.assertIn("STALE_ANSWER", self.codes(answer=answer))
        self.assertIn("EVIDENCE_ANSWER_MISMATCH", self.codes(answer=answer))

    def test_changed_answer_revision_with_same_text_is_detected(self):
        self.assertIn("STALE_ANSWER", self.codes(answer=replace(self.answer, revision=2)))

    def test_another_student_identical_text_is_not_same_answer(self):
        self.assertIn("STALE_ANSWER", self.codes(answer=replace(self.answer, student_id="student-2")))

    def test_changed_rubric_same_revision_is_detected(self):
        changed = replace(self.rubric, criteria=(
            replace(self.rubric.criteria[0], maximum="2"), self.rubric.criteria[1]))
        self.assertIn("STALE_RUBRIC", self.codes(rubric=changed))

    def test_changed_teacher_instructions_are_detected(self):
        changed = replace(self.context, instructions="수정된 교사 지시")
        self.assertIn("STALE_TEACHER_CONTEXT", self.codes(context=changed))

    def test_new_problem_version_requires_matching_rubric(self):
        context = replace(self.context, problem=replace(self.problem, revision=2))
        self.assertIn("STALE_PROBLEM", self.codes(context=context))
        self.assertIn("RUBRIC_PROBLEM_MISMATCH", self.codes(context=context))

    def test_old_teacher_confirmation_cannot_approve_new_rubric(self):
        confirmed = confirm_score(self.proposal, self.context, self.answer, self.rubric,
                                  teacher_id="teacher-1", confirmation_receipt="teacher-1-confirm")
        review = review_confirmation(confirmed, self.context, self.answer,
                                     replace(self.rubric, revision=2))
        self.assertFalse(review.valid)
        self.assertIn("STALE_RUBRIC", {i.code for i in review.issues})

    def test_wrong_teacher_cannot_confirm(self):
        with self.assertRaises(ScoreHold) as raised:
            confirm_score(self.proposal, self.context, self.answer, self.rubric,
                          teacher_id="other-teacher", confirmation_receipt="click")
        self.assertIn("TEACHER_MISMATCH", {i.code for i in raised.exception.issues})

    def test_teacher_receipt_is_required(self):
        with self.assertRaises(ScoreHold):
            confirm_score(self.proposal, self.context, self.answer, self.rubric,
                          teacher_id="teacher-1", confirmation_receipt="")

    def test_unapproved_rubric_cannot_finalize(self):
        rubric = replace(self.rubric, approved_by=None)
        proposal = propose_score("draft", self.context, self.answer, rubric, self.rows)
        self.assertIn("RUBRIC_NOT_APPROVED", self.codes(proposal=proposal, rubric=rubric))

    def test_uncertain_handwriting_blocks_finalization_even_after_rebinding(self):
        answer = replace(self.answer, reading_status="uncertain")
        proposal = propose_score("uncertain", self.context, answer, self.rubric, self.rows)
        with self.assertRaises(ScoreHold) as raised:
            confirm_score(proposal, self.context, answer, self.rubric,
                          teacher_id="teacher-1", confirmation_receipt="click")
        self.assertIn("READING_UNCERTAIN", {i.code for i in raised.exception.issues})

    def test_problem_and_rubric_transcriptions_also_need_confirmation(self):
        context = replace(self.context, problem=replace(self.problem, reading_status="uncertain"))
        rubric = replace(self.rubric, reading_status="uncertain")
        proposal = propose_score("uncertain", context, self.answer, rubric, self.rows)
        self.assertIn("READING_UNCERTAIN", self.codes(proposal, context, rubric=rubric))

    def test_fabricated_exact_quote_is_rejected(self):
        bad = replace(self.evidence, quote="답안에 없는 인용")
        proposal = replace(self.proposal, scores=(replace(self.rows[0], evidence=(bad,)), self.rows[1]))
        self.assertIn("EVIDENCE_QUOTE_MISMATCH", self.codes(proposal))

    def test_quote_from_another_answer_is_rejected(self):
        bad = replace(self.evidence, answer_id="answer-2")
        proposal = replace(self.proposal, scores=(replace(self.rows[0], evidence=(bad,)), self.rows[1]))
        self.assertIn("EVIDENCE_ANSWER_MISMATCH", self.codes(proposal))

    def test_invalid_span_and_boolean_offsets_are_rejected(self):
        for start, end in ((-1, 4), (4, 2), (0, 10000), (True, 4), (0, 0)):
            with self.subTest(start=start, end=end):
                bad = replace(self.evidence, start=start, end=end)
                proposal = replace(self.proposal, scores=(replace(self.rows[0], evidence=(bad,)), self.rows[1]))
                self.assertIn("EVIDENCE_SPAN_INVALID", self.codes(proposal))

    def test_korean_and_emoji_offsets_use_exact_characters(self):
        answer = replace(self.answer, text="가🙂나\n관점")
        evidence = AnswerEvidence.from_answer(answer, 1, 2)
        self.assertEqual(evidence.quote, "🙂")
        rows = tuple(replace(row, evidence=(evidence,)) for row in self.rows)
        proposal = propose_score("unicode", self.context, answer, self.rubric, rows)
        self.assertTrue(review_score(proposal, self.context, answer, self.rubric).valid)

    def test_every_criterion_requires_answer_evidence(self):
        proposal = replace(self.proposal, scores=(replace(self.rows[0], evidence=()), self.rows[1]))
        self.assertIn("EVIDENCE_MISSING", self.codes(proposal))

    def test_missing_unknown_and_duplicate_rubric_keys_are_rejected(self):
        for rows, expected in ((self.rows[:1], "CRITERION_SET_MISMATCH"),
                               ((replace(self.rows[0], criterion_key="invented"), self.rows[1]),
                                "CRITERION_SET_MISMATCH"),
                               ((self.rows[0], self.rows[0]), "DUPLICATE_CRITERION")):
            with self.subTest(expected=expected):
                proposal = propose_score("bad-keys", self.context, self.answer, self.rubric, rows)
                self.assertIn(expected, self.codes(proposal))

    def test_scores_follow_supplied_bounds_only(self):
        for points in ("3.01", "-0.01"):
            proposal = propose_score("range", self.context, self.answer, self.rubric,
                                      (replace(self.rows[0], points=points), self.rows[1]))
            self.assertIn("POINTS_OUT_OF_RANGE", self.codes(proposal))

    def test_incorrect_reported_total_is_rejected(self):
        self.assertIn("TOTAL_MISMATCH", self.codes(replace(self.proposal, reported_total="8")))

    def test_decimal_arithmetic_is_explicit(self):
        rows = (replace(self.rows[0], points="0.1"), replace(self.rows[1], points="0.2"))
        proposal = propose_score("decimals", self.context, self.answer, self.rubric, rows)
        self.assertEqual(proposal.calculated_total, Decimal("0.3"))
        self.assertTrue(review_score(proposal, self.context, self.answer, self.rubric).valid)

    def test_decimal_total_retains_digits_when_global_precision_is_low(self):
        rows = (replace(self.rows[0], points="0.1234"), replace(self.rows[1], points="0.2345"))
        with localcontext() as decimal_context:
            decimal_context.prec = 2
            proposal = propose_score("precision", self.context, self.answer, self.rubric, rows)
            self.assertEqual(proposal.calculated_total, Decimal("0.3579"))
            self.assertTrue(review_score(proposal, self.context, self.answer, self.rubric).valid)

    def test_nonfinite_float_boolean_points_are_not_accepted(self):
        for points in ("NaN", "Infinity", True, 0.1):
            with self.subTest(points=points), self.assertRaises(ValueError):
                replace(self.rows[0], points=points)

    def test_creativity_cannot_replace_argument_validity(self):
        rubric = replace(self.rubric, criteria=(
            self.rubric.criteria[0], replace(self.rubric.criteria[1], dimension="other")))
        proposal = propose_score("separate", self.context, self.answer, rubric, self.rows)
        self.assertIn("DIMENSIONS_NOT_SEPARATE", self.codes(proposal, rubric=rubric))

    def test_structural_checks_do_not_claim_to_prove_reason_truth(self):
        # A faithful citation can accompany an unsupported interpretation.
        rows = tuple(replace(row, reason="학생이 모든 사실을 증명했다는 미검증 해석") for row in self.rows)
        proposal = propose_score("semantic-limit", self.context, self.answer, self.rubric, rows)
        self.assertTrue(review_score(proposal, self.context, self.answer, self.rubric).valid)
        self.assertEqual(proposal.status, "ai_proposed")

    def test_engine_adapter_submits_four_versioned_sources_without_finalization(self):
        port = self.port()
        candidate = submit_score_proposal(port, self.proposal, self.context, self.answer,
                                          self.rubric, source_links=self.links(),
                                          goal_ids=["essay-evidence"])
        self.assertEqual(candidate, "candidate-1")
        self.assertEqual(len(port.calls), 1)
        call = port.calls[0]
        self.assertEqual(call["purpose"], "essay_score_proposal")
        self.assertEqual(call["content"]["status"], "ai_proposed")
        self.assertEqual(len(call["source_refs"]), 4)
        self.assertTrue(call["consumption_complete"])
        self.assertEqual(set(source_documents(self.context, self.answer, self.rubric)),
                         {"teacher_context", "problem", "answer", "rubric"})

    def test_adapter_rejects_missing_or_changed_source_before_call(self):
        for changed in ("missing", "hash", "alias"):
            links = self.links()
            if changed == "missing":
                del links["answer"]
            elif changed == "hash":
                links["answer"] = replace(links["answer"], snapshot_hash="wrong")
            else:
                links["answer"] = replace(links["answer"], source_id=links["rubric"].source_id)
            port = CapturingPort()
            with self.subTest(changed=changed), self.assertRaises(ScoreHold):
                submit_score_proposal(port, self.proposal, self.context, self.answer,
                                      self.rubric, source_links=links, goal_ids=["essay-evidence"])
            self.assertEqual(port.calls, [])

    def test_adapter_checks_actual_engine_value_not_only_supplied_hash(self):
        port = self.port()
        port.sources["source-answer"]["fields"]["snapshot"]["value"] = {"text": "다른 답안"}
        with self.assertRaises(ScoreHold) as raised:
            submit_score_proposal(port, self.proposal, self.context, self.answer,
                                  self.rubric, source_links=self.links(), goal_ids=["essay-evidence"])
        self.assertIn("ENGINE_SOURCE_MISMATCH", {i.code for i in raised.exception.issues})
        self.assertEqual(port.calls, [])

    def test_adapter_checks_actual_engine_field_revision(self):
        port = self.port()
        port.sources["source-answer"]["fields"]["snapshot"]["revision"] = 2
        with self.assertRaises(ScoreHold):
            submit_score_proposal(port, self.proposal, self.context, self.answer,
                                  self.rubric, source_links=self.links(), goal_ids=["essay-evidence"])
        self.assertEqual(port.calls, [])

    def test_real_engine_holds_old_score_after_source_correction(self):
        from app.services.dreamkeeper_engine import DreamkeeperEngine, EngineHold
        engine = DreamkeeperEngine(":memory:", "owner-1")
        try:
            engine.create_task("dreamkeeper-1", "essay-task", goals={"essay-evidence": "답안 근거 확인"},
                               priorities=["essay-evidence"], rules=["교사 확정 전 제안 유지"],
                               obligations={"teacher": "교사 검토"}, user_receipt="task-authorized")
            documents = source_documents(self.context, self.answer, self.rubric)
            links = {}
            fingerprints = ScoreBinding.capture(self.context, self.answer, self.rubric).document()
            for role, document in documents.items():
                state = engine.put_source("dreamkeeper-1", "essay-task", source_id=role,
                                          fields={"snapshot": document}, expected_revision=0,
                                          user_receipt="teacher-source-" + role)
                links[role] = EngineSourceLink(role, "snapshot",
                                               state["fields"]["snapshot"]["revision"],
                                               fingerprints[role])
            binding = engine.begin_run("dreamkeeper-1", "essay-task", model_id="no-llm-test",
                                       session_id="session-1")
            candidate = submit_score_proposal(engine.model_port(binding), self.proposal,
                                              self.context, self.answer, self.rubric,
                                              source_links=links, goal_ids=["essay-evidence"])
            stored = engine.candidate("dreamkeeper-1", "essay-task", candidate)
            self.assertEqual(stored["content"]["status"], "ai_proposed")
            approval = engine.review_candidate(
                "dreamkeeper-1", "essay-task", candidate_id=candidate,
                checks={key: True for key in engine.REVIEW_CHECKS},
                obligation_checks={"teacher": True},
                reviewer="test-teacher", receipt="fixture-structural-review")
            changed = replace(self.answer, revision=2, text=self.answer.text + " 수정")
            engine.put_source("dreamkeeper-1", "essay-task", source_id="answer",
                              fields={"snapshot": changed.document()}, expected_revision=1,
                              user_receipt="teacher-correction")
            with self.assertRaises(EngineHold):
                engine.consume("dreamkeeper-1", "essay-task", candidate_id=candidate,
                               approval_id=approval, purpose="essay_score_proposal")
        finally:
            engine.close()

    def test_adapter_does_not_submit_stale_or_uncertain_scoring(self):
        port = CapturingPort()
        with self.assertRaises(ScoreHold):
            submit_score_proposal(port, self.proposal, self.context,
                                  replace(self.answer, revision=2), self.rubric,
                                  source_links=self.links(), goal_ids=["essay-evidence"])
        self.assertEqual(port.calls, [])

    def test_source_revision_must_not_be_boolean(self):
        with self.assertRaises(ValueError):
            EngineSourceLink("source", "snapshot", True, "hash")
        with self.assertRaises(ValueError):
            replace(self.evidence, answer_revision=True)

    def test_guidance_stages_cannot_skip_to_equation(self):
        progress = GuidanceProgress()
        self.assertEqual(progress.stage, "korean_interpretation")
        with self.assertRaises(ValueError):
            progress.record_student_response("student_equation", "학생이 쓴 식")
        responses = ("문장의 뜻", "조건과 요구량", "그림의 관계", "학생이 직접 쓴 논증")
        for response in responses:
            progress = progress.record_student_response(progress.stage, response)
        self.assertEqual(progress.stage, "teacher_review")
        with self.assertRaises(ValueError):
            progress.record_student_response("student_equation", "다시 입력")

    def test_student_response_required_for_each_stage(self):
        with self.assertRaises(ValueError):
            GuidanceProgress().record_student_response("korean_interpretation", "  ")


if __name__ == "__main__":
    unittest.main()
