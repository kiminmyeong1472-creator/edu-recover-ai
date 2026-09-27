import unittest

from app.training.eligibility import evaluate_training_eligibility
from app.training.gpt_oss_plan import (
    TrainingRecipe,
    build_training_plan,
    default_gpt_oss_20b_spec,
)
from app.training.models import (
    ConsentState,
    PatentClearanceState,
    RightsState,
    SourceClass,
    TrainingDataStatus,
    TrainingPurpose,
    TrajectoryEnvelope,
)
from app.training.promotion import evaluate_model_promotion
from app.training.models import PromotionEvidence, ModelLifecycleState
from app.training.snapshot import build_dataset_snapshot
from app.training.socratic import (
    ConceptTargetPlan,
    DiagnosticStage,
    SocraticTrajectory,
    TutoringTurn,
)
from app.training.tutoring_ip_gate import (
    IPReviewState,
    TutoringIPReview,
    TutoringTrainingUse,
    assert_tutoring_use_allowed,
)


def eligible_envelope(trajectory_id="t-1"):
    return TrajectoryEnvelope(
        trajectory_id=trajectory_id,
        source_class=SourceClass.FIRST_PARTY_TUTORING,
        rights_state=RightsState.CONFIRMED,
        consent_state=ConsentState.CONFIRMED,
        patent_clearance=PatentClearanceState.CLEARED,
        contains_personal_data=True,
        deidentified=True,
        learner_pseudonymous_id="learner-A",
        session_hash="a" * 64,
        content_hash="b" * 64,
        source_revision="rev-1",
        purpose_code="SOCRATIC_DIAGNOSIS",
        provenance_complete=True,
        source_integrity_verified=True,
        evidence_refs=("e-1",),
    )


def concept_plan():
    return ConceptTargetPlan(
        target_concept_id="MATH:RATIO_CHAIN",
        target_concept_revision="rev-1",
        learning_goal="Explain and apply the chained multiplicative relation.",
        misconception_hypothesis_ids=("H1", "H2"),
        predesigned_trap_probe_ids=("TRAP-1", "TRAP-2"),
        predesigned_boundary_probe_ids=("BOUNDARY-1",),
        question_plan_hash="q" * 64,
    )


def tutoring_trajectory(**overrides):
    data = dict(
        trajectory_id="s-1",
        educational_object_revision="rev-1",
        educational_object_hash="c" * 64,
        subject_code="MATH",
        difficulty_band="ADVANCED",
        concept_plan=concept_plan(),
        learner_initial_response_hash="d" * 64,
        turns=(
            TutoringTurn(
                turn_index=0,
                stage=DiagnosticStage.REASON_PROBE,
                tutor_action_code="WHY_THIS_STEP",
                target_concept_id="MATH:RATIO_CHAIN",
                answer_revealed=False,
            ),
            TutoringTurn(
                turn_index=1,
                stage=DiagnosticStage.TRANSFER_CHECK,
                tutor_action_code="CHANGE_CONTEXT_SAME_CONCEPT",
                target_concept_id="MATH:RATIO_CHAIN",
                variation_dimension="context",
                answer_revealed=False,
            ),
        ),
    )
    data.update(overrides)
    return SocraticTrajectory(**data)


class EligibilityTests(unittest.TestCase):
    def test_first_party_deidentified_record_can_be_eligible(self):
        decision = evaluate_training_eligibility(eligible_envelope())
        self.assertEqual(decision.status, TrainingDataStatus.ELIGIBLE)

    def test_managed_api_output_is_rejected(self):
        item = eligible_envelope()
        item.source_class = SourceClass.THIRD_PARTY_API_OUTPUT
        decision = evaluate_training_eligibility(item)
        self.assertEqual(decision.status, TrainingDataStatus.REJECTED)

    def test_personal_data_without_deidentification_is_quarantined(self):
        item = eligible_envelope()
        item.deidentified = False
        decision = evaluate_training_eligibility(item)
        self.assertEqual(decision.status, TrainingDataStatus.QUARANTINED)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_hash_is_order_independent(self):
        a = eligible_envelope("a")
        b = eligible_envelope("b")
        da = evaluate_training_eligibility(a)
        db = evaluate_training_eligibility(b)

        first = build_dataset_snapshot([(a, da), (b, db)])
        second = build_dataset_snapshot([(b, db), (a, da)])

        self.assertEqual(first.snapshot_hash, second.snapshot_hash)
        self.assertEqual(first.record_count, 2)

    def test_snapshot_rejects_quarantined_record(self):
        item = eligible_envelope()
        item.deidentified = False
        decision = evaluate_training_eligibility(item)
        with self.assertRaises(ValueError):
            build_dataset_snapshot([(item, decision)])


