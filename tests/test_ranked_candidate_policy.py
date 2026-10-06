import copy
import math
import unittest

from training.rl.demo_ranked_candidate_policy import CONTEXT, make_export, run_demo
from training.rl.ranked_candidate_policy import (
    PolicyDataError, candidate_features, digest, fit_policy, score_candidates,
)


def reseal(row):
    row["receipt"].pop("receipt_digest", None)
    row["receipt"]["receipt_digest"] = digest(row["receipt"])
    row.pop("reward_digest", None)
    row["reward_digest"] = digest(row)
    return row


class ContentPolicyTests(unittest.TestCase):
    def test_actual_cpu_gradient_improves_content_not_rank_fourteen(self):
        result = run_demo()
        correct = next(x for x in result["after"] if x["candidate_id"].endswith("correct") and not x["candidate_id"].endswith("incorrect"))
        wrong = next(x for x in result["after"] if x["candidate_id"].endswith("incorrect"))
        self.assertEqual(correct["original_rank"], 9)
        self.assertEqual(wrong["original_rank"], 14)
        self.assertGreater(correct["policy_priority"], 0.85)
        self.assertGreater(correct["policy_priority"], wrong["policy_priority"])
        self.assertGreater(result["training_result"]["metrics"]["gradient_steps"], 0)
        self.assertFalse(result["training_result"]["state"]["rank_used_as_feature"])
        self.assertFalse(result["base_model_weights_trained"])

    def test_rank_and_provider_score_changes_do_not_change_features(self):
        candidate = make_export()[1]["candidate"]
        changed = copy.deepcopy(candidate)
        changed.update(rank=1, model_score=0.9999, text="Entirely different untrusted wording")
        self.assertEqual(candidate_features(candidate, CONTEXT), candidate_features(changed, CONTEXT))

    def test_unknown_outcome_is_masked_not_zero_reward(self):
        rows = make_export()
        rows[1]["receipt"]["ground_truth_status"] = "UNKNOWN"
        reseal(rows[1])
        result = fit_policy(rows, context_ref=CONTEXT)
        self.assertEqual(result["metrics"]["masked"]["UNKNOWN_OUTCOME"], 1)
        self.assertEqual(result["metrics"]["gradient_steps"], 0)
        self.assertEqual(result["state"]["weights"], {})

    def test_unknown_reason_and_missing_approval_or_rights_are_not_learned(self):
        for key, value, category in [
            ("reason_status", "UNKNOWN", "UNKNOWN_REASON"),
            ("learning_use_approved", False, "LEARNING_NOT_APPROVED"),
            ("rights_status", "UNKNOWN", "RIGHTS_NOT_APPROVED"),
            ("split", "UNDECLARED", "SPLIT_NOT_DECLARED"),
        ]:
            with self.subTest(key=key):
                rows = make_export()
                rows[1]["receipt"][key] = value
                reseal(rows[1])
                result = fit_policy(rows, context_ref=CONTEXT)
                self.assertEqual(result["metrics"]["masked"][category], 1)
                self.assertEqual(result["metrics"]["gradient_steps"], 0)

    def test_evaluation_labels_never_change_weights(self):
        train = make_export()
        evaluation = make_export(batch_id="eval", split="EVAL", split_group="held-out")
        trained = fit_policy(train, context_ref=CONTEXT)
        with_eval = fit_policy(train + evaluation, context_ref=CONTEXT)
        self.assertEqual(trained["state"]["weights"], with_eval["state"]["weights"])
        self.assertEqual(trained["state"]["training_digest"], with_eval["state"]["training_digest"])
        self.assertEqual(with_eval["metrics"]["eval_sample_count"], 2)

    def test_same_task_family_cannot_be_in_train_and_eval(self):
        with self.assertRaisesRegex(PolicyDataError, "TRAIN/EVAL overlap"):
            fit_policy(make_export() + make_export(batch_id="eval", split="EVAL"), context_ref=CONTEXT)

    def test_exact_replay_is_idempotent_and_conflicting_duplicate_fails(self):
        rows = make_export()
        once = fit_policy(rows, context_ref=CONTEXT)
        duplicate = fit_policy(rows + copy.deepcopy(rows), context_ref=CONTEXT)
        self.assertEqual(once["state"]["weights"], duplicate["state"]["weights"])
        self.assertEqual(duplicate["metrics"]["masked"]["EXACT_REPLAY_DUPLICATE"], 2)
        altered = copy.deepcopy(rows[1])
        altered["receipt"]["reason"] += " changed"
        reseal(altered)
        with self.assertRaisesRegex(PolicyDataError, "conflicting duplicate"):
            fit_policy(rows + [altered], context_ref=CONTEXT)

    def test_tampered_reason_fails_receipt_or_reward_digest(self):
        rows = make_export()
        rows[1]["receipt"]["reason"] = "unrecorded alteration"
        with self.assertRaisesRegex(PolicyDataError, "invalid reward_digest"):
            fit_policy(rows, context_ref=CONTEXT)
        rows[1].pop("reward_digest")
        rows[1]["reward_digest"] = digest(rows[1])
        with self.assertRaisesRegex(PolicyDataError, "invalid receipt_digest"):
            fit_policy(rows, context_ref=CONTEXT)

    def test_original_candidate_binding_cannot_be_rewritten_even_when_rehashed(self):
        rows = make_export()
        rows[1]["candidate"]["hypothesis_type"] = "FAKE_REASON"
        reseal(rows[1])
        with self.assertRaisesRegex(PolicyDataError, "immutable selection manifest"):
            fit_policy(rows, context_ref=CONTEXT)

    def test_invalidation_rebuild_removes_previous_weight_influence(self):
        rows = make_export()
        previous = fit_policy(rows, context_ref=CONTEXT)["state"]
        rows[1]["valid"] = False
        result = fit_policy(rows, previous, context_ref=CONTEXT)
        self.assertEqual(result["state"]["weights"], {})
        self.assertGreater(result["state"]["revision"], previous["revision"])
        self.assertEqual(result["metrics"]["masked"]["INVALIDATED_OR_NOT_CURRENT"], 1)

    def test_different_context_isolated_and_cross_context_scoring_rejected(self):
        result = fit_policy(make_export(), context_ref="other-context")
        self.assertEqual(result["metrics"]["train_sample_count"], 0)
        with self.assertRaisesRegex(PolicyDataError, "policy context mismatch"):
            score_candidates([make_export()[0]["candidate"]], result["state"], context_ref=CONTEXT)

    def test_checked_reason_text_is_not_a_prediction_feature(self):
        rows = make_export()
        original = fit_policy(rows, context_ref=CONTEXT)
        for row in rows:
            row["receipt"]["reason"] = "A different teacher-checked wording for the same predeclared hypothesis"
            reseal(row)
        changed = fit_policy(rows, context_ref=CONTEXT)
        self.assertEqual(original["state"]["weights"], changed["state"]["weights"])
        self.assertNotEqual(original["state"]["training_reason_digests"], changed["state"]["training_reason_digests"])

    def test_human_insight_has_no_fabricated_rank_or_probability(self):
        rows = make_export()
        candidate = rows[1]["candidate"]
        candidate.update(origin="HUMAN_INSIGHT", rank=None, model_score=None)
        rows[1]["receipt"]["original_rank"] = None
        for row in rows:
            row["candidate_set"][1] = copy.deepcopy(candidate)
            row["selection_manifest"]["candidates"][1] = copy.deepcopy(candidate)
            reseal(row)
        state = fit_policy(rows, context_ref=CONTEXT)["state"]
        score = score_candidates([candidate], state, context_ref=CONTEXT)[0]
        self.assertIsNone(score["original_rank"])
        self.assertEqual(score["priority_semantics"], "EXPLORATION_PRIORITY_NOT_CORRECTNESS_PROBABILITY")

    def test_retraining_same_current_export_is_deterministic(self):
        rows = make_export()
        first = fit_policy(rows, context_ref=CONTEXT)
        repeated = fit_policy(rows, first["state"], context_ref=CONTEXT)
        self.assertEqual(first["state"], repeated["state"])
        self.assertEqual(first["metrics"], repeated["metrics"])

    def test_bad_hyperparameters_and_nonfinite_weights_fail(self):
        for rate in [float("nan"), float("inf"), 0, -1, 1.1]:
            with self.assertRaises(PolicyDataError):
                fit_policy([], context_ref=CONTEXT, learning_rate=rate)
        state = fit_policy(make_export(), context_ref=CONTEXT)["state"]
        state["weights"]["bad"] = float("inf")
        with self.assertRaises((PolicyDataError, ValueError)):
            score_candidates([], state, context_ref=CONTEXT)

    def test_empty_metadata_cannot_learn_from_rank_only(self):
        rows = make_export()
        for row in rows:
            for candidate in row["candidate_set"] + row["selection_manifest"]["candidates"] + [row["candidate"]]:
                candidate.pop("hypothesis_type", None)
                candidate.pop("concept_refs", None)
            reseal(row)
        result = fit_policy(rows, context_ref=CONTEXT)
        self.assertEqual(result["metrics"]["masked"]["NO_PREDECLARED_CONTENT_FEATURES"], 2)
        self.assertEqual(result["state"]["weights"], {})

    def test_explicit_reward_and_receipt_flags_cannot_disagree(self):
        for field, value in [("reward", -1), ("split", "EVAL"), ("rights_status", "UNKNOWN"),
                             ("receipt_digest", "0" * 64), ("batch_id", "different-batch")]:
            with self.subTest(field=field):
                rows = make_export()
                rows[1][field] = value
                reseal(rows[1])
                with self.assertRaises(PolicyDataError):
                    fit_policy(rows, context_ref=CONTEXT)


if __name__ == "__main__":
    unittest.main()
