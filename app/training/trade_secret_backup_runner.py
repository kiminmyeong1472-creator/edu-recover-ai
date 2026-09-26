"""Execution core for trade-secret backup plans.

Storage-specific adapters are injected at runtime. This keeps Drive folder IDs,
credentials, and provider-specific code outside the policy engine while still
making backup execution and receipts testable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Protocol

from pydantic import BaseModel, Field

from app.training.trade_secret_engine import (
    BackupPlan,
    BackupPolicy,
    BackupTarget,
    SecretAsset,
    SecretHandlingDecision,
    build_backup_plan,
)


class StorageWriteReceipt(BaseModel):
    asset_id: str = Field(min_length=1)
    target: BackupTarget
    content_hash: str = Field(min_length=1)
    storage_ref: str = Field(min_length=1)
    raw_content_written: bool
    completed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class BackupCycleReceipt(BaseModel):
    asset_id: str = Field(min_length=1)
    due: bool
    complete: bool
    plan: BackupPlan
    writes: tuple[StorageWriteReceipt, ...] = ()
    completed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class BackupAdapter(Protocol):
    def write(
        self,
        *,
        asset: SecretAsset,
        decision: SecretHandlingDecision,
        plan: BackupPlan,
        target: BackupTarget,
        source_ref: str | None,
    ) -> StorageWriteReceipt:
        """Write raw content or metadata-only backup and return a receipt."""


def execute_backup_cycle(
    *,
    asset: SecretAsset,
    decision: SecretHandlingDecision,
    adapters: Mapping[BackupTarget, BackupAdapter],
    source_ref: str | None,
    last_completed_at: datetime | None,
    now: datetime | None = None,
    policy: BackupPolicy | None = None,
) -> BackupCycleReceipt:
    """Execute all required backup targets for one asset.

    Missing adapters, mismatched receipts, or incomplete writes fail closed.
    Credential-bearing material receives source_ref=None so adapters can only
    write protected metadata/pointers.
    """

    plan = build_backup_plan(
        asset=asset,
        decision=decision,
        last_completed_at=last_completed_at,
        now=now,
        policy=policy,
    )

    if not plan.due:
        return BackupCycleReceipt(
            asset_id=asset.asset_id,
            due=False,
            complete=True,
            plan=plan,
            writes=(),
        )

    writes: list[StorageWriteReceipt] = []
    effective_source_ref = (
        source_ref if plan.raw_content_backup_allowed else None
    )

    for target in plan.targets:
        adapter = adapters.get(target)
        if adapter is None:
            raise ValueError(
                f"Missing backup adapter for required target {target.value}."
            )

        receipt = adapter.write(
            asset=asset,
            decision=decision,
            plan=plan,
            target=target,
            source_ref=effective_source_ref,
        )

        if receipt.asset_id != asset.asset_id:
            raise ValueError("Backup receipt asset_id mismatch.")
        if receipt.target is not target:
            raise ValueError("Backup receipt target mismatch.")
        if receipt.content_hash != asset.content_hash:
            raise ValueError("Backup receipt content_hash mismatch.")
        if (
            not plan.raw_content_backup_allowed
            and receipt.raw_content_written
        ):
            raise ValueError(
                "Raw content was written despite metadata-only policy."
            )

        writes.append(receipt)

    return BackupCycleReceipt(
        asset_id=asset.asset_id,
        due=True,
        complete=len(writes) == len(plan.targets),
        plan=plan,
        writes=tuple(writes),
    )
