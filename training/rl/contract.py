"""Private, reviewed question-policy RL contract; stdlib-only preflight.

This is an action-selection experiment, not a claim of student learning gains.
No historical student outcome is assigned to a newly sampled teacher action.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

SCHEMA = "edurecover.reviewed_question_policy.v1"
SOURCES = {"OWNER_AUTHORED", "FIRST_PARTY_TUTORING", "LICENSED_CORPUS", "OPEN_WEIGHT_SELF_GENERATED"}

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def require(condition, message):
    if not condition:
        raise ValueError(message)

def text(value):
    return isinstance(value, str) and bool(value.strip())

def load_object(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    require(isinstance(value, dict), "Expected JSON object")
    return value

def validate_record(row):
    require(isinstance(row, dict), "Expected record object")
    for field in ("record_id", "group_id", "target_concept_id", "concept_plan_revision", "review_id"):
        require(text(row.get(field)), f"Missing {field}")
    require(row.get("review_status") == "HUMAN_APPROVED", "Human review required")
    require(row.get("reward_basis") == "REVIEWED_NEXT_QUESTION_QUALITY", "Historical transfer outcomes cannot score new actions")
    require(row.get("content_status") == "VALID", "Defective/unreviewed content cannot enter policy updates")
    require(row.get("split") in {"train", "eval"}, "Explicit train/eval split required")
    require(isinstance(row.get("source_refs"), list) and all(text(x) for x in row["source_refs"]) and row["source_refs"], "Source references required")
    dialogue = row.get("dialogue")
    require(isinstance(dialogue, list) and dialogue, "Actual reviewed dialogue/state is required")
    require(all(isinstance(m, dict) and m.get("role") in {"user", "assistant"} and text(m.get("content")) for m in dialogue), "Invalid dialogue")
    actions = row.get("actions")
    require(isinstance(actions, list) and 2 <= len(actions) <= 8, "Two to eight reviewed actions required")
    ids, scores = set(), set()
    for action in actions:
        require(isinstance(action, dict), "Invalid action")
        aid = action.get("action_id")
        require(isinstance(aid, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", aid), "Invalid action ID")
        require(aid not in ids, "Duplicate action ID")
        ids.add(aid)
        require(action.get("target_concept_id") == row["target_concept_id"], "Target concept changed")
        require(text(action.get("question")) and text(action.get("content_revision")), "Versioned question required")
        require(action.get("content_status") == "VALID", "Invalid question revision")
        require(action.get("answer_disclosed") is False, "Answer-revealing actions are excluded")
        score = action.get("reviewed_score")
        require(type(score) in {int, float} and math.isfinite(score) and -1 <= score <= 1, "Score must be finite and in [-1, 1]")
        require(text(action.get("score_rationale")), "Human score rationale required")
        scores.add(float(score))
    require(len(scores) > 1, "Equal scores supply no relative policy-learning signal")

def make_prompt(row):
    # Rewards, rationales, review IDs and provenance never enter the model prompt.
    state = {"target_concept_id": row["target_concept_id"], "dialogue": row["dialogue"],
             "questions": [{"action_id": a["action_id"], "question": a["question"]} for a in row["actions"]]}
    return [{"role": "system", "content": "Choose the next teaching question for the fixed target concept. Do not answer the student's problem. Return only a JSON object with the key action_id, choosing one listed ID."},
            {"role": "user", "content": json.dumps(state, ensure_ascii=False, sort_keys=True)}]

def validate_revocations(path, rows):
    current = load_object(path)
    require(current.get("schema") == "edurecover.revocations.v1", "Revocation file required")
    ids, revisions = current.get("record_ids"), current.get("content_revisions")
    require(isinstance(ids, list) and isinstance(revisions, list) and all(text(x) for x in ids + revisions), "Invalid revocation lists")
    require(text(current.get("review_id")), "Revocation review ID required")
    for row in rows:
        require(row["record_id"] not in ids, "Record revoked; abort this training run")
        refs = [row["concept_plan_revision"]] + [a["content_revision"] for a in row["actions"]]
        require(not set(refs).intersection(revisions), "Content revision revoked; abort this training run")
    return current

def load_bundle(dataset_path, manifest_path, revocations_path):
    manifest = load_object(manifest_path)
    require(manifest.get("schema") == SCHEMA, "Unknown dataset schema")
    require(manifest.get("training_status") == "ELIGIBLE", "Dataset is not approved")
    require(manifest.get("allowed_use_scopes") == ["TUTOR_POLICY"], "Tutor-only purpose required")
    require(manifest.get("rights_state") == "CONFIRMED" and manifest.get("consent_state") in {"CONFIRMED", "NOT_REQUIRED"}, "Rights/consent review incomplete")
    sources = manifest.get("source_classes")
    require(isinstance(sources, list) and sources and all(isinstance(s, str) for s in sources) and set(sources) <= SOURCES, "Unapproved source class")
    require(manifest.get("provenance_complete") is True, "Provenance incomplete")
    require(type(manifest.get("contains_personal_data")) is bool, "Personal-data review missing")
    if manifest["contains_personal_data"]:
        require(manifest.get("deidentified") is True and manifest.get("consent_state") == "CONFIRMED", "Personal-data review incomplete")
    if "FIRST_PARTY_TUTORING" in sources:
        require(manifest.get("patent_clearance") == "CLEARED_FOR_TUTOR_MODEL_TRAINING", "Existing tutor training review gate is not cleared")
    require(text(manifest.get("review_id")), "Manifest review ID required")
    require(manifest.get("dataset_file_sha256") == file_digest(dataset_path), "Dataset hash mismatch")
    require(manifest.get("reviewed_dataset_sha256") == manifest["dataset_file_sha256"], "Review is not bound to this dataset")
    unsigned = {k: v for k, v in manifest.items() if k != "snapshot_hash"}
    require(manifest.get("snapshot_hash") == digest(unsigned), "Snapshot metadata hash mismatch")
    rows = [json.loads(line) for line in Path(dataset_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    require(type(manifest.get("record_count")) is int and len(rows) == manifest["record_count"] and len(rows) >= 2, "Record count mismatch or missing eval")
    for row in rows:
        validate_record(row)
    require(len({r["record_id"] for r in rows}) == len(rows), "Duplicate record IDs")
    splits = {name: [r for r in rows if r["split"] == name] for name in ("train", "eval")}
    require(all(splits.values()), "Both train and untouched eval records required")
    require(not ({r["group_id"] for r in splits["train"]} & {r["group_id"] for r in splits["eval"]}), "Related source/session groups cross train/eval boundary")
    require(not ({digest(make_prompt(r)) for r in splits["train"]} & {digest(make_prompt(r)) for r in splits["eval"]}), "Duplicate prompt in training and evaluation")
    validate_revocations(revocations_path, rows)
    return rows, manifest

def score_completion(raw, row):
    # Decode with special tokens retained: never mine JSON from analysis text.
    marker = re.search(r"(?:<\|start\|>|<\|im_start\|>)assistant(?:<\|channel\|>|<\|meta_sep\|>)final(?:<\|message\|>|<\|im_sep\|>)", raw)
    if marker:
        raw = raw[marker.end():]
    for ending in ("<|return|>", "<|fim_suffix|>", "<|end|>", "<|im_end|>", "<|endoftext|>"):
        raw = raw.replace(ending, "")
    try:
        value = json.loads(raw.strip())
    except (ValueError, TypeError):
        return -1.0, False
    if not isinstance(value, dict) or set(value) != {"action_id"}:
        return -1.0, False
    for action in row["actions"]:
        if value["action_id"] == action["action_id"]:
            return float(action["reviewed_score"]), True
    return -1.0, False
