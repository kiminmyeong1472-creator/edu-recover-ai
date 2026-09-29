"""Regression checks for instruction versus independent transfer evidence."""

from datetime import datetime, timezone

import pytest

from app.training.tutor_learning import (
    ContentTrustState, EvidenceType, LearningPurpose, evaluate_tutor_reward,
    evaluate_purpose_eligibility,
)
from app.training.tutor_ledger import TutorLearningLedger
from app.training.socratic import (
    AnswerPolicy, ConceptTargetPlan, DiagnosticStage, SocraticTrajectory, TutoringTurn,
)


def evidence(kind, **kwargs):
    return dict(evidence_revision_id=kind, evidence_type=kind,
                content_trust="VERIFIED", source_integrity_verified=True,
                pseudonym_salt_configured=True, **kwargs)


def test_worked_explanation_can_precede_unaided_success():
    rows = [evidence("HINT_USE", answer_disclosed=True, hint_level=2),
            evidence("INDEPENDENT_FOLLOWUP_RESPONSE", independent=True, correct=True)]
    reward = evaluate_tutor_reward(rows)
    assert not reward.hard_block_reasons
    assert reward.components["unaided_same_concept_transfer"] == 35


@pytest.mark.parametrize("support", [dict(hint_level=1), dict(answer_disclosed=True), dict(independent=False)])
def test_followup_label_cannot_turn_supported_work_into_independent_evidence(support):
    row = evidence("INDEPENDENT_FOLLOWUP_RESPONSE", correct=True, independent=True)
    row.update(support)
    reward = evaluate_tutor_reward([row])
    assert reward.components["unaided_same_concept_transfer"] == 0


def test_unverified_material_cannot_supply_mastery_evidence():
    row = evidence("INDEPENDENT_FOLLOWUP_RESPONSE", correct=True, independent=True)
    row["content_trust"] = "UNVERIFIED"
    reward = evaluate_tutor_reward([row])
    assert "CONTENT_NOT_VERIFIED" in reward.hard_block_reasons
    assert evaluate_purpose_eligibility(LearningPurpose.MASTERY_MODEL, [row], reward).state.value == "HOLD"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("EDURECOVER_TRAINING_PSEUDONYM_SALT", "test-only-salt")
    return TutorLearningLedger(tmp_path / "ledger.sqlite3")


def segment(name, text, **kwargs):
    return dict(segment_id=name, content_revision_id=name + ":v1", concept_key="ratio",
                text=text, kind="PROBLEM", source_integrity_verified=True,
                content_trust="VERIFIED", **kwargs)


def capture(ledger, s, correct=False):
    return ledger.capture_response(session_id="session", student_alias="student",
        segment=s, response_ref=s["segment_id"], correct=correct, reason_present=True,
        confidence=3)


def review(ledger, s, correct=True):
    return ledger.review_response(session_id="session", student_alias="student",
        response_ref=s["segment_id"], segment=s, correct=correct, reason_present=True,
        confidence=3, source_integrity_verified=True, content_trust=ContentTrustState.VERIFIED,
        hint_or_explanation_confirmed=False, notes=None)


def rows(ledger):
    with ledger._db() as db:
        return ledger._evidence_rows(db, "session", ledger.learner_key("session", "student"), "ratio")


def test_repeated_task_with_new_revision_is_practice(ledger):
    capture(ledger, segment("initial", "2배의 3배는?", feedback_phase="PRE_FEEDBACK"))
    capture(ledger, segment("renamed", " 2배의 3배는? ", independent_followup=True), True)
    assert all(r["evidence_type"] != "INDEPENDENT_FOLLOWUP_RESPONSE" for r in rows(ledger))


def test_retest_name_alone_does_not_establish_independence(ledger):
    s = segment("retest", "3배의 4배는?")
    s["kind"] = "RETEST"
    capture(ledger, s, True)
    assert rows(ledger)[0]["evidence_type"] == "RETRY_RESPONSE"


