"""Immutable dataset snapshot construction.

Only records that already passed the eligibility gate may enter a snapshot.
The snapshot hash binds the exact set of training records and their purposes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable

from pydantic import BaseModel, Field

from app.training.models import (
    EligibilityDecision,
    TrainingDataStatus,
    TrainingPurpose,
    TrajectoryEnvelope,
)


class SnapshotRecord(BaseModel):
    trajectory_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    purpose_code: str = Field(min_length=1)
    allowed_purposes: tuple[TrainingPurpose, ...]


class DatasetSnapshot(BaseModel):
    snapshot_hash: str = Field(min_length=64, max_length=64)
    record_count: int = Field(ge=1)
    records: tuple[SnapshotRecord, ...]


def _canonical_payload(records: tuple[SnapshotRecord, ...]) -> bytes:
    payload = [
        {
            "trajectory_id": record.trajectory_id,
            "content_hash": record.content_hash,
            "source_revision": record.source_revision,
            "purpose_code": record.purpose_code,
            "allowed_purposes": sorted(
                purpose.value for purpose in record.allowed_purposes
            ),
        }
        for record in records
    ]
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def build_dataset_snapshot(
    evaluated_items: Iterable[
        tuple[TrajectoryEnvelope, EligibilityDecision]
    ],
) -> DatasetSnapshot:
    """Create an immutable, deterministic training snapshot.

    Any ineligible item causes a fail-closed error instead of being silently
    skipped, so the caller must explicitly resolve or exclude quarantined data.
    """

    records: list[SnapshotRecord] = []

    for item, decision in evaluated_items:
        if decision.status is not TrainingDataStatus.ELIGIBLE:
            raise ValueError(
                f"Trajectory {item.trajectory_id} is not ELIGIBLE "
                f"({decision.status.value})."
            )

        if not decision.allowed_purposes:
            raise ValueError(
                f"Trajectory {item.trajectory_id} has no allowed purpose."
            )

        records.append(
            SnapshotRecord(
                trajectory_id=item.trajectory_id,
                content_hash=item.content_hash,
                source_revision=item.source_revision,
                purpose_code=item.purpose_code,
                allowed_purposes=decision.allowed_purposes,
            )
        )

    if not records:
        raise ValueError("A dataset snapshot requires at least one record.")

    ordered = tuple(
        sorted(
            records,
            key=lambda row: (
                row.trajectory_id,
                row.source_revision,
                row.content_hash,
            ),
        )
    )

    digest = hashlib.sha256(_canonical_payload(ordered)).hexdigest()

    return DatasetSnapshot(
        snapshot_hash=digest,
        record_count=len(ordered),
        records=ordered,
    )
