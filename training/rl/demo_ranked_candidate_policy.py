"""Reproducible synthetic demonstration, not a student effectiveness study."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path

from .ranked_candidate_policy import REWARD_SEMANTICS, digest, fit_policy, score_candidates

CONTEXT = "arithmetic-series-diagnosis-demo"


def make_export(*, batch_id="synthetic-train", split="TRAIN", split_group="train-task-family",
                positive_rank=14, negative_rank=1) -> list[dict]:
    """The 91/116 story here is declared test data, not observed clinical/education data."""
    candidates = [
        {"candidate_id": f"{batch_id}-incorrect", "rank": negative_rank, "model_score": None,
         "origin": "MODEL", "text": "Incorrect hypothesis: last term used as first term",
         "hypothesis_type": "LAST_TERM_AS_FIRST", "concept_refs": ["arithmetic.sum"],
         "evidence_refs": ["synthetic-hypothesis-input"]},
        {"candidate_id": f"{batch_id}-correct", "rank": positive_rank, "model_score": None,
         "origin": "MODEL", "text": "Supported test hypothesis: counted seven terms rather than eight",
         "hypothesis_type": "MISSING_TERM", "concept_refs": ["arithmetic.term-count"],
         "evidence_refs": ["synthetic-hypothesis-input"]},
    ]
    manifest = {"context_ref": CONTEXT, "candidates": copy.deepcopy(candidates),
                "selected_candidate_ids": [c["candidate_id"] for c in candidates],
                "selection_mode": "DETERMINISTIC_ENUMERATION", "exploration_probability": None}
    rows = []
    for index, candidate in enumerate(candidates):
        receipt = {"receipt_id": f"receipt-{candidate['candidate_id']}", "batch_id": batch_id,
                   "batch_digest": digest({"candidate_set": candidates, "manifest": manifest}),
                   "candidate_id": candidate["candidate_id"], "original_rank": candidate["rank"],
                   "status": "REJECTED" if index == 0 else "ACCEPTED",
                   "ground_truth_status": "VERIFIED", "teacher_verified": True,
                   "reason_status": "TEACHER_CHECKED", "learning_use_approved": True,
                   "rights_status": "APPROVED", "split": split, "split_group": split_group,
                   "evidence_refs": ["synthetic-check-arithmetic-and-seven-term-trace"],
                   "reason": "Synthetic test: last-as-first predicts 284 and fails; seven terms predict 91 and match the stipulated seven-term trace."}
        receipt["receipt_digest"] = digest(receipt)
        row = {"context_ref": CONTEXT, "candidate": copy.deepcopy(candidate), "receipt": receipt,
               "selection_manifest": copy.deepcopy(manifest), "candidate_set": copy.deepcopy(candidates),
               "reward_semantics": REWARD_SEMANTICS, "valid": True,
               "data_origin": "SYNTHETIC_DEMONSTRATION_NOT_STUDENT_OBSERVATION"}
        row["reward_digest"] = digest(row)
        rows.append(row)
    return rows


def run_demo() -> dict:
    train = make_export()
    evaluation = make_export(batch_id="synthetic-eval", split="EVAL", split_group="held-out-task-family",
                             positive_rank=9, negative_rank=14)
    before = fit_policy([], context_ref=CONTEXT)["state"]
    trained = fit_policy(train + evaluation, context_ref=CONTEXT)
    future = [row["candidate"] for row in evaluation]
    return {"data_origin": "SYNTHETIC_DEMONSTRATION_NOT_STUDENT_OBSERVATION",
            "description": "Rank 14 verified in training; matching content at rank 9 beats incorrect content at rank 14 in held-out synthetic data.",
            "base_model_weights_trained": False, "real_student_efficacy_verified": False,
            "train_rows": train, "eval_rows": evaluation,
            "before": score_candidates(future, before, context_ref=CONTEXT),
            "after": score_candidates(future, trained["state"], context_ref=CONTEXT),
            "training_result": trained}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_demo()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"before": result["before"], "after": result["after"],
                      "metrics": result["training_result"]["metrics"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
