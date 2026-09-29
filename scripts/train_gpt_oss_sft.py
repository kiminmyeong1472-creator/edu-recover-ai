"""Local/private gpt-oss SFT/LoRA runner for EduRecover.

This script is intentionally excluded from the production Railway dependency set.
It never pushes a model or dataset to a public hub. Training data must already
be exported from the EduRecover eligibility/snapshot pipeline.

PyTorch must be installed separately for the target GPU/CUDA environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


BLOCKED_SOURCE_CLASSES = {
    "THIRD_PARTY_API_OUTPUT",
    "CHATGPT_APP_REQUEST",
    "UNKNOWN",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return value


def validate_manifest(
    *,
    dataset_path: Path,
    manifest: dict[str, Any],
) -> None:
    if manifest.get("training_status") != "ELIGIBLE":
        raise ValueError("Dataset manifest is not ELIGIBLE.")

    snapshot_hash = str(manifest.get("snapshot_hash", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot_hash):
        raise ValueError("Dataset snapshot_hash is missing or invalid.")

    expected_file_hash = str(manifest.get("dataset_file_sha256", ""))
    actual_file_hash = sha256_file(dataset_path)
    if expected_file_hash != actual_file_hash:
        raise ValueError(
            "Dataset file hash does not match the frozen manifest."
        )

    if int(manifest.get("record_count", 0)) < 1:
        raise ValueError("Dataset manifest contains no records.")

    source_classes = set(manifest.get("source_classes", []))
    allowed_sources = {"FIRST_PARTY_TUTORING", "OWNER_AUTHORED", "LICENSED_CORPUS", "OPEN_WEIGHT_SELF_GENERATED"}
    if not source_classes or not source_classes <= allowed_sources:
        raise ValueError("Explicit approved source classes are required.")
    if manifest.get("allowed_use_scopes") != ["TUTOR_POLICY"]:
        raise ValueError("Only the tutor policy training scope is supported by this runner.")
    if manifest.get("rights_state") != "CONFIRMED" or manifest.get("consent_state") not in {"CONFIRMED", "NOT_REQUIRED"}:
        raise ValueError("Rights and consent review is incomplete.")
    if "FIRST_PARTY_TUTORING" in source_classes and manifest.get("patent_clearance") != "CLEARED_FOR_TUTOR_MODEL_TRAINING":
        raise ValueError("First-party tutoring data has not passed the existing training review gate.")
    blocked = sorted(source_classes & BLOCKED_SOURCE_CLASSES)
    if blocked:
        raise ValueError(
            "Dataset contains blocked source classes: " + ", ".join(blocked)
        )

    if manifest.get("contains_personal_data") and not manifest.get(
        "deidentified"
    ):
        raise ValueError("Personal training data is not deidentified.")

    if manifest.get("provenance_complete") is not True:
        raise ValueError("Dataset provenance is incomplete.")
    count = 0
    with dataset_path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            record = json.loads(line)
            messages = record.get("messages") if isinstance(record, dict) else None
            if not isinstance(messages, list) or not messages:
                raise ValueError("Every record must contain an explicit training conversation.")
            if any(not isinstance(m, dict) or m.get("role") not in {"system", "user", "assistant"}
                   or not isinstance(m.get("content"), str) or not m["content"].strip() for m in messages):
                raise ValueError("Invalid training conversation.")
            if not any(m["role"] == "assistant" for m in messages):
                raise ValueError("A teacher target response is required; evidence metadata alone is not SFT data.")
            count += 1
    if count != manifest["record_count"]:
        raise ValueError("Dataset record count differs from the reviewed manifest.")


def load_recipe(path: Path) -> dict[str, Any]:
    recipe = load_json(path)
    required = {
        "recipe_id",
        "learning_rate",
        "num_train_epochs",
        "per_device_train_batch_size",
        "gradient_accumulation_steps",
        "max_length",
        "lora_r",
        "lora_alpha",
        "target_parameters",
    }
    missing = sorted(required - set(recipe))
    if missing:
        raise ValueError("Recipe missing fields: " + ", ".join(missing))
    return recipe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true", help="Validate data and recipe without importing GPU libraries or starting training.")
    parser.add_argument(
        "--model-id",
        default="openai/gpt-oss-20b",
        help="Replaceable open-weight base model.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = load_json(args.manifest)
    recipe = load_recipe(args.recipe)
    validate_manifest(dataset_path=args.dataset, manifest=manifest)
    if args.check_only:
        print(json.dumps({"validation": "passed", "record_count": manifest["record_count"],
                          "gpu_training_started": False, "method": "SFT_LORA"}))
        return

    # Heavy dependencies are imported only after all fail-closed data checks.
    import torch
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer, Mxfp4Config
    from trl import SFTConfig, SFTTrainer

    dataset = load_dataset(
        "json",
        data_files=str(args.dataset),
        split="train",
    )
    if "messages" not in dataset.column_names:
        raise ValueError(
            "Training JSONL must contain a 'messages' field compatible with "
            "the gpt-oss chat template."
        )

    tokenizer = AutoTokenizer.from_pretrained(args.model_id)

    quantization_config = Mxfp4Config(dequantize=True)
    model = AutoModelForCausalLM.from_pretrained(
        args.model_id,
        attn_implementation="eager",
        torch_dtype=torch.bfloat16,
        quantization_config=quantization_config,
        use_cache=False,
        device_map="auto",
    )

    peft_config = LoraConfig(
        r=int(recipe["lora_r"]),
        lora_alpha=int(recipe["lora_alpha"]),
        target_modules="all-linear",
        target_parameters=list(recipe["target_parameters"]),
    )
    model = get_peft_model(model, peft_config)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    training_args = SFTConfig(
        learning_rate=float(recipe["learning_rate"]),
        gradient_checkpointing=True,
        num_train_epochs=float(recipe["num_train_epochs"]),
        logging_steps=int(recipe.get("logging_steps", 1)),
        per_device_train_batch_size=int(
            recipe["per_device_train_batch_size"]
        ),
        gradient_accumulation_steps=int(
            recipe["gradient_accumulation_steps"]
        ),
        max_length=int(recipe["max_length"]),
        warmup_ratio=float(recipe.get("warmup_ratio", 0.03)),
        lr_scheduler_type=str(
            recipe.get("lr_scheduler_type", "cosine_with_min_lr")
        ),
        lr_scheduler_kwargs=recipe.get(
            "lr_scheduler_kwargs",
            {"min_lr_rate": 0.1},
        ),
        output_dir=str(args.output_dir),
        report_to="none",
        push_to_hub=False,
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        processing_class=tokenizer,
    )
    trainer.train()
    trainer.save_model(str(args.output_dir))

    output_hashes = {}
    for path in sorted(args.output_dir.rglob("*")):
        if path.is_file():
            output_hashes[str(path.relative_to(args.output_dir))] = (
                sha256_file(path)
            )

    receipt = {
        "method": "SFT_LORA",
        "reinforcement_learning": False,
        "base_model": args.model_id,
        "dataset_snapshot_hash": manifest["snapshot_hash"],
        "dataset_file_sha256": manifest["dataset_file_sha256"],
        "recipe_id": recipe["recipe_id"],
        "output_files_sha256": output_hashes,
        "push_to_hub": False,
    }
    (args.output_dir / "edurecover_training_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

