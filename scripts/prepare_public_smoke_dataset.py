"""Prepare a small, license-pinned public smoke-test dataset.

This is NOT EduRecover proprietary tutor training data. It exists only to
verify that the private gpt-oss training pipeline can load, hash, train, save,
and produce a receipt before first-party tutoring data is admitted.

Source:
  HuggingFaceH4/Multilingual-Thinking
Pinned revision:
  d57770a279da10d30be4845451a499aef8caf5b9
License:
  Apache-2.0

The script intentionally drops assistant 'thinking'/analysis content and keeps
only developer/user/final-visible assistant text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


DATASET_ID = "HuggingFaceH4/Multilingual-Thinking"
DATASET_REVISION = "d57770a279da10d30be4845451a499aef8caf5b9"
DATASET_LICENSE = "Apache-2.0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=64)
    parser.add_argument("--seed", type=int, default=20260926)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.count < 1 or args.count > 1000:
        raise ValueError("--count must be between 1 and 1000.")

    from datasets import load_dataset

    source = load_dataset(
        DATASET_ID,
        split="train",
        revision=DATASET_REVISION,
    )
    source = source.shuffle(seed=args.seed).select(
        range(min(args.count, len(source)))
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = args.output_dir / "smoke_public_v0_1.jsonl"

    count = 0
    with dataset_path.open("w", encoding="utf-8") as handle:
        for row in source:
            developer = str(row.get("developer") or "").strip()
            user = str(row.get("user") or "").strip()
            final = str(row.get("final") or "").strip()
            if not user or not final:
                continue

            messages = []
            if developer:
                messages.append(
                    {"role": "system", "content": developer}
                )
            messages.append({"role": "user", "content": user})
            messages.append({"role": "assistant", "content": final})

            handle.write(
                json.dumps(
                    {"messages": messages},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            count += 1

    if count < 1:
        raise RuntimeError("No usable records were produced.")

    file_hash = sha256_file(dataset_path)
    snapshot_hash = hashlib.sha256(
        (
            DATASET_ID
            + "\n"
            + DATASET_REVISION
            + "\n"
            + file_hash
            + "\n"
            + str(count)
        ).encode("utf-8")
    ).hexdigest()

    manifest = {
        "snapshot_version": "PUBLIC_SMOKE_V0_1",
        "snapshot_hash": snapshot_hash,
        "dataset_file": dataset_path.name,
        "dataset_file_sha256": file_hash,
        "record_count": count,
        "training_status": "ELIGIBLE",
        "source_classes": ["LICENSED_CORPUS"],
        "source_dataset": DATASET_ID,
        "source_revision": DATASET_REVISION,
        "source_license": DATASET_LICENSE,
        "contains_personal_data": False,
        "deidentified": True,
        "provenance_complete": True,
        "assistant_hidden_reasoning_included": False,
        "purpose": "PIPELINE_SMOKE_TEST_ONLY"
    }
    (args.output_dir / "smoke_public_v0_1.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