class SocraticTests(unittest.TestCase):
    def test_early_answer_reveal_is_blocked(self):
        trajectory = tutoring_trajectory(
            turns=(
                TutoringTurn(
                    turn_index=0,
                    stage=DiagnosticStage.REASON_PROBE,
                    tutor_action_code="WHY_THIS_STEP",
                    target_concept_id="MATH:RATIO_CHAIN",
                    answer_revealed=True,
                ),
            ),
        )
        with self.assertRaises(ValueError):
            trajectory.assert_owner_tutoring_policy()

    def test_transfer_must_stay_on_same_target_concept(self):
        trajectory = tutoring_trajectory(
            turns=(
                TutoringTurn(
                    turn_index=0,
                    stage=DiagnosticStage.TRANSFER_CHECK,
                    tutor_action_code="UNRELATED_NEW_CONCEPT",
                    target_concept_id="MATH:UNRELATED",
                    answer_revealed=False,
                ),
            ),
        )
        with self.assertRaises(ValueError):
            trajectory.assert_owner_tutoring_policy()

    def test_tutoring_response_cannot_feed_distractor_optimization(self):
        trajectory = tutoring_trajectory(
            used_for_distractor_optimization=True,
        )
        with self.assertRaises(ValueError):
            trajectory.assert_owner_tutoring_policy()

    def test_same_concept_question_variation_is_allowed(self):
        trajectory = tutoring_trajectory()
        trajectory.assert_owner_tutoring_policy()
        trajectory.assert_transfer_is_same_concept()


class TutoringIPGateTests(unittest.TestCase):
    def test_tutor_model_training_is_held_without_explicit_clearance(self):
        review = TutoringIPReview(
            review_id="r-1",
            state=IPReviewState.REVIEWED_LIMITED_USE,
        )
        with self.assertRaises(ValueError):
            assert_tutoring_use_allowed(
                intended_use=TutoringTrainingUse.TUTOR_MODEL_TRAINING,
                review=review,
            )

    def test_choice_rate_dpo_remains_blocked_even_after_clearance(self):
        review = TutoringIPReview(
            review_id="r-2",
            state=IPReviewState.CLEARED_FOR_TUTOR_MODEL_TRAINING,
        )
        with self.assertRaises(ValueError):
            assert_tutoring_use_allowed(
                intended_use=TutoringTrainingUse.CHOICE_RATE_DPO,
                review=review,
            )

    def test_tutor_model_training_can_pass_after_explicit_clearance(self):
        review = TutoringIPReview(
            review_id="r-3",
            state=IPReviewState.CLEARED_FOR_TUTOR_MODEL_TRAINING,
        )
        assert_tutoring_use_allowed(
            intended_use=TutoringTrainingUse.TUTOR_MODEL_TRAINING,
            review=review,
        )


class PromotionTests(unittest.TestCase):
    def test_all_required_gates_must_pass(self):
        result = evaluate_model_promotion(
            PromotionEvidence(
                candidate_checkpoint_hash="1" * 64,
                baseline_checkpoint_hash="2" * 64,
                dataset_snapshot_hash="3" * 64,
                eval_snapshot_hash="4" * 64,
                required_gates={
                    "data_rights": True,
                    "held_out_eval": True,
                    "socratic_non_disclosure": True,
                },
            )
        )
        self.assertEqual(result.state, ModelLifecycleState.PROMOTION_READY)

    def test_one_failed_gate_holds_candidate(self):
        result = evaluate_model_promotion(
            PromotionEvidence(
                candidate_checkpoint_hash="1" * 64,
                baseline_checkpoint_hash="2" * 64,
                dataset_snapshot_hash="3" * 64,
                eval_snapshot_hash="4" * 64,
                required_gates={
                    "data_rights": True,
                    "held_out_eval": False,
                },
            )
        )
        self.assertEqual(result.state, ModelLifecycleState.HOLD)


class TrainingPlanTests(unittest.TestCase):
    def test_default_plan_uses_replaceable_open_weight_spec(self):
        recipe = TrainingRecipe(
            recipe_id="sft-v1",
            purposes=(TrainingPurpose.SFT,),
            method="QLoRA",
            precision="bf16",
            max_sequence_length=4096,
            epochs=1.0,
            learning_rate=2e-5,
        )
        plan = build_training_plan(
            dataset_snapshot_hash="f" * 64,
            recipe=recipe,
        )
        self.assertTrue(plan.base_model.open_weight)
        self.assertEqual(plan.base_model.family, "gpt-oss")
        self.assertEqual(
            plan.base_model.model_id,
            default_gpt_oss_20b_spec().model_id,
        )


if __name__ == "__main__":
    unittest.main()

