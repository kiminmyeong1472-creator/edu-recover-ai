"""Bounded, private GRPO/LoRA pilot for human-reviewed next-question selection.

No automatic promotion. No live student experiments. Requires an approved bundle.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from training.rl.contract import (file_digest, load_bundle, load_object, make_prompt,
                                  require, score_completion, validate_revocations)


def load_recipe(path):
    r = load_object(path)
    limits = {"max_steps": (1, 200), "max_prompt_length": (64, 2048),
              "max_completion_length": (32, 1024), "num_generations": (2, 4),
              "gradient_accumulation_steps": (2, 16), "lora_r": (1, 32),
              "lora_alpha": (1, 64), "seed": (0, 2**31 - 1)}
    for key, (low, high) in limits.items():
        require(type(r.get(key)) is int and low <= r[key] <= high, f"Invalid bounded recipe: {key}")
    require(r["gradient_accumulation_steps"] % r["num_generations"] == 0, "GRPO generation batch must be divisible by group size")
    require(type(r.get("learning_rate")) in {float, int} and 0 < r["learning_rate"] <= 0.0002, "Invalid learning rate")
    require(r.get("recipe_status") == "PILOT_NOT_PRODUCTION", "Pilot recipe required")
    return r


def parameter_digest(model):
    h = hashlib.sha256()
    count = 0
    for name, p in sorted(model.named_parameters()):
        if p.requires_grad:
            require(bool(__import__("torch").isfinite(p).all().item()), "Non-finite adapter weights; candidate must stay HOLD")
            h.update(name.encode())
            h.update(p.detach().cpu().contiguous().view(-1).view(__import__('torch').uint8).numpy().tobytes())
            count += p.numel()
    require(count > 0, "No trainable adapter parameters")
    return h.hexdigest(), count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "manifest", "revocations", "recipe", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--model-id", default="openai/gpt-oss-20b")
    parser.add_argument("--model-revision", required=True, help="Exact 40-character Hugging Face commit, never a moving branch")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    import re
    require(re.fullmatch(r"[0-9a-f]{40}", args.model_revision), "Pinned model revision required")
    rows, manifest = load_bundle(args.dataset, args.manifest, args.revocations)
    recipe = load_recipe(args.recipe)
    if args.check_only:
        print(json.dumps({"validation": "PASSED", "train_records": sum(r["split"] == "train" for r in rows),
                          "eval_records": sum(r["split"] == "eval" for r in rows),
                          "actual_training_started": False, "method": "GRPO_LORA_QUESTION_SELECTION"}))
        return

    os.umask(0o077)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["WANDB_DISABLED"] = "true"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    require(not args.output_dir.exists(), "Use a fresh output directory; never overwrite a previous run")
    args.output_dir.mkdir(parents=True, mode=0o700)
    receipt = {"method": "GRPO_LORA_QUESTION_SELECTION", "status": "PREFLIGHT",
               "reinforcement_learning": True, "actual_training_started": False,
               "production_promotion": False, "student_learning_effect_verified": False,
               "dataset_snapshot_hash": manifest["snapshot_hash"], "dataset_file_sha256": file_digest(args.dataset),
               "recipe_sha256": file_digest(args.recipe), "revocations_sha256_start": file_digest(args.revocations),
               "base_model": args.model_id, "base_revision": args.model_revision,
               "reward_basis": "REVIEWED_NEXT_QUESTION_QUALITY", "push_to_hub": False}
    receipt_path = args.output_dir / "edurecover_rl_receipt.json"

    def save_receipt():
        temp = receipt_path.with_suffix(".tmp")
        temp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temp.replace(receipt_path)

    def interrupted(signum, frame):
        raise KeyboardInterrupt("Training interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    save_receipt()
    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer, Mxfp4Config, TrainerCallback, set_seed
        from trl import GRPOConfig, GRPOTrainer

        require(torch.cuda.is_available() and torch.cuda.device_count() == 1, "Pilot needs exactly one visible CUDA GPU")
        require(int(os.getenv("WORLD_SIZE", "1")) == 1, "Distributed launch is not supported by this pilot")
        require(torch.cuda.is_bf16_supported(), "BF16 support required")
        gpu = torch.cuda.get_device_properties(0)
        require(gpu.total_memory >= 75 * 1024**3, "Pilot budget requires an 80GB-class GPU; 16GB inference is not a training budget")
        receipt["gpu"] = {"name": gpu.name, "total_memory_bytes": gpu.total_memory}
        receipt["packages"] = {p: importlib.metadata.version(p) for p in ("torch", "transformers", "trl", "peft", "datasets", "accelerate")}
        set_seed(recipe["seed"])
        tok = AutoTokenizer.from_pretrained(args.model_id, revision=args.model_revision, trust_remote_code=False)
        tok.padding_side = "left"
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token
        prompts = {}
        for row in rows:
            prompt = tok.apply_chat_template(make_prompt(row), tokenize=False, add_generation_prompt=True, reasoning_effort="low")
            length = len(tok(prompt, add_special_tokens=False)["input_ids"])
            require(length <= recipe["max_prompt_length"], "Reviewed prompt exceeds the token budget; do not silently truncate concept or dialogue")
            prompts[row["record_id"]] = prompt

        config = GRPOConfig(
            output_dir=str(args.output_dir), max_steps=recipe["max_steps"],
            learning_rate=recipe["learning_rate"], per_device_train_batch_size=1,
            gradient_accumulation_steps=recipe["gradient_accumulation_steps"],
            num_generations=recipe["num_generations"], max_prompt_length=recipe["max_prompt_length"],
            max_completion_length=recipe["max_completion_length"], bf16=True,
            gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False},
            optim="adamw_torch", weight_decay=0.0, lr_scheduler_type="constant", beta=0.0,
            loss_type="dr_grpo", use_vllm=False, remove_unused_columns=False,
            report_to="none", push_to_hub=False, log_completions=False,
            logging_steps=1, save_steps=5, save_total_limit=2,
            seed=recipe["seed"], data_seed=recipe["seed"])

        model = AutoModelForCausalLM.from_pretrained(
            args.model_id, revision=args.model_revision, trust_remote_code=False,
            quantization_config=Mxfp4Config(dequantize=True), torch_dtype=torch.bfloat16,
            attn_implementation="eager", device_map={"": 0}, use_cache=False)
        # Attention-only LoRA is the bounded pilot; expert-layer tuning remains a later measured experiment.
        model = get_peft_model(model, LoraConfig(r=recipe["lora_r"], lora_alpha=recipe["lora_alpha"],
                                               target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
                                               task_type="CAUSAL_LM"))
        before_hash, trainable = parameter_digest(model)
        receipt.update({"adapter_before_sha256": before_hash, "trainable_parameters": trainable})
        by_id = {r["record_id"]: r for r in rows}
        train_rows = [r for r in rows if r["split"] == "train"]
        eval_rows = [r for r in rows if r["split"] == "eval"]

        def evaluate():
            model.eval()
            scores, valid = [], 0
            for row in eval_rows:
                validate_revocations(args.revocations, rows)
                inputs = tok(prompts[row["record_id"]], return_tensors="pt", add_special_tokens=False).to("cuda")
                with torch.no_grad():
                    output = model.generate(**inputs, max_new_tokens=recipe["max_completion_length"],
                                            do_sample=False, pad_token_id=tok.pad_token_id)
                raw = tok.decode(output[0, inputs.input_ids.shape[1]:], skip_special_tokens=False)
                score, good = score_completion(raw, row)
                scores.append(score)
                valid += int(good)
                del inputs, output
            model.train()
            return {"records": len(scores), "mean_reviewed_action_score": sum(scores) / len(scores),
                    "valid_action_rate": valid / len(scores), "is_student_transfer_measurement": False}

        receipt["baseline_evaluation"] = evaluate()
        save_receipt()
        stats = {"completions": 0, "valid_actions": 0, "groups_with_reward_difference": 0}

        def reviewed_question_reward(completions, record_id, completion_ids, **kwargs):
            validate_revocations(args.revocations, rows)
            rewards = []
            for rid, ids in zip(record_id, completion_ids, strict=True):
                value, good = score_completion(tok.decode(ids, skip_special_tokens=False), by_id[rid])
                rewards.append(value)
                stats["completions"] += 1
                stats["valid_actions"] += int(good)
            group = recipe["num_generations"]
            for offset in range(0, len(rewards), group):
                if len(set(rewards[offset:offset + group])) > 1:
                    stats["groups_with_reward_difference"] += 1
            return rewards

        class ReviewCallback(TrainerCallback):
            def on_step_begin(self, args, state, control, **kwargs):
                validate_revocations(main_args.revocations, rows)
            def on_step_end(self, args, state, control, **kwargs):
                receipt["global_step"] = state.global_step
                receipt["reward_statistics"] = dict(stats)
                save_receipt()

        main_args = args
        trainer = GRPOTrainer(model=model, args=config, processing_class=tok,
                              train_dataset=Dataset.from_list([{"prompt": prompts[r["record_id"]], "record_id": r["record_id"]} for r in train_rows]),
                              reward_funcs=reviewed_question_reward, callbacks=[ReviewCallback()])
        # Re-read hashes immediately before updates. No data changes can ride along with a frozen approval.
        _, current_manifest = load_bundle(args.dataset, args.manifest, args.revocations)
        require(current_manifest["snapshot_hash"] == manifest["snapshot_hash"] and file_digest(args.recipe) == receipt["recipe_sha256"], "Reviewed input changed during model loading")
        receipt.update({"status": "RUNNING", "actual_training_started": True, "training_started_unix": time.time()})
        save_receipt()
        trainer.train()
        validate_revocations(args.revocations, rows)
        after_hash, _ = parameter_digest(model)
        receipt.update({"adapter_after_sha256": after_hash, "global_step": trainer.state.global_step,
                        "adapter_parameters_changed": before_hash != after_hash, "reward_statistics": stats})
        require(trainer.state.global_step > 0 and before_hash != after_hash and stats["groups_with_reward_difference"] > 0,
                "No demonstrated rewarded policy update; candidate must remain HOLD")
        receipt["candidate_evaluation"] = evaluate()
        receipt["holdout_score_improved"] = receipt["candidate_evaluation"]["mean_reviewed_action_score"] > receipt["baseline_evaluation"]["mean_reviewed_action_score"]
        trainer.save_model(str(args.output_dir / "candidate_adapter"))
        tok.save_pretrained(str(args.output_dir / "candidate_adapter"))
        receipt["adapter_files_sha256"] = {p.name: file_digest(p) for p in sorted((args.output_dir / "candidate_adapter").iterdir()) if p.is_file()}
        validate_revocations(args.revocations, rows)
        receipt["revocations_sha256_end"] = file_digest(args.revocations)
        receipt["status"] = "TRAINED_REQUIRES_INDEPENDENT_REVIEW"
    except BaseException as exc:
        receipt.update({"status": "HOLD", "error_type": type(exc).__name__,
                        "note": "Failure, interruption, missing hardware or invalid evidence; do not promote checkpoints."})
        raise
    finally:
        receipt["finished_unix"] = time.time()
        save_receipt()

if __name__ == "__main__":
    main()
