"""Offline learning example; check-only by default and never auto-activate."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.dreamkeeper_training import (
    file_digest, fingerprint_trainable, load_bundle, load_object, load_object_snapshot,
    validate_model_rights, validate_recipe, weight_change_receipt,
)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--current-state", type=Path, required=True)
    p.add_argument("--recipe", type=Path, required=True)
    p.add_argument("--model-path", type=Path, required=True)
    p.add_argument("--output-dir", type=Path)
    action = p.add_mutually_exclusive_group()
    action.add_argument("--train", action="store_true", help="Actually change local model weights")
    action.add_argument("--check-only", action="store_true", help="Default: review inputs without ML imports")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    rows, manifest, current = load_bundle(args.dataset, args.manifest, args.current_state)
    recipe, frozen_recipe = load_object_snapshot(args.recipe)
    model_path = validate_recipe(recipe, args.model_path, current)
    if not args.train:
        print(json.dumps({"validation": "passed", "training_started": False,
                          "mode": recipe["mode"], "record_count": len(rows)}))
        return 0
    if args.output_dir is None:
        raise ValueError("--output-dir is required for actual training")
    output = args.output_dir.resolve()
    if output.exists() or output == model_path or model_path.is_relative_to(output) or output.is_relative_to(model_path):
        raise ValueError("Use a new output directory outside the model's parent tree")
    if int(os.environ.get("WORLD_SIZE", "1")) != 1:
        raise ValueError("This receipt implementation supports one training process")
    # Explicit offline flags plus local_files_only prevent implicit downloads.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments, TrainerCallback

    tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(str(model_path), local_files_only=True,
                                               trust_remote_code=False, use_safetensors=True)
    # Recheck the reviewed local input after the loader has consumed it.
    validate_recipe(recipe, model_path, load_object(args.current_state))
    if recipe["mode"] == "additional":
        from peft import LoraConfig, get_peft_model
        cfg = recipe["adapter"]
        model = get_peft_model(model, LoraConfig(task_type="CAUSAL_LM", r=cfg["r"],
                                                lora_alpha=cfg["alpha"], target_modules=cfg["target_modules"]))
    else:
        for parameter in model.parameters():
            parameter.requires_grad_(True)
    if tokenizer.pad_token_id is None:
        if tokenizer.eos_token_id is None:
            raise ValueError("Tokenizer needs an existing padding or EOS token")
        tokenizer.pad_token = tokenizer.eos_token

    def encode_record(row):
        messages = [{"role": "user", "content": row["prompt"]}]
        prompt_ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)
        all_ids = tokenizer.apply_chat_template(messages + [{"role": "assistant", "content": row["target"]}],
                                                tokenize=True, add_generation_prompt=False)
        if all_ids[:len(prompt_ids)] != prompt_ids or len(all_ids) <= len(prompt_ids):
            raise ValueError("Chat template cannot establish an exact assistant-target boundary")
        if len(all_ids) > recipe["max_length"]:
            raise ValueError("A reviewed case exceeds max_length; do not silently truncate its context")
        return {"input_ids": all_ids, "labels": [-100] * len(prompt_ids) + all_ids[len(prompt_ids):]}

    data = {split: [encode_record(r) for r in rows if r["split"] == split] for split in ("train", "eval")}

    def collate(batch):
        width = max(len(x["input_ids"]) for x in batch)
        return {"input_ids": torch.tensor([x["input_ids"] + [tokenizer.pad_token_id] * (width-len(x["input_ids"])) for x in batch]),
                "attention_mask": torch.tensor([[1] * len(x["input_ids"]) + [0] * (width-len(x["input_ids"])) for x in batch]),
                "labels": torch.tensor([x["labels"] + [-100] * (width-len(x["labels"])) for x in batch])}

    def tensor_bytes(parameter):
        return parameter.detach().cpu().contiguous().reshape(-1).view(torch.uint8).numpy().tobytes()

    before = fingerprint_trainable(model, tensor_bytes)
    frozen_snapshot = manifest["snapshot_hash"]
    frozen_state = file_digest(args.current_state)

    def recheck():
        _, now, current_rights = load_bundle(args.dataset, args.manifest, args.current_state)
        validate_model_rights(recipe, current_rights)
        if (now["snapshot_hash"] != frozen_snapshot or file_digest(args.current_state) != frozen_state
                or file_digest(args.recipe) != frozen_recipe):
            raise ValueError("Learning authorization changed; keep this run inactive")

    class CurrentPermissionGuard(TrainerCallback):
        def on_step_begin(self, *unused, **kwargs):
            recheck()

    recheck()
    training_args = TrainingArguments(output_dir=str(output), learning_rate=recipe["learning_rate"],
                                      num_train_epochs=recipe["num_train_epochs"],
                                      per_device_train_batch_size=recipe["batch_size"],
                                      per_device_eval_batch_size=recipe["batch_size"],
                                      eval_strategy="no", save_strategy="no", report_to=[],
                                      push_to_hub=False, seed=int(recipe.get("seed", 0)))
    trainer = Trainer(model=model, args=training_args, train_dataset=data["train"],
                      eval_dataset=data["eval"], data_collator=collate, processing_class=tokenizer,
                      callbacks=[CurrentPermissionGuard()])
    trainer.train()
    recheck()
    change = weight_change_receipt(before, fingerprint_trainable(model, tensor_bytes))
    metrics = trainer.evaluate()
    recheck()
    validate_recipe(recipe, model_path, load_object(args.current_state))
    trainer.save_model(str(output))
    tokenizer.save_pretrained(str(output))
    receipt = {**change, "training_method": "SUPERVISED_EXAMPLE", "mode": recipe["mode"],
               "model_revision": recipe["model_revision"], "model_files_sha256": recipe["model_files_sha256"],
               "model_rights_review_id": recipe["model_rights_review_id"], "recipe": recipe,
               "dataset_snapshot_hash": frozen_snapshot, "current_state_sha256": frozen_state,
               "permission_revision": current["permission_revision"], "independent_holdout_loss": metrics,
               "record_consumption": [{"record_id": r["record_id"], "split": r["split"],
                                       "consumed_fields": r["consumed_fields"]} for r in rows],
               "output_files_sha256": {str(p.relative_to(output)): file_digest(p)
                                         for p in output.rglob("*") if p.is_file()},
               "training_started": True, "automatically_activated": False}
    (output / "dreamkeeper_training_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"weight_change_observed": True, "activation_status": change["activation_status"],
                      "receipt": str(output / "dreamkeeper_training_receipt.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
