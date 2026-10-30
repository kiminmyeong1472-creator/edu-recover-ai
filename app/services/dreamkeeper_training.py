"""Reviewed learning inputs and weight-change evidence; no model/network imports.

This contract is separate from the existing tutor-question policy contract. It
does not widen TUTOR_POLICY or approve provider output for model training.
Review records are supplied by a trusted host, not authenticated by booleans.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

SCHEMA = "edurecover.dreamkeeper_learning.v1"
PURPOSES = {"GOAL_MAINTENANCE", "ERROR_CLASSIFICATION", "CREATIVE_REINTERPRETATION"}
SOURCE_CLASSES = {"OWNER_AUTHORED", "LICENSED_CORPUS", "FIRST_PARTY_TUTORING",
                  "OPEN_WEIGHT_SELF_GENERATED"}
DISPOSITIONS = {"HOLD_ORIGINAL", "CORRECT_ORIGINAL", "VERIFIED_ALTERNATIVE"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_object(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    require(isinstance(value, dict), "Expected a JSON object")
    return value


def load_object_snapshot(path):
    """Bind the parsed object to the digest of the exact same read bytes."""
    raw = Path(path).read_bytes()
    value = json.loads(raw)
    require(isinstance(value, dict), "Expected a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def record_digest(row):
    return digest({k: v for k, v in row.items() if k != "reviewed_record_sha256"})


def validate_record(row, current, manifest):
    require(isinstance(row, dict), "Expected a learning record")
    for name in ("record_id", "family_id", "logical_id", "task_id", "review_id", "prompt", "target"):
        require(text(row.get(name)), "Missing " + name)
    require(row.get("split") in {"train", "eval"}, "Explicit train/eval split required")
    require(type(row.get("context_revision")) is int and row["context_revision"] > 0,
            "A positive context revision is required")
    require(row.get("review_status") == "APPROVED" and row.get("verification") == "VERIFIED",
            "Unreviewed or unverified cases cannot be learning labels")
    require(row.get("disposition") in DISPOSITIONS, "Unknown disposition cannot become a negative label")
    require(row.get("purpose") in PURPOSES and row["purpose"] in manifest["allowed_learning_purposes"],
            "Learning purpose is not allowed")
    require(row.get("provenance_complete") is True, "Actual consumed fields must be complete")
    require(row.get("rights_state") == "CONFIRMED" and
            row.get("consent_state") in {"CONFIRMED", "NOT_REQUIRED"}, "Rights/consent review incomplete")
    require(type(row.get("contains_personal_data")) is bool, "Personal-data review missing")
    if row["contains_personal_data"]:
        require(row.get("deidentified") is True and row["consent_state"] == "CONFIRMED",
                "Personal learning records require reviewed deidentification and consent")
    require(row.get("reviewed_record_sha256") == record_digest(row), "Record changed after review")
    require(row["record_id"] not in current["revoked_record_ids"], "Learning record has been revoked")
    expected_context = current["contexts"].get(row["logical_id"], {}).get(row["task_id"])
    require(expected_context == row["context_revision"], "Context correction changed after review")
    refs = row.get("consumed_fields")
    require(isinstance(refs, list) and refs, "Actual consumed source fields are required")
    seen = set()
    for ref in refs:
        require(isinstance(ref, dict), "Invalid consumed-field reference")
        for name in ("source_id", "field", "revision", "rights_review_id", "family_id"):
            require(text(ref.get(name)), "Missing source " + name)
        key = (ref["source_id"], ref["field"])
        require(key not in seen, "Duplicate consumed field")
        seen.add(key)
        require(ref.get("source_class") in SOURCE_CLASSES,
                "Third-party API output, app chat output and unknown sources are not approved")
        source = current["sources"].get(ref["source_id"], {}).get(ref["field"])
        require(isinstance(source, dict) and source.get("revision") == ref["revision"],
                "Consumed source field is absent or has changed")
        require(source.get("learning_allowed") is True and source.get("rights_review_id") == ref["rights_review_id"],
                "Current learning right is absent or changed")
        require(row["purpose"] in source.get("allowed_learning_purposes", []), "Source purpose is not allowed")
        require(source.get("source_class") == ref["source_class"] and
                source.get("family_id") == ref["family_id"], "Source lineage differs from current review")
        if ref["source_class"] == "FIRST_PARTY_TUTORING":
            require(manifest.get("patent_clearance") == "CLEARED_FOR_TUTOR_MODEL_TRAINING",
                    "Existing tutoring learning clearance is still required")


def load_bundle(dataset_path, manifest_path, current_state_path):
    """Recheck reviewed bytes, present permissions and source-family separation."""
    manifest, current = load_object(manifest_path), load_object(current_state_path)
    require(manifest.get("schema") == SCHEMA, "Unknown learning contract")
    require(manifest.get("training_status") == "ELIGIBLE", "Learning bundle still needs host review")
    require(text(manifest.get("review_id")), "Manifest review missing")
    purposes = manifest.get("allowed_learning_purposes")
    require(isinstance(purposes, list) and purposes and set(purposes) <= PURPOSES,
            "Explicit learning purposes required; TUTOR_POLICY is not substituted")
    require(current.get("schema") == "edurecover.dreamkeeper_learning_current.v1",
            "Current source and permission state required")
    require(type(current.get("permission_revision")) is int and current["permission_revision"] > 0,
            "Current permission revision missing")
    require(manifest.get("permission_revision") == current["permission_revision"], "Permission revision changed")
    require(text(current.get("review_id")), "Current rights review missing")
    require(isinstance(current.get("contexts"), dict) and isinstance(current.get("sources"), dict),
            "Current context/source records missing")
    require(isinstance(current.get("revoked_record_ids"), list) and
            all(text(x) for x in current["revoked_record_ids"]), "Explicit revocation list required")
    dataset_bytes = Path(dataset_path).read_bytes()
    actual = hashlib.sha256(dataset_bytes).hexdigest()
    require(manifest.get("dataset_file_sha256") == actual and manifest.get("reviewed_dataset_sha256") == actual,
            "Dataset differs from reviewed bytes")
    unsigned = {k: v for k, v in manifest.items() if k != "snapshot_hash"}
    require(manifest.get("snapshot_hash") == digest(unsigned), "Manifest metadata changed")
    rows = [json.loads(line) for line in dataset_bytes.decode("utf-8").splitlines() if line.strip()]
    require(type(manifest.get("record_count")) is int and len(rows) == manifest["record_count"],
            "Record count differs from the frozen list")
    require(all(isinstance(r, dict) for r in rows), "Invalid record object")
    require(len(rows) >= 2 and len({r.get("record_id") for r in rows}) == len(rows),
            "Unique train and eval records required")
    for row in rows:
        validate_record(row, current, manifest)
    splits = {name: [r for r in rows if r["split"] == name] for name in ("train", "eval")}
    require(all(splits.values()), "An untouched evaluation split is required")
    # Family IDs include input origins, not only the conversation's own group.
    def families(records):
        return {family for r in records for family in
                [r["family_id"], *(x["family_id"] for x in r["consumed_fields"])]}
    require(not families(splits["train"]) & families(splits["eval"]),
            "Related events or source derivatives cross the train/eval boundary")
    def normalized_prompt(row):
        return " ".join(row["prompt"].split()).casefold()
    require(not {normalized_prompt(r) for r in splits["train"]} &
            {normalized_prompt(r) for r in splits["eval"]}, "Duplicate input crosses train/eval boundary")
    return rows, manifest, current


def write_draft_bundle(rows, output_directory, allowed_learning_purposes):
    """Write a review draft only. Creating files does not grant eligibility."""
    require(isinstance(rows, list) and rows, "Rows required")
    purposes = list(allowed_learning_purposes)
    require(purposes and set(purposes) <= PURPOSES, "Unknown learning purpose")
    destination = Path(output_directory)
    destination.mkdir(parents=True, exist_ok=False)
    dataset = destination / "records.jsonl"
    dataset.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
                               for r in rows), encoding="utf-8")
    manifest = {"schema": SCHEMA, "training_status": "PENDING_REVIEW", "record_count": len(rows),
                "dataset_file_sha256": file_digest(dataset), "allowed_learning_purposes": purposes,
                "training_started": False}
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"dataset": str(dataset), "manifest": str(destination / "manifest.json"),
            "training_status": "PENDING_REVIEW", "training_started": False}


def validate_model_rights(recipe, current):
    model = current.get("models", {}).get(recipe.get("model_revision"))
    require(isinstance(model, dict) and model.get("training_allowed") is True,
            "Current model training right is absent or revoked")
    require(model.get("rights_review_id") == recipe.get("model_rights_review_id") and
            model.get("model_files_sha256") == recipe.get("model_files_sha256"),
            "Model rights review or model revision has changed")


def validate_recipe(recipe, model_directory, current=None):
    require(isinstance(recipe, dict), "Recipe required")
    for key in ("recipe_id", "model_revision", "model_rights_review_id"):
        require(text(recipe.get(key)), "Missing " + key)
    require(recipe.get("model_training_rights") == "CONFIRMED", "Model training rights need review")
    if current is not None:
        validate_model_rights(recipe, current)
    require(recipe.get("mode") in {"full", "additional"}, "Choose full or additional weights")
    require(type(recipe.get("num_train_epochs")) is int and recipe["num_train_epochs"] >= 1,
            "This runner requires one or more complete epochs")
    for key in ("learning_rate",):
        value = recipe.get(key)
        require(type(value) in {int, float} and 0 < value < float("inf"), "Invalid " + key)
    for key in ("batch_size", "max_length"):
        require(type(recipe.get(key)) is int and recipe[key] > 0, "Invalid " + key)
    root = Path(model_directory).resolve(strict=True)
    require(root.is_dir(), "A local model directory is required")
    files = recipe.get("model_files_sha256")
    require(isinstance(files, dict) and files and "config.json" in files and
            any(k.endswith(".safetensors") for k in files), "Reviewed config and safe weight files required")
    for name, expected in files.items():
        relative = Path(name)
        require(not relative.is_absolute() and ".." not in relative.parts, "Invalid model file path")
        target = (root / relative).resolve(strict=True)
        require(target.is_relative_to(root) and target.is_file(), "Model file escapes local directory")
        require(file_digest(target) == expected, "Local model bytes differ from reviewed revision")
    # All files a loader might consume must be covered by the review.
    actual_files = {str(x.relative_to(root)) for x in root.rglob("*") if x.is_file()}
    require(actual_files == set(files), "Every local model/tokenizer file must be reviewed")
    if recipe["mode"] == "additional":
        cfg = recipe.get("adapter")
        require(isinstance(cfg, dict) and cfg.get("method") == "lora",
                "This runner's additional-weight example requires an explicit LoRA recipe")
        require(type(cfg.get("r")) is int and cfg["r"] > 0 and type(cfg.get("alpha")) is int and cfg["alpha"] > 0,
                "Invalid adapter rank or scale")
        require(isinstance(cfg.get("target_modules"), list) and cfg["target_modules"] and
                all(text(x) for x in cfg["target_modules"]), "Explicit adapter target modules required")
    return root


def fingerprint_trainable(model, encode: Callable):
    """Hash actual trainable tensors using a host-provided byte encoder."""
    result = {}
    for name, parameter in model.named_parameters():
        if parameter.requires_grad:
            raw = encode(parameter)
            require(isinstance(raw, bytes), "Tensor encoder must return actual bytes")
            result[name] = hashlib.sha256(raw).hexdigest()
    require(result, "No trainable weights")
    return result


def weight_change_receipt(before, after):
    require(isinstance(before, dict) and before and set(before) == set(after),
            "Trainable weight set changed during the run")
    changed = sorted(k for k in before if before[k] != after[k])
    require(changed, "No actual trainable weight change; do not report training success")
    return {"before_trainable_sha256": before, "after_trainable_sha256": after,
            "changed_parameter_names": changed, "weight_change_observed": True,
            "activation_status": "PENDING_BEHAVIORAL_REVIEW"}
