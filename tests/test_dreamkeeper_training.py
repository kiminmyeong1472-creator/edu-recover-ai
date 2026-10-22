"""Synthetic preflight and real-byte comparison tests; no downloaded model."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from app.services.dreamkeeper_training import (
    SCHEMA, digest, file_digest, fingerprint_trainable, load_bundle, record_digest,
    validate_recipe, weight_change_receipt, write_draft_bundle,
)


class LearningContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dataset = self.root / "records.jsonl"
        self.manifest_path = self.root / "manifest.json"
        self.current_path = self.root / "current.json"
        self.rows = []
        sources = {}
        for i, split in enumerate(("train", "eval")):
            source_id = "source-" + str(i)
            family_id = "family-" + str(i)
            ref = {"source_id": source_id, "field": "context", "revision": "r1",
                   "source_class": "OWNER_AUTHORED", "rights_review_id": "test-rights",
                   "family_id": family_id}
            sources[source_id] = {"context": {**ref, "learning_allowed": True,
                                               "allowed_learning_purposes": ["GOAL_MAINTENANCE"]}}
            self.rows.append({"record_id": str(i), "family_id": family_id, "logical_id": "L1",
                              "task_id": "T1", "context_revision": 1, "review_id": "synthetic-review",
                              "review_status": "APPROVED", "verification": "VERIFIED", "split": split,
                              "prompt": "Synthetic context " + str(i), "target": "Keep the pending goal " + str(i),
                              "disposition": "CORRECT_ORIGINAL", "purpose": "GOAL_MAINTENANCE",
                              "provenance_complete": True, "rights_state": "CONFIRMED",
                              "consent_state": "NOT_REQUIRED", "contains_personal_data": False,
                              "consumed_fields": [ref]})
        self.current = {"schema": "edurecover.dreamkeeper_learning_current.v1", "permission_revision": 1,
                        "review_id": "synthetic-current", "contexts": {"L1": {"T1": 1}},
                        "sources": sources, "revoked_record_ids": []}
        self.manifest = {"schema": SCHEMA, "training_status": "ELIGIBLE", "review_id": "synthetic-manifest",
                         "permission_revision": 1, "record_count": 2,
                         "allowed_learning_purposes": ["GOAL_MAINTENANCE"]}
        self.write()
        self.model = self.root / "model"
        self.model.mkdir()
        (self.model / "config.json").write_text("{}")
        (self.model / "model.safetensors").write_bytes(b"synthetic-file-not-a-real-model")
        self.recipe = {"recipe_id": "synthetic-recipe", "model_revision": "synthetic-v1",
                       "model_rights_review_id": "synthetic-model-rights", "model_training_rights": "CONFIRMED",
                       "mode": "full", "learning_rate": 0.01, "num_train_epochs": 1,
                       "batch_size": 1, "max_length": 32,
                       "model_files_sha256": {p.name: file_digest(p) for p in self.model.iterdir()}}
        self.current["models"] = {"synthetic-v1": {"training_allowed": True,
                                                  "rights_review_id": "synthetic-model-rights",
                                                  "model_files_sha256": self.recipe["model_files_sha256"]}}
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        for row in self.rows:
            row["reviewed_record_sha256"] = record_digest(row)
        self.dataset.write_text("".join(json.dumps(r) + "\n" for r in self.rows))
        sha = file_digest(self.dataset)
        self.manifest.update(dataset_file_sha256=sha, reviewed_dataset_sha256=sha)
        self.manifest["snapshot_hash"] = digest({k: v for k, v in self.manifest.items() if k != "snapshot_hash"})
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.current_path.write_text(json.dumps(self.current))

    def load(self):
        return load_bundle(self.dataset, self.manifest_path, self.current_path)

    def rejected(self):
        self.write()
        with self.assertRaises(ValueError):
            self.load()

    def test_valid_reviewed_context_cases(self):
        rows, manifest, _ = self.load()
        self.assertEqual(len(rows), 2)
        self.assertEqual(manifest["allowed_learning_purposes"], ["GOAL_MAINTENANCE"])

    def test_pending_review_and_old_tutor_scope_are_not_approved(self):
        for change in ({"training_status": "PENDING_REVIEW"}, {"allowed_learning_purposes": ["TUTOR_POLICY"]}):
            original = deepcopy(self.manifest)
            self.manifest.update(change)
            self.rejected()
            self.manifest = original

    def test_provider_output_cannot_be_laundered_as_owned(self):
        self.rows[0]["consumed_fields"][0]["source_class"] = "THIRD_PARTY_API_OUTPUT"
        self.current["sources"]["source-0"]["context"]["source_class"] = "THIRD_PARTY_API_OUTPUT"
        self.rejected()

    def test_unreviewed_and_unknown_outcomes_are_not_negative_labels(self):
        for field, value in (("verification", "UNKNOWN"), ("review_status", "UNREVIEWED"),
                             ("disposition", "UNTESTED"), ("rights_state", "UNKNOWN")):
            original = deepcopy(self.rows)
            self.rows[0][field] = value
            self.rejected()
            self.rows = original

    def test_current_permission_revocation_and_context_change(self):
        self.current["revoked_record_ids"] = ["0"]
        self.rejected()
        self.current["revoked_record_ids"] = []
        self.current["contexts"]["L1"]["T1"] = 2
        self.rejected()

    def test_current_source_field_and_learning_rights_are_rechecked(self):
        self.current["sources"]["source-0"]["context"]["revision"] = "r2"
        self.rejected()
        self.current["sources"]["source-0"]["context"]["revision"] = "r1"
        self.current["sources"]["source-0"]["context"]["learning_allowed"] = False
        self.rejected()

    def test_source_family_or_event_family_crossing_is_rejected(self):
        self.rows[1]["family_id"] = "family-0"
        self.rejected()
        self.rows[1]["family_id"] = "family-1"
        self.rows[1]["consumed_fields"][0]["family_id"] = "family-0"
        self.current["sources"]["source-1"]["context"]["family_id"] = "family-0"
        self.rejected()

    def test_duplicate_input_is_rejected_even_with_different_family_ids(self):
        self.rows[1]["prompt"] = " Synthetic   CONTEXT 0 "
        self.rejected()

    def test_dataset_and_record_tampering_are_detected(self):
        with self.dataset.open("a") as target:
            target.write("\n")
        with self.assertRaisesRegex(ValueError, "reviewed bytes"):
            self.load()
        self.write()
        data = json.loads(self.dataset.read_text().splitlines()[0])
        data["target"] = "secretly changed target"
        lines = self.dataset.read_text().splitlines()
        self.dataset.write_text(json.dumps(data) + "\n" + lines[1] + "\n")
        self.manifest["dataset_file_sha256"] = file_digest(self.dataset)
        self.manifest["reviewed_dataset_sha256"] = self.manifest["dataset_file_sha256"]
        self.manifest["snapshot_hash"] = digest({k: v for k, v in self.manifest.items() if k != "snapshot_hash"})
        self.manifest_path.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError, "Record changed"):
            self.load()

    def test_dataset_replacement_after_read_cannot_change_the_verified_rows(self):
        replacement = deepcopy(self.rows)
        replacement[0]["target"] = "A target absent from the reviewed dataset"
        replacement[0]["reviewed_record_sha256"] = record_digest(replacement[0])
        replacement_bytes = "".join(json.dumps(row) + "\n" for row in replacement).encode()
        original_open = Path.open
        swapped = False

        @contextmanager
        def swap_after_read(path, *args, **kwargs):
            nonlocal swapped
            with original_open(path, *args, **kwargs) as source:
                yield source
            mode = args[0] if args else kwargs.get("mode", "r")
            if path == self.dataset and mode == "rb" and not swapped:
                swapped = True
                with original_open(path, "wb") as target:
                    target.write(replacement_bytes)

        # The former two-open implementation hashed the original file but
        # parsed the replacement. A single byte snapshot must retain the first.
        with patch.object(Path, "open", swap_after_read):
            loaded, _, _ = self.load()
        self.assertTrue(swapped)
        self.assertEqual(loaded[0]["target"], self.rows[0]["target"])
        with self.assertRaisesRegex(ValueError, "reviewed bytes"):
            self.load()

    def test_personal_data_and_existing_tutor_clearance_remain_required(self):
        self.rows[0]["contains_personal_data"] = True
        self.rejected()
        self.rows[0]["contains_personal_data"] = False
        self.rows[0]["consumed_fields"][0]["source_class"] = "FIRST_PARTY_TUTORING"
        self.current["sources"]["source-0"]["context"]["source_class"] = "FIRST_PARTY_TUTORING"
        self.rejected()
        self.manifest["patent_clearance"] = "CLEARED_FOR_TUTOR_MODEL_TRAINING"
        self.write()
        self.assertEqual(len(self.load()[0]), 2)

    def test_draft_writer_never_self_approves(self):
        result = write_draft_bundle(self.rows, self.root / "draft", ["GOAL_MAINTENANCE"])
        self.assertEqual(result["training_status"], "PENDING_REVIEW")
        self.assertFalse(result["training_started"])

    def test_full_and_additional_recipe_checks_reviewed_local_bytes(self):
        self.assertEqual(validate_recipe(self.recipe, self.model), self.model.resolve())
        self.recipe.update(mode="additional", adapter={"method": "lora", "r": 2, "alpha": 4,
                                                        "target_modules": ["q_proj"]})
        self.assertEqual(validate_recipe(self.recipe, self.model), self.model.resolve())
        (self.model / "tokenizer.json").write_text("unreviewed")
        with self.assertRaisesRegex(ValueError, "Every local"):
            validate_recipe(self.recipe, self.model)

    def test_weight_receipt_uses_actual_parameter_bytes_and_rejects_no_change(self):
        class Parameter:
            requires_grad = True
            def __init__(self, value): self.value = value
        class Model:
            def __init__(self): self.weight = Parameter(1.0)
            def named_parameters(self): return [("weight", self.weight)]
        model = Model()
        encode = lambda parameter: struct.pack("d", parameter.value)
        before = fingerprint_trainable(model, encode)
        with self.assertRaisesRegex(ValueError, "No actual"):
            weight_change_receipt(before, fingerprint_trainable(model, encode))
        # Fake numeric update verifies the evidence function, not LLM training.
        model.weight.value -= 0.25
        receipt = weight_change_receipt(before, fingerprint_trainable(model, encode))
        self.assertEqual(receipt["changed_parameter_names"], ["weight"])
        self.assertEqual(receipt["activation_status"], "PENDING_BEHAVIORAL_REVIEW")

    def fake_runner(self, mode="full", change=True, revoke=False, replace_recipe=False):
        """Exercise real runner branches with local fake tensor/model libraries."""
        script = Path(__file__).resolve().parents[1] / "scripts" / "train_dreamkeeper.py"
        spec = importlib.util.spec_from_file_location("test_training_runner", script)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        self.recipe["mode"] = mode
        if mode == "additional":
            self.recipe["adapter"] = {"method": "lora", "r": 2, "alpha": 4, "target_modules": ["q_proj"]}
        recipe_path = self.root / "recipe.json"
        recipe_path.write_text(json.dumps(self.recipe))
        owner = self
        calls = []

        class Parameter:
            requires_grad = True
            value = 1.0
            def requires_grad_(self, value): self.requires_grad = value
            def detach(self): return self
            def cpu(self): return self
            def contiguous(self): return self
            def reshape(self, *args): return self
            def view(self, dtype): return self
            def numpy(self): return self
            def tobytes(self): return struct.pack("d", self.value)

        class Model:
            def __init__(self): self.weight = Parameter()
            def parameters(self): return [self.weight]
            def named_parameters(self): return [("weight", self.weight)]
            @classmethod
            def from_pretrained(cls, path, **kwargs):
                owner.assertEqual(kwargs, {"local_files_only": True, "trust_remote_code": False,
                                           "use_safetensors": True})
                calls.append("model_loaded_offline")
                if replace_recipe:
                    changed_recipe = {**owner.recipe, "learning_rate": 0.02}
                    recipe_path.write_text(json.dumps(changed_recipe))
                return cls()

        class Tokenizer:
            pad_token_id = 0
            eos_token_id = 9
            @classmethod
            def from_pretrained(cls, path, **kwargs):
                owner.assertEqual(kwargs, {"local_files_only": True, "trust_remote_code": False})
                return cls()
            def apply_chat_template(self, messages, **kwargs):
                return [1, 2] if len(messages) == 1 else [1, 2, 3, 9]
            def save_pretrained(self, path):
                (Path(path) / "tokenizer.json").write_text("synthetic-tokenizer")

        class TrainingArguments:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
                owner.assertFalse(kwargs["push_to_hub"])
                owner.assertEqual(kwargs["report_to"], [])

        class Trainer:
            def __init__(self, **kwargs): self.__dict__.update(kwargs)
            def train(self):
                if revoke:
                    owner.current["models"]["synthetic-v1"]["training_allowed"] = False
                    owner.current_path.write_text(json.dumps(owner.current))
                for callback in self.callbacks: callback.on_step_begin()
                for row in self.train_dataset:
                    batch = self.data_collator([row])
                    owner.assertEqual(batch["labels"][0][:2], [-100, -100])
                    owner.assertEqual(batch["labels"][0][2:], [3, 9])
                if change: self.model.weight.value -= 0.25
                calls.append("trained")
            def evaluate(self):
                for row in self.eval_dataset: self.data_collator([row])
                return {"eval_loss": 0.5}
            def save_model(self, path):
                Path(path).mkdir()
                (Path(path) / "model.safetensors").write_bytes(b"synthetic-updated")

        def get_peft_model(model, config):
            calls.append("additional_weights")
            owner.assertEqual(config.r, 2)
            return model

        modules = {"torch": SimpleNamespace(uint8="uint8", tensor=lambda values: values),
                   "transformers": SimpleNamespace(AutoModelForCausalLM=Model, AutoTokenizer=Tokenizer,
                                                    Trainer=Trainer, TrainingArguments=TrainingArguments,
                                                    TrainerCallback=object),
                   "peft": SimpleNamespace(LoraConfig=lambda **kw: SimpleNamespace(**kw),
                                            get_peft_model=get_peft_model)}
        output = self.root / ("out-" + mode)
        argv = ["--dataset", str(self.dataset), "--manifest", str(self.manifest_path), "--current-state",
                str(self.current_path), "--recipe", str(recipe_path), "--model-path", str(self.model),
                "--output-dir", str(output), "--train"]
        with patch.dict(sys.modules, modules), patch.dict("os.environ", {}), redirect_stdout(io.StringIO()):
            runner.main(argv)
        return json.loads((output / "dreamkeeper_training_receipt.json").read_text()), calls

    def test_full_runner_masks_input_and_records_actual_change_with_fake_libraries(self):
        receipt, calls = self.fake_runner()
        self.assertIn("model_loaded_offline", calls)
        self.assertNotIn("additional_weights", calls)
        self.assertTrue(receipt["weight_change_observed"])
        self.assertFalse(receipt["automatically_activated"])
        self.assertEqual(len(receipt["record_consumption"]), 2)

    def test_additional_runner_is_optional_and_does_not_activate(self):
        receipt, calls = self.fake_runner(mode="additional")
        self.assertIn("additional_weights", calls)
        self.assertEqual(receipt["activation_status"], "PENDING_BEHAVIORAL_REVIEW")

    def test_runner_rejects_unchanged_weights_and_revoked_model(self):
        with self.assertRaisesRegex(ValueError, "No actual"):
            self.fake_runner(change=False)
        self.assertFalse((self.root / "out-full" / "dreamkeeper_training_receipt.json").exists())
        with self.assertRaisesRegex(ValueError, "absent or revoked"):
            self.fake_runner(revoke=True)
        self.assertFalse((self.root / "out-full" / "dreamkeeper_training_receipt.json").exists())

    def test_runner_binds_recipe_hash_before_model_loading(self):
        with self.assertRaisesRegex(ValueError, "authorization changed"):
            self.fake_runner(replace_recipe=True)
        self.assertFalse((self.root / "out-full" / "dreamkeeper_training_receipt.json").exists())

    def test_cli_is_check_only_without_any_ml_library(self):
        recipe_path = self.root / "recipe.json"
        recipe_path.write_text(json.dumps(self.recipe))
        script = Path(__file__).resolve().parents[1] / "scripts" / "train_dreamkeeper.py"
        result = subprocess.run([sys.executable, str(script), "--dataset", str(self.dataset),
                                 "--manifest", str(self.manifest_path), "--current-state", str(self.current_path),
                                 "--recipe", str(recipe_path), "--model-path", str(self.model)],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["training_started"])
        self.assertFalse((self.root / "output").exists())


if __name__ == "__main__":
    unittest.main()