def test_instruction_answer_does_not_block_distinct_independent_question(ledger):
    first = segment("demo", "2배의 3배는 6배입니다.", answer_disclosed=True, hint_level=2)
    first["kind"] = "VIDEO"
    capture(ledger, first)
    capture(ledger, segment("independent", "3배의 4배는?", independent_followup=True), True)
    reward = evaluate_tutor_reward(rows(ledger))
    assert "ASSESSMENT_ANSWER_DISCLOSED" not in reward.hard_block_reasons
    assert reward.components["unaided_same_concept_transfer"] == 35


def test_declared_delayed_hours_cannot_fake_elapsed_time(ledger):
    capture(ledger, segment("initial", "2배의 3배는?", feedback_phase="PRE_FEEDBACK"))
    capture(ledger, segment("later", "4배의 5배는?", independent_followup=True, delayed_hours=48), True)
    reward = evaluate_tutor_reward(rows(ledger))
    assert reward.components["delayed_transfer"] == 0


def test_independent_assessment_pre_feedback_phase_is_not_initial_diagnosis(ledger):
    capture(ledger, segment("initial", "2배의 3배는?", feedback_phase="PRE_FEEDBACK"))
    capture(ledger, segment("later", "4배의 5배는?", independent_followup=True,
                            feedback_phase="PRE_FEEDBACK"), True)
    assert any(r["evidence_type"] == "INDEPENDENT_FOLLOWUP_RESPONSE" for r in rows(ledger))
    assert evaluate_tutor_reward(rows(ledger)).components["unaided_same_concept_transfer"] == 35


def test_one_review_cannot_verify_other_unreviewed_revision(ledger):
    first = segment("initial", "2배의 3배는?", feedback_phase="PRE_FEEDBACK")
    first.update(content_trust="UNVERIFIED", source_integrity_verified=False)
    later = segment("later", "4배의 5배는?", independent_followup=True)
    later.update(content_trust="UNVERIFIED", source_integrity_verified=False)
    capture(ledger, first)
    capture(ledger, later, True)
    review(ledger, later)
    initial = next(r for r in rows(ledger) if r["evidence_type"] == "PRE_FEEDBACK_RESPONSE")
    assert initial["content_trust"] == "UNVERIFIED"
    assert not initial["source_integrity_verified"]


def test_teacher_correction_overrides_earlier_automatic_correctness(ledger):
    first = segment("initial", "2배의 3배는?", feedback_phase="PRE_FEEDBACK")
    later = segment("later", "4배의 5배는?", independent_followup=True)
    capture(ledger, first)
    capture(ledger, later, True)
    assert evaluate_tutor_reward(rows(ledger)).components["unaided_same_concept_transfer"] == 35
    review(ledger, later, False)
    assert evaluate_tutor_reward(rows(ledger)).components["unaided_same_concept_transfer"] == 0


def trajectory(stage, **turn_kwargs):
    return SocraticTrajectory(trajectory_id="t", educational_object_revision="r",
        educational_object_hash="h", subject_code="MATH", difficulty_band="BASIC",
        learner_initial_response_hash="l", concept_plan=ConceptTargetPlan(
            target_concept_id="C", target_concept_revision="r", learning_goal="apply",
            question_plan_hash="h"), turns=(TutoringTurn(turn_index=0, stage=stage,
            tutor_action_code="action", target_concept_id="C", **turn_kwargs),))


def test_optional_instruction_policy_allows_explanation():
    t = trajectory(DiagnosticStage.WORKED_EXAMPLE, answer_revealed=True)
    assert t.concept_plan.answer_policy is AnswerPolicy.EXPLAIN_THEN_INDEPENDENT_TRANSFER
    t.assert_owner_tutoring_policy()


def test_transfer_stays_unaided_even_after_concept_recovery():
    t = trajectory(DiagnosticStage.TRANSFER_CHECK, answer_revealed=True)
    t.concept_recovery_passed = True
    with pytest.raises(ValueError, match="Independent"):
        t.assert_owner_tutoring_policy()
