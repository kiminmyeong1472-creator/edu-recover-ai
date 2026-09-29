"""Synthetic unit fixtures only: not an approved EduRecover training corpus."""
import copy
import json
import subprocess
import sys
from pathlib import Path
import pytest
from training.rl.contract import (SCHEMA, digest, file_digest, load_bundle, make_prompt,
                                  score_completion, validate_record, validate_revocations)


def record(rid="fixture-train", split="train"):
    return {"record_id": rid, "group_id": rid, "target_concept_id": "fixture-concept",
            "concept_plan_revision": "fixture-plan-1", "review_id": "fixture-review-not-real",
            "review_status": "HUMAN_APPROVED", "reward_basis": "REVIEWED_NEXT_QUESTION_QUALITY",
            "content_status": "VALID", "split": split, "source_refs": ["synthetic-test-only"],
            "dialogue": [{"role": "user", "content": rid + ": I need a question."}],
            "actions": [{"action_id": "a", "question": "Which condition did you use?", "target_concept_id": "fixture-concept", "content_revision": "q1", "content_status": "VALID", "answer_disclosed": False, "reviewed_score": 1, "score_rationale": "fixture only"},
                        {"action_id": "b", "question": "Can you repeat that condition?", "target_concept_id": "fixture-concept", "content_revision": "q2", "content_status": "VALID", "answer_disclosed": False, "reviewed_score": 0, "score_rationale": "fixture only"}]}


@pytest.fixture
def bundle(tmp_path):
    rows = [record(), record("fixture-eval", "eval")]
    path = tmp_path / "records.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    m = {"schema": SCHEMA, "training_status": "ELIGIBLE", "allowed_use_scopes": ["TUTOR_POLICY"],
         "rights_state": "CONFIRMED", "consent_state": "NOT_REQUIRED", "source_classes": ["OWNER_AUTHORED"],
         "provenance_complete": True, "contains_personal_data": False, "review_id": "fixture-review",
         "dataset_file_sha256": file_digest(path), "reviewed_dataset_sha256": file_digest(path), "record_count": 2}
    m["snapshot_hash"] = digest(m)
    mp = tmp_path / "manifest.json"; mp.write_text(json.dumps(m))
    rp = tmp_path / "revocations.json"; rp.write_text(json.dumps({"schema": "edurecover.revocations.v1", "review_id": "fixture-review", "record_ids": [], "content_revisions": []}))
    return path, mp, rp, rows


def reseal(path, manifest, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows))
    m = json.loads(manifest.read_text()); m.pop("snapshot_hash")
    m["dataset_file_sha256"] = file_digest(path); m["reviewed_dataset_sha256"] = file_digest(path); m["snapshot_hash"] = digest(m)
    manifest.write_text(json.dumps(m))


def test_reviewed_bundle_passes_without_gpu_imports(bundle):
    p, m, r, rows = bundle
    assert load_bundle(p, m, r)[0] == rows


@pytest.mark.parametrize("field,value", [("content_status", "DEFECTIVE"), ("review_status", "AI_APPROVED"),
    ("reward_basis", "PAST_STUDENT_TRANSFER_SUCCESS"), ("dialogue", []), ("source_refs", [])])
def test_ineligible_events_are_excluded_not_zero_reward(field, value):
    row = record(); row[field] = value
    with pytest.raises(ValueError): validate_record(row)


@pytest.mark.parametrize("field,value", [("target_concept_id", "other"), ("answer_disclosed", True),
    ("content_status", "REVOKED"), ("reviewed_score", float("nan")), ("reviewed_score", True)])
def test_question_constraints(field, value):
    row = record(); row["actions"][0][field] = value
    with pytest.raises(ValueError): validate_record(row)


def test_metadata_and_data_tampering_both_fail(bundle):
    p, m, r, _ = bundle
    obj = json.loads(m.read_text()); obj["review_id"] = "changed"; m.write_text(json.dumps(obj))
    with pytest.raises(ValueError, match="metadata hash"): load_bundle(p, m, r)
    p.write_text(p.read_text() + " ")
    with pytest.raises(ValueError, match="Dataset hash"): load_bundle(p, m, r)


