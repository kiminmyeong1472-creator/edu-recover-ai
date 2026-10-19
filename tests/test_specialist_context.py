"""Counterexamples for incomplete evidence and specialist-transfer preparation."""
import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from app.services.specialist_context import (
    ContextFrame, Source, Fact, Goal, Hypothesis, KnowledgeAccess,
    TransferPlan, ConditionMapping, analyze_context, choose_goal,
    knowledge_before, prepare_specialist_review, preflight_transfer,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def source(sid="s1", **updates):
    return Source(source_id=sid, revision=1, current_revision=1,
                  independence_family=updates.pop("independence_family", sid),
                  permitted_uses=updates.pop("permitted_uses", ("internal_review", "specialist_review", "training")),
                  quality=updates.pop("quality", "usable"), **updates)


def fact(fid="f1", sid="s1", **updates):
    data = dict(fact_id=fid, source_id=sid, source_locator="training-example:1",
                event_id="incident-1", subject="room-2", predicate="person_count",
                value="2", value_type="number", unit="persons", observation_scope="room-2-interior",
                kind="statement", observed_at=NOW-timedelta(seconds=60),
                observed_until=NOW-timedelta(seconds=50), received_at=NOW-timedelta(seconds=30))
    data.update(updates)
    return Fact(**data)


def frame(**updates):
    data = dict(frame_id="case-1", revision=1, domain="fire", current_goal_id="rescue",
                goals=(Goal(goal_id="rescue", purpose="대원의 안전 조건을 고려한 현장 검토",
                            last_completed="신고 접수", unfinished=("관측 범위 확인",)),
                       Goal(goal_id="idea", purpose="새 센서 배치 아이디어", parent_goal_id="rescue",
                            branch_reason="확인 중 떠오른 관점")),
                sources=(source(),), facts=(fact(),), input_completeness="complete")
    data.update(updates)
    return ContextFrame(**data)


def plan(**updates):
    data = dict(source_domain="perspective_reasoning", target_domain="fire_review",
                mappings=(ConditionMapping(source_relation="정보 접근 범위", target_relation="카메라 관측 범위", satisfied=True),),
                intended_label="apply", training_family_ids=("s1",), holdout_family_ids=("incident-b",),
                source_rights_check="passed", expert_target_review="passed", critical_safety_check="passed",
                relationship_preservation_check="passed")
    data.update(updates)
    return TransferPlan(**data)


class SpecialistContextTests(unittest.TestCase):
    def test_scope_matched_conflict_does_not_decide_who_lied(self):
        f = frame(sources=(source(), source("s2")), facts=(fact(), fact("f2", "s2", value="0", kind="observation")))
        result = analyze_context(f, now=NOW)
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(result["conflicts"][0]["cause"], "unresolved")
        self.assertFalse(result["truth_or_intent_established"])

    def test_first_floor_camera_is_not_a_refutation_of_second_floor_report(self):
        f = frame(facts=(fact(), fact("f2", subject="room-1", observation_scope="room-1-interior", value="0")))
        self.assertEqual(analyze_context(f, now=NOW)["conflicts"], [])

    def test_nonoverlapping_times_are_not_merged(self):
        later = fact("f2", value="0", observed_at=NOW-timedelta(seconds=20), observed_until=NOW-timedelta(seconds=10))
        self.assertEqual(analyze_context(frame(facts=(fact(), later)), now=NOW)["conflicts"], [])

    def test_different_units_are_not_silently_compared(self):
        self.assertEqual(analyze_context(frame(facts=(fact(), fact("f2", value="200", unit="centipersons"))), now=NOW)["conflicts"], [])

    def test_numeric_spelling_does_not_create_false_conflict(self):
        self.assertEqual(analyze_context(frame(facts=(fact(), fact("f2", value="2.00"))), now=NOW)["conflicts"], [])

    def test_copied_reports_share_one_source_family(self):
        f = frame(sources=(source(), source("s2", independence_family="s1")), facts=(fact(), fact("f2", "s2")))
        self.assertEqual(analyze_context(f, now=NOW)["non_model_source_family_count"], 1)

    def test_model_repetition_is_not_an_independent_source(self):
        f = frame(sources=(source(), source("s2")), facts=(fact(), fact("f2", "s2", kind="model_inference")))
        self.assertEqual(analyze_context(f, now=NOW)["non_model_source_family_count"], 1)

    def test_uncertain_sensor_is_not_automatically_true(self):
        result = analyze_context(frame(sources=(source(quality="uncertain"),)), now=NOW)
        self.assertEqual(result["usable_fact_ids"], [])

    def test_revocation_and_old_revision_exclude_evidence(self):
        for s in [source(revoked=True), source().model_copy(update={"current_revision":2})]:
            with self.subTest(source=s):
                self.assertEqual(analyze_context(frame(sources=(s,)), now=NOW)["usable_fact_ids"], [])

    def test_expiry_boundary_is_excluded(self):
        self.assertEqual(analyze_context(frame(sources=(source(valid_until=NOW),)), now=NOW)["usable_fact_ids"], [])

    def test_future_observation_or_future_receipt_is_excluded(self):
        for f in [fact(observed_until=NOW+timedelta(seconds=1)), fact(received_at=NOW+timedelta(seconds=1))]:
            with self.subTest(fact=f):
                self.assertEqual(analyze_context(frame(facts=(f,)), now=NOW)["usable_fact_ids"], [])

    def test_naive_timestamps_and_nonfinite_values_are_rejected(self):
        for update in [{"observed_at":NOW.replace(tzinfo=None)}, {"value":"NaN"}, {"value":"Infinity"}]:
            with self.subTest(update=update), self.assertRaises(ValidationError):
                fact(**update)

    def test_unknown_source_and_duplicate_ids_are_rejected(self):
        for update in [{"sources":()}, {"facts":(fact(),fact())}]:
            with self.subTest(update=update), self.assertRaises(ValidationError):
                frame(**update)

    def test_goal_selection_keeps_original_unfinished_work_and_idea(self):
        before = frame()
        after = choose_goal(before, goal_id="idea", expected_revision=1)
        self.assertEqual(after.current_goal_id, "idea")
        self.assertEqual(after.goals, before.goals)
        self.assertEqual(before.current_goal_id, "rescue")
        self.assertEqual(after.revision, 2)

    def test_stale_or_unknown_goal_selection_is_rejected(self):
        for args in [{"goal_id":"idea","expected_revision":0}, {"goal_id":"unknown","expected_revision":1}]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                choose_goal(frame(), **args)

    def test_branch_cycles_are_rejected(self):
        with self.assertRaises(ValidationError):
            frame(goals=(Goal(goal_id="rescue",purpose="원래 목표",parent_goal_id="idea"),
                         Goal(goal_id="idea",purpose="아이디어",parent_goal_id="rescue")))

    def test_later_information_is_not_assigned_to_an_earlier_belief(self):
        f = frame(knowledge_access=(KnowledgeAccess(actor_id="caller",fact_id="f1",reported_access_at=NOW),))
        self.assertEqual(knowledge_before(f, actor_id="caller",at=NOW-timedelta(seconds=1))["fact_ids"], [])
        self.assertEqual(knowledge_before(f, actor_id="caller",at=NOW)["fact_ids"], ["f1"])

    def test_no_specialist_permission_does_not_leak_fact_or_hypothesis(self):
        f = frame(sources=(source(permitted_uses=("internal_review",)),),
                  hypotheses=(Hypothesis(hypothesis_id="h",text="민감한 해석",supporting_fact_ids=("f1",)),))
        result = prepare_specialist_review(f, now=NOW)
        self.assertEqual(result["facts"], [])
        self.assertEqual(result["hypotheses"], [])
        self.assertNotIn("민감한",str(result))
        self.assertNotIn("source_locator",str(result))

    def test_partial_permission_does_not_launder_a_mixed_hypothesis(self):
        f = frame(sources=(source(),source("s2",permitted_uses=("internal_review",))),
                  facts=(fact(),fact("f2","s2")),
                  hypotheses=(Hypothesis(hypothesis_id="h",text="합친 해석",supporting_fact_ids=("f1","f2")),))
        result = prepare_specialist_review(f,now=NOW)
        self.assertEqual(len(result["facts"]),1)
        self.assertEqual(result["hypotheses"],[])

    def test_unknown_input_scope_holds_external_package(self):
        self.assertEqual(prepare_specialist_review(frame(input_completeness="unknown"),now=NOW)["facts"],[])

    def test_old_observation_is_not_current_field_evidence(self):
        self.assertEqual(prepare_specialist_review(frame(),now=NOW+timedelta(hours=1))["facts"],[])

    def test_review_package_never_authorizes_equipment_or_claims_call_success(self):
        result = prepare_specialist_review(frame(),now=NOW)
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["external_call_performed"])
        self.assertTrue(result["absence_of_observation_is_not_safety"])

    def test_read_permission_is_not_training_permission(self):
        result=preflight_transfer(frame(sources=(source(permitted_uses=("internal_review",)),)),plan(),now=NOW)
        self.assertEqual(result["state"],"held")

    def test_condition_failure_requires_refusal_label(self):
        mapping=(ConditionMapping(source_relation="조건",target_relation="현장 조건",satisfied=False),)
        self.assertEqual(preflight_transfer(frame(),plan(mappings=mapping),now=NOW)["state"],"held")
        result=preflight_transfer(frame(),plan(mappings=mapping,intended_label="refuse"),now=NOW)
        self.assertEqual(result["condition_label"],"refuse")

    def test_missing_condition_requires_more_information(self):
        mapping=(ConditionMapping(source_relation="조건",target_relation="현장 조건",satisfied=None),)
        result=preflight_transfer(frame(),plan(mappings=mapping,intended_label="request_more"),now=NOW)
        self.assertEqual(result["condition_label"],"request_more")

    def test_holdout_family_overlap_is_rejected(self):
        result=preflight_transfer(frame(),plan(holdout_family_ids=("s1",)),now=NOW)
        self.assertIn("training_holdout_family_overlap",result["reasons"])

    def test_declared_training_family_must_match_actual_source_lineage(self):
        for families in [("invented-family",), ("s1", "unseen-family")]:
            with self.subTest(families=families):
                result=preflight_transfer(frame(),plan(training_family_ids=families),now=NOW)
                self.assertIn("training_family_manifest_mismatch",result["reasons"])

    def test_permission_flags_and_condition_results_are_not_coerced_from_text(self):
        with self.assertRaises(ValidationError):
            source(revoked="false")
        with self.assertRaises(ValidationError):
            ConditionMapping(source_relation="조건",target_relation="현장",satisfied="true")

    def test_mandatory_failure_is_not_offset_by_other_passes(self):
        for field in ["source_rights_check","expert_target_review","critical_safety_check","relationship_preservation_check"]:
            for status in ["failed","unknown"]:
                with self.subTest(field=field,status=status):
                    self.assertEqual(preflight_transfer(frame(),plan(**{field:status}),now=NOW)["state"],"held")

    def test_ready_review_is_not_training_or_model_promotion(self):
        result=preflight_transfer(frame(),plan(),now=NOW)
        self.assertEqual(result["state"],"ready_for_pipeline_review")
        self.assertFalse(result["model_weights_updated"])
        self.assertFalse(result["training_submission_performed"])
        self.assertTrue(result["requires_authoritative_pipeline_admission"])

    def test_empty_training_input_is_not_ready(self):
        self.assertEqual(preflight_transfer(frame(facts=()),plan(),now=NOW)["state"],"held")


if __name__ == "__main__":
    unittest.main()
