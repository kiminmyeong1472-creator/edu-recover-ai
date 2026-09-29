"""Seal owner-reviewed RL records without manufacturing a review or examples."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from training.rl.contract import SCHEMA, digest, file_digest, load_bundle, load_object, require


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("records", "review", "revocations", "output-dir"):
        p.add_argument("--" + key, type=Path, required=True)
    a = p.parse_args()
    review = load_object(a.review)
    require(review.get("training_status") == "ELIGIBLE", "Reviewer must explicitly approve records before sealing")
    require(review.get("reviewed_dataset_sha256") == file_digest(a.records), "Review does not bind this exact dataset")
    require(not a.output_dir.exists(), "Use a fresh bundle directory")
    os.umask(0o077)
    a.output_dir.mkdir(parents=True, mode=0o700)
    try:
        dataset = a.output_dir / "records.jsonl"
        shutil.copyfile(a.records, dataset)
        manifest = {k: v for k, v in review.items() if k != "snapshot_hash"}
        manifest.update({"schema": SCHEMA, "dataset_file_sha256": file_digest(dataset),
                         "record_count": sum(bool(line.strip()) for line in dataset.read_text(encoding="utf-8").splitlines())})
        manifest["snapshot_hash"] = digest(manifest)
        path = a.output_dir / "manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        shutil.copyfile(a.revocations, a.output_dir / "revocations.json")
        rows, _ = load_bundle(dataset, path, a.output_dir / "revocations.json")
        print(json.dumps({"sealed": True, "record_count": len(rows), "snapshot_hash": manifest["snapshot_hash"], "actual_training_started": False}))
    except BaseException:
        shutil.rmtree(a.output_dir)
        raise

if __name__ == "__main__":
    main()
