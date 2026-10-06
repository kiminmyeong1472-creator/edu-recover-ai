"""Google Cloud short-lease GPU policy and server-persistence gate.

This module does not provision a GPU by itself. It defines the production gate:
Google Cloud is the only approved GPU lease provider for EduRecover training,
leases should be short-lived, and an ephemeral GPU disk is never authoritative.
Checkpoint/receipt metadata must be persisted to the EduRecover server volume
through the normal training workflow before a run can be promoted.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.secret_vault import state_root
from app.training.backup_service import atomic_json


_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def gpu_state_root() -> Path:
    path = state_root() / "google_gpu_runs"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise RuntimeError("GPU_STATE_SYMLINK_FORBIDDEN")
    path.chmod(0o700)
    return path


def policy_status() -> dict[str, Any]:
    sync_seconds = int(os.getenv("EDURECOVER_GPU_SERVER_SYNC_SECONDS", "30"))
    return {
        "approved_provider": "google_cloud",
        "lease_strategy": "short_term_preemptible_or_on_demand",
        "aggressive_release_when_idle": True,
        "server_persistence_required": True,
        "ephemeral_gpu_disk_authoritative": False,
        "checkpoint_metadata_sync_seconds": max(10, min(sync_seconds, 300)),
        "google_cloud_project_configured": bool(settings.google_cloud_project),
        "google_credentials_configured": settings.google_credentials_json is not None,
        "promotion_blocked_without_server_receipt": True,
    }


def persist_checkpoint_receipt(
    *,
    run_id: str,
    sequence: int,
    checkpoint_sha256: str,
    receipt_sha256: str,
    remote_artifact_ref: str = "",
) -> dict[str, Any]:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("INVALID_GPU_RUN_ID")
    if sequence < 0:
        raise ValueError("INVALID_GPU_SEQUENCE")
    if not _SHA256.fullmatch(checkpoint_sha256) or not _SHA256.fullmatch(receipt_sha256):
        raise ValueError("INVALID_GPU_CHECKPOINT_HASH")
    if len(remote_artifact_ref) > 500:
        raise ValueError("GPU_ARTIFACT_REFERENCE_TOO_LONG")

    run_dir = gpu_state_root() / run_id
    run_dir.mkdir(mode=0o700, exist_ok=True)
    if run_dir.is_symlink():
        raise RuntimeError("GPU_RUN_SYMLINK_FORBIDDEN")

    latest_path = run_dir / "latest.json"
    previous = None
    if latest_path.exists() and not latest_path.is_symlink():
        try:
            previous = json.loads(latest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
    if isinstance(previous, dict) and int(previous.get("sequence", -1)) >= sequence:
        raise ValueError("GPU_CHECKPOINT_SEQUENCE_NOT_MONOTONIC")

    receipt = {
        "run_id": run_id,
        "sequence": sequence,
        "checkpoint_sha256": checkpoint_sha256,
        "receipt_sha256": receipt_sha256,
        "remote_artifact_ref": remote_artifact_ref,
        "server_persisted_at": datetime.now(timezone.utc).isoformat(),
        "authoritative_state": "EDURECOVER_SERVER_VOLUME",
    }
    atomic_json(run_dir / f"{sequence:08d}.json", receipt)
    atomic_json(latest_path, receipt)
    return receipt
