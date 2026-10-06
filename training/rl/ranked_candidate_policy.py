"""Train a small content-based exploration policy using verified rewards.

This is a contextual, full-information policy-gradient optimizer over the
verified contrast set in each recorded candidate batch. It is not offline
REINFORCE: deterministic enumeration has no sampled-action propensity. There
are no foundation-model weights or provider calls here.

Features are declared candidate hypothesis types and concept references, bound
to a task context. Original rank, provider score, outcome, and the subsequently
observed verification reason are NEVER prediction inputs. The checked reason
qualifies the hypothesis/reward pair for learning and remains in the evidence
record. A policy probability is an exploration priority, not truth confidence.

Every run rebuilds from the CURRENT eligible training export. This deliberately
removes the influence of withdrawn or invalidated observations. Evaluation
rows are scored but never update weights. Hashes detect changed exported data;
authorization and source legitimacy must be enforced by the exporting service.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any

VERSION = "CONTENT_EXPECTED_REWARD_POLICY_V1"
REWARD_SEMANTICS = "VERIFIED_CANDIDATE_CORRECTNESS_NOT_STUDENT_LEARNING_GAIN"


class PolicyDataError(ValueError):
    """The exported learning observations are internally inconsistent."""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _without_digest(value: dict, key: str) -> dict:
    return {k: v for k, v in value.items() if k != key}


def _checked_digest(value: dict, key: str) -> None:
    recorded = value.get(key)
    if not isinstance(recorded, str) or recorded != digest(_without_digest(value, key)):
        raise PolicyDataError(f"invalid {key}")


def _semantic_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise PolicyDataError(f"invalid {name}")
    return value.strip()


def candidate_features(candidate: dict, context_ref: str) -> dict[str, float]:
    """Use only content metadata that existed before the outcome was checked."""
    context = _semantic_string(context_ref, "context_ref")
    kind = candidate.get("hypothesis_type")
    concepts = candidate.get("concept_refs", [])
    if not isinstance(concepts, list) or len(concepts) > 30:
        raise PolicyDataError("invalid concept_refs")
    keys: set[str] = set()
    if kind is not None and kind != "":
        kind = _semantic_string(kind, "hypothesis_type")
        keys.add(f"hypothesis:{kind}")
        keys.add(f"context:{context}|hypothesis:{kind}")
    for concept in concepts:
        concept = _semantic_string(concept, "concept_ref")
        keys.add(f"concept:{concept}")
        keys.add(f"context:{context}|concept:{concept}")
    # Empty metadata produces no invented text/rank feature and cannot learn.
    if not keys:
        return {}
    scale = 1.0 / math.sqrt(len(keys))
    return {key: scale for key in sorted(keys)}


def import_eligible_export(rows: list[dict], *, context_ref: str | None = None) -> dict:
    """Validate current receipts and separate approved train/evaluation data.

    Ineligible observations are masked, not assigned zero or negative reward.
    A duplicate with different content, broken digest, or split leakage is an
    error. Exact replay duplicates contribute only once.
    """
    if not isinstance(rows, list):
        raise PolicyDataError("export must be a list")
    masked: Counter[str] = Counter()
    imported: list[dict] = []
    seen: dict[str, str] = {}
    group_splits: dict[tuple[str, str], str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise PolicyDataError("export row must be an object")
        if row.get("valid") is not True:
            masked["INVALIDATED_OR_NOT_CURRENT"] += 1
            continue
        receipt = row.get("receipt", {})
        if not isinstance(receipt, dict):
            raise PolicyDataError("receipt must be an object")
        _checked_digest(row, "reward_digest")
        _checked_digest(receipt, "receipt_digest")
        context = _semantic_string(row.get("context_ref"), "context_ref")
        if context_ref is not None and context != context_ref:
            masked["OTHER_CONTEXT"] += 1
            continue
        status = receipt.get("status")
        if status not in {"ACCEPTED", "REJECTED"} or receipt.get("ground_truth_status") != "VERIFIED":
            masked["UNKNOWN_OUTCOME"] += 1
            continue
        if receipt.get("teacher_verified") is not True:
            masked["UNVERIFIED_OUTCOME"] += 1
            continue
        if receipt.get("reason_status") not in {"VERIFIED", "TEACHER_CHECKED"}:
            masked["UNKNOWN_REASON"] += 1
            continue
        if not isinstance(receipt.get("reason"), str) or not receipt["reason"].strip():
            masked["EMPTY_REASON"] += 1
            continue
        if receipt.get("learning_use_approved") is not True:
            masked["LEARNING_NOT_APPROVED"] += 1
            continue
        if receipt.get("rights_status") != "APPROVED":
            masked["RIGHTS_NOT_APPROVED"] += 1
            continue
        split = receipt.get("split")
        if split not in {"TRAIN", "EVAL"}:
            masked["SPLIT_NOT_DECLARED"] += 1
            continue
        if row.get("reward_semantics") != REWARD_SEMANTICS:
            raise PolicyDataError("unsupported reward semantics")
        expected_reward = 1 if status == "ACCEPTED" else -1
        if "reward" in row and row["reward"] != expected_reward:
            raise PolicyDataError("reward differs from verified outcome")
        if "receipt_digest" in row and row["receipt_digest"] != receipt["receipt_digest"]:
            raise PolicyDataError("reward/receipt digest binding mismatch")
        for field in ("split", "split_group", "learning_use_approved", "rights_status", "reason_status"):
            if field in row and row[field] != receipt.get(field):
                raise PolicyDataError(f"reward/receipt {field} binding mismatch")
        evidence = receipt.get("evidence_refs")
        if not isinstance(evidence, list) or not evidence or any(not isinstance(e, str) or not e.strip() for e in evidence):
            masked["MISSING_VERIFICATION_EVIDENCE"] += 1
            continue
        candidate = row.get("candidate")
        manifest = row.get("selection_manifest")
        if not isinstance(candidate, dict) or not isinstance(manifest, dict):
            raise PolicyDataError("missing candidate or pre-verification manifest")
        candidate_id = _semantic_string(candidate.get("candidate_id"), "candidate_id")
        batch_id = _semantic_string(receipt.get("batch_id"), "batch_id")
        receipt_id = _semantic_string(receipt.get("receipt_id"), "receipt_id")
        if receipt.get("candidate_id") != candidate_id or manifest.get("context_ref") != context:
            raise PolicyDataError("receipt/context/candidate binding mismatch")
        if "batch_id" in row and row["batch_id"] != batch_id:
            raise PolicyDataError("reward/receipt batch binding mismatch")
        if not isinstance(receipt.get("batch_digest"), str) or len(receipt["batch_digest"]) != 64:
            raise PolicyDataError("missing original batch digest")
        selected = manifest.get("candidates", [])
        original = next((x for x in selected if x.get("candidate_id") == candidate_id), None)
        if original != candidate or candidate_id not in manifest.get("selected_candidate_ids", []):
            raise PolicyDataError("candidate not in immutable selection manifest")
        if receipt.get("original_rank") != candidate.get("rank"):
            raise PolicyDataError("original rank changed after verification")
        candidate_set = row.get("candidate_set", [])
        recorded = next((x for x in candidate_set if x.get("candidate_id") == candidate_id), None)
        if recorded != candidate:
            raise PolicyDataError("candidate set differs from selected candidate")
        split_group = _semantic_string(receipt.get("split_group"), "split_group")
        split_key = (context, split_group)
        if split_key in group_splits and group_splits[split_key] != split:
            raise PolicyDataError("TRAIN/EVAL overlap in the same task family")
        group_splits[split_key] = split
        sample_id = f"{context}:{batch_id}:{candidate_id}"
        sample_digest = row["reward_digest"]
        if sample_id in seen:
            if seen[sample_id] != sample_digest:
                raise PolicyDataError("conflicting duplicate candidate observation")
            masked["EXACT_REPLAY_DUPLICATE"] += 1
            continue
        seen[sample_id] = sample_digest
        features = candidate_features(candidate, context)
        if not features:
            masked["NO_PREDECLARED_CONTENT_FEATURES"] += 1
            continue
        imported.append({"sample_id": sample_id, "receipt_id": receipt_id,
                         "batch_id": batch_id, "context_ref": context,
                         "split": split, "split_group": split_group,
                         "candidate_id": candidate_id, "features": features,
                         "reward": 1.0 if status == "ACCEPTED" else -1.0,
                         "reward_digest": sample_digest,
                         "receipt_digest": receipt["receipt_digest"],
                         "reason_digest": digest(receipt["reason"]),
                         "original_rank": candidate.get("rank")})
    return {"train": [x for x in imported if x["split"] == "TRAIN"],
            "eval": [x for x in imported if x["split"] == "EVAL"],
            "masked": dict(sorted(masked.items()))}


def _probabilities(vectors: list[dict[str, float]], weights: dict[str, float]) -> list[float]:
    logits = [sum(weights.get(k, 0.0) * v for k, v in vector.items()) for vector in vectors]
    if not logits:
        return []
    maximum = max(logits)
    values = [math.exp(logit - maximum) for logit in logits]
    total = sum(values)
    return [value / total for value in values]


def _groups(samples: list[dict]) -> list[list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for sample in samples:
        grouped[(sample["context_ref"], sample["batch_id"])].append(sample)
    return [sorted(group, key=lambda s: s["candidate_id"]) for _, group in sorted(grouped.items())
            if len(group) >= 2]


def _expected_reward(groups: list[list[dict]], weights: dict[str, float]) -> float | None:
    if not groups:
        return None
    values = [sum(p * s["reward"] for p, s in zip(
              _probabilities([s["features"] for s in group], weights), group)) for group in groups]
    return sum(values) / len(values)


def fit_policy(rows: list[dict], previous_state: dict | None = None, *, epochs: int = 30,
               learning_rate: float = 0.3, context_ref: str | None = None) -> dict:
    """Run exact CPU gradient ascent on verified contrast-set expected reward.

    For each contrast set J=Σ softmax(w·x_i) r_i and
    ∇J=Σ p_i(r_i−J)x_i. Batch gradients are deterministic. Singleton feedback
    cannot establish a relative preference and performs no weight update.
    The previous state supplies revision history, not potentially stale weights.
    """
    if not isinstance(epochs, int) or isinstance(epochs, bool) or not 1 <= epochs <= 500:
        raise PolicyDataError("epochs must be between 1 and 500")
    if not isinstance(learning_rate, (int, float)) or not math.isfinite(learning_rate) or not 0 < learning_rate <= 1:
        raise PolicyDataError("learning_rate must be finite and between 0 and 1")
    imported = import_eligible_export(rows, context_ref=context_ref)
    contexts = {sample["context_ref"] for sample in imported["train"] + imported["eval"]}
    if context_ref is None and len(contexts) > 1:
        raise PolicyDataError("train a separate policy per context_ref")
    resolved_context = context_ref if context_ref is not None else next(iter(contexts), None)
    if previous_state:
        _checked_digest(previous_state, "state_digest")
        if previous_state.get("context_ref") not in {None, resolved_context}:
            raise PolicyDataError("previous policy belongs to another context")
    groups = _groups(imported["train"])
    eval_groups = _groups(imported["eval"])
    weights: dict[str, float] = {}
    before_train = _expected_reward(groups, weights)
    before_eval = _expected_reward(eval_groups, weights)
    gradient_steps = 0
    for _ in range(epochs if groups else 0):
        gradient: dict[str, float] = defaultdict(float)
        for group in groups:
            probabilities = _probabilities([s["features"] for s in group], weights)
            expected = sum(p * s["reward"] for p, s in zip(probabilities, group))
            for probability, sample in zip(probabilities, group):
                for key, value in sample["features"].items():
                    gradient[key] += probability * (sample["reward"] - expected) * value / len(groups)
        if not any(abs(value) > 1e-15 for value in gradient.values()):
            break
        for key, value in gradient.items():
            weights[key] = weights.get(key, 0.0) + learning_rate * value
        gradient_steps += 1
    training_digest = digest(sorted(sample["reward_digest"] for sample in imported["train"]))
    hyperparameters = {"epochs": epochs, "learning_rate": float(learning_rate)}
    changed = not previous_state or previous_state.get("training_digest") != training_digest or \
        previous_state.get("hyperparameters") != hyperparameters
    old_revision = int((previous_state or {}).get("revision", 0))
    state = {"algorithm": VERSION, "context_ref": resolved_context,
             "revision": old_revision + (1 if changed else 0),
             "weights": dict(sorted(weights.items())), "training_digest": training_digest,
             "training_sample_ids": sorted(s["sample_id"] for s in imported["train"]),
             "training_receipt_ids": sorted(s["receipt_id"] for s in imported["train"]),
             "training_reason_digests": sorted(s["reason_digest"] for s in imported["train"]),
             "hyperparameters": hyperparameters,
             "feature_contract": "PREVERIFICATION_HYPOTHESIS_TYPE_AND_CONCEPT_REFS_ONLY",
             "reason_use": "CHECKED_EXPLANATION_GATES_HYPOTHESIS_REWARD_PAIR_NOT_POSTHOC_INPUT",
             "rank_used_as_feature": False, "base_model_updated": False,
             "rebuild_from_current_export": True,
             "probability_semantics": "EXPLORATION_PRIORITY_NOT_CORRECTNESS_PROBABILITY"}
    state["state_digest"] = digest(state)
    metrics = {"train_sample_count": len(imported["train"]),
               "eval_sample_count": len(imported["eval"]),
               "training_contrast_groups": len(groups), "evaluation_contrast_groups": len(eval_groups),
               "gradient_steps": gradient_steps, "masked": imported["masked"],
               "train_expected_reward_before": before_train,
               "train_expected_reward_after": _expected_reward(groups, weights),
               "eval_expected_reward_before": before_eval,
               "eval_expected_reward_after": _expected_reward(eval_groups, weights),
               "evaluation_updates_weights": False,
               "educational_efficacy_tested": False,
               "metric_scope": "VERIFIED_SYNTHETIC_OR_EXPORTED_CONTRAST_SETS_ONLY"}
    return {"state": state, "metrics": metrics}


def score_candidates(candidates: list[dict], state: dict, *, context_ref: str) -> list[dict]:
    """Compute content-based priority without rewriting original provider rank."""
    if not isinstance(candidates, list) or len(candidates) > 100:
        raise PolicyDataError("candidate pool must contain at most 100 entries")
    if state.get("context_ref") != context_ref:
        raise PolicyDataError("policy context mismatch")
    if state.get("algorithm") != VERSION:
        raise PolicyDataError("unsupported policy algorithm")
    _checked_digest(state, "state_digest")
    weights = state.get("weights", {})
    if not isinstance(weights, dict) or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in weights.values()):
        raise PolicyDataError("nonfinite policy weights")
    ids = [c.get("candidate_id") for c in candidates]
    if len(set(ids)) != len(ids):
        raise PolicyDataError("duplicate inference candidate identifiers")
    vectors = [candidate_features(candidate, context_ref) for candidate in candidates]
    probabilities = _probabilities(vectors, weights)
    entries = [{"candidate_id": candidate["candidate_id"], "original_rank": candidate.get("rank"),
                "policy_priority": probability,
                "policy_revision": state["revision"],
                "priority_semantics": "EXPLORATION_PRIORITY_NOT_CORRECTNESS_PROBABILITY"}
               for candidate, probability in zip(candidates, probabilities)]
    return sorted(entries, key=lambda e: (-e["policy_priority"], e["candidate_id"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_json", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--context-ref")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--learning-rate", type=float, default=0.3)
    args = parser.parse_args()
    rows = json.loads(args.export_json.read_text(encoding="utf-8"))
    result = fit_policy(rows, epochs=args.epochs, learning_rate=args.learning_rate,
                        context_ref=args.context_ref)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["metrics"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