def test_related_sessions_cannot_leak_into_eval(bundle):
    p, m, r, rows = bundle
    rows[1]["group_id"] = rows[0]["group_id"]; reseal(p, m, rows)
    with pytest.raises(ValueError, match="groups cross"): load_bundle(p, m, r)


def test_exact_prompt_copy_fails_even_if_group_is_renamed(bundle):
    p, m, r, rows = bundle
    rows[1]["dialogue"] = copy.deepcopy(rows[0]["dialogue"]); reseal(p, m, rows)
    with pytest.raises(ValueError, match="Duplicate prompt"): load_bundle(p, m, r)


def test_revocations_checked_again_after_loading(bundle):
    p, m, r, rows = bundle
    load_bundle(p, m, r)
    obj = json.loads(r.read_text()); obj["content_revisions"] = ["q1"]; r.write_text(json.dumps(obj))
    with pytest.raises(ValueError, match="revoked"): validate_revocations(r, rows)


def test_no_reward_or_private_review_details_in_prompt():
    row = record(); prompt = json.dumps(make_prompt(row))
    assert "reviewed_score" not in prompt and "score_rationale" not in prompt and "fixture-review" not in prompt


def test_only_final_action_is_scored():
    row = record()
    assert score_completion('{"action_id":"a"}', row) == (1.0, True)
    assert score_completion('analysis {"action_id":"a"}', row) == (-1.0, False)
    raw = 'analysis {"action_id":"b"}<|im_start|>assistant<|meta_sep|>final<|im_sep|>{"action_id":"a"}<|fim_suffix|>'
    assert score_completion(raw, row) == (1.0, True)
    assert score_completion('{"action_id":"unknown"}', row) == (-1.0, False)
    assert score_completion('{"action_id":"a", "answer":"leak"}', row) == (-1.0, False)


def test_check_only_cannot_claim_training_started(bundle, tmp_path):
    p, m, r, _ = bundle
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "run"
    result = subprocess.run([sys.executable, str(root / "scripts/train_gpt_oss_grpo.py"),
        "--dataset", str(p), "--manifest", str(m), "--revocations", str(r),
        "--recipe", str(root / "training/recipes/gpt_oss_20b_grpo_pilot.json"),
        "--model-revision", "a" * 40, "--output-dir", str(output), "--check-only"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["actual_training_started"] is False
    assert not output.exists()


def test_sealer_requires_approval_of_exact_file(bundle, tmp_path):
    p, m, r, _ = bundle
    root = Path(__file__).resolve().parents[1]
    review = json.loads(m.read_text())
    review["reviewed_dataset_sha256"] = "0" * 64
    review_path = tmp_path / "review.json"; review_path.write_text(json.dumps(review))
    output = tmp_path / "sealed"
    cmd = [sys.executable, str(root / "scripts/prepare_rl_bundle.py"), "--records", str(p),
           "--review", str(review_path), "--revocations", str(r), "--output-dir", str(output)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode != 0 and not output.exists()
    review["reviewed_dataset_sha256"] = file_digest(p); review_path.write_text(json.dumps(review))
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["actual_training_started"] is False
    load_bundle(output / "records.jsonl", output / "manifest.json", output / "revocations.json")


def test_actual_gpu_path_records_hold_without_dependencies(bundle, tmp_path):
    # Isolate Python from installed site packages: a missing GPU runtime must not say TRAINED.
    p, m, r, _ = bundle
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "failed-run"
    result = subprocess.run([sys.executable, "-S", str(root / "scripts/train_gpt_oss_grpo.py"),
        "--dataset", str(p), "--manifest", str(m), "--revocations", str(r),
        "--recipe", str(root / "training/recipes/gpt_oss_20b_grpo_pilot.json"),
        "--model-revision", "a" * 40, "--output-dir", str(output)], capture_output=True, text=True)
    assert result.returncode != 0
    receipt = json.loads((output / "edurecover_rl_receipt.json").read_text())
    assert receipt["status"] == "HOLD" and receipt["actual_training_started"] is False
    assert receipt["production_promotion"] is False
