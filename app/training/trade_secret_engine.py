"""Fail-closed trade-secret classification and backup routing.

This module is intentionally policy-first. It does not store secrets itself and
does not contain Drive folder IDs, credentials, prompts, reward weights, or
other owner-only values. Storage adapters can consume the returned plan.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class SecretClass(StrEnum):
    PUBLIC_RELEASED = "PUBLIC_RELEASED"
    INTERNAL_CONFIDENTIAL = "INTERNAL_CONFIDENTIAL"
    TRADE_SECRET = "TRADE_SECRET"
    PERSONAL_RESTRICTED = "PERSONAL_RESTRICTED"
    QUARANTINED = "QUARANTINED"


class AssetKind(StrEnum):
    UPLOAD = "UPLOAD"
    GENERATED = "GENERATED"
    CHAT_TRANSCRIPT = "CHAT_TRANSCRIPT"
    TRAINING_RECORD = "TRAINING_RECORD"
    PATENT_DRAFT = "PATENT_DRAFT"
    PRODUCT_DESIGN = "PRODUCT_DESIGN"
    SOURCE_CODE = "SOURCE_CODE"
    CONFIGURATION = "CONFIGURATION"
    OTHER = "OTHER"


class BackupTarget(StrEnum):
    SERVER_PRIVATE = "SERVER_PRIVATE"
    SERVER_RESTRICTED_PERSONAL = "SERVER_RESTRICTED_PERSONAL"
    SERVER_QUARANTINE = "SERVER_QUARANTINE"
    DRIVE_GENERAL_PRIVATE = "DRIVE_GENERAL_PRIVATE"
    DRIVE_TRADE_SECRET = "DRIVE_TRADE_SECRET"
    DRIVE_RESTRICTED_PERSONAL = "DRIVE_RESTRICTED_PERSONAL"
    DRIVE_QUARANTINE = "DRIVE_QUARANTINE"


class SecretAsset(BaseModel):
    asset_id: str = Field(min_length=1)
    kind: AssetKind
    content_hash: str = Field(min_length=1)

    owner_marked_trade_secret: bool = False
    contains_credentials: bool = False
    contains_personal_data: bool = False
    deidentified: bool = False

    unfiled_patent_material: bool = False
    product_strategy_or_internal_policy: bool = False
    training_policy_secret: bool = False
    chat_contains_secret_context: bool = False

    explicit_public_release: bool = False
    provenance_complete: bool = False
    integrity_verified: bool = False

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class SecretHandlingDecision(BaseModel):
    classification: SecretClass
    reasons: tuple[str, ...]
    backup_targets: tuple[BackupTarget, ...]
    external_provider_delivery_allowed: bool
    raw_content_backup_allowed: bool
    backup_extension: str | None = None


class BackupPolicy(BaseModel):
    interval_minutes: int = Field(default=30, ge=1)
    require_server_copy: bool = True
    require_drive_copy: bool = True
    chat_transcript_extension: str = "txt"


class BackupPlan(BaseModel):
    asset_id: str
    due: bool
    classification: SecretClass
    targets: tuple[BackupTarget, ...]
    raw_content_backup_allowed: bool
    backup_extension: str | None
    next_due_at: datetime
    reasons: tuple[str, ...]


_TRADE_SECRET_KINDS = {
    AssetKind.PATENT_DRAFT,
    AssetKind.PRODUCT_DESIGN,
    AssetKind.SOURCE_CODE,
}


def classify_secret_asset(asset: SecretAsset) -> SecretHandlingDecision:
    """Classify and route one asset with fail-closed defaults."""

    if asset.contains_credentials:
        return SecretHandlingDecision(
            classification=SecretClass.QUARANTINED,
            reasons=(
                "Credential-bearing content is never placed in ordinary "
                "content backup paths; store only protected metadata/pointers.",
            ),
            backup_targets=(
                BackupTarget.SERVER_QUARANTINE,
                BackupTarget.DRIVE_QUARANTINE,
            ),
            external_provider_delivery_allowed=False,
            raw_content_backup_allowed=False,
            backup_extension=_extension_for(asset),
        )

    if not asset.provenance_complete or not asset.integrity_verified:
        return SecretHandlingDecision(
            classification=SecretClass.QUARANTINED,
            reasons=(
                "Provenance or integrity verification is incomplete.",
            ),
            backup_targets=(
                BackupTarget.SERVER_QUARANTINE,
                BackupTarget.DRIVE_QUARANTINE,
            ),
            external_provider_delivery_allowed=False,
            raw_content_backup_allowed=True,
            backup_extension=_extension_for(asset),
        )

    if asset.contains_personal_data and not asset.deidentified:
        return SecretHandlingDecision(
            classification=SecretClass.PERSONAL_RESTRICTED,
            reasons=(
                "Personal data is present and has not been deidentified.",
            ),
            backup_targets=(
                BackupTarget.SERVER_RESTRICTED_PERSONAL,
                BackupTarget.DRIVE_RESTRICTED_PERSONAL,
            ),
            external_provider_delivery_allowed=False,
            raw_content_backup_allowed=True,
            backup_extension=_extension_for(asset),
        )

    is_trade_secret = any(
        (
            asset.owner_marked_trade_secret,
            asset.unfiled_patent_material,
            asset.product_strategy_or_internal_policy,
            asset.training_policy_secret,
            asset.chat_contains_secret_context,
            asset.kind in _TRADE_SECRET_KINDS,
        )
    )

    if is_trade_secret:
        return SecretHandlingDecision(
            classification=SecretClass.TRADE_SECRET,
            reasons=(
                "Owner-only product, patent, source-code, training-policy, "
                "or secret-context material requires trade-secret handling.",
            ),
            backup_targets=(
                BackupTarget.SERVER_PRIVATE,
                BackupTarget.DRIVE_TRADE_SECRET,
            ),
            external_provider_delivery_allowed=False,
            raw_content_backup_allowed=True,
            backup_extension=_extension_for(asset),
        )

    if asset.explicit_public_release:
        return SecretHandlingDecision(
            classification=SecretClass.PUBLIC_RELEASED,
            reasons=("Owner explicitly marked the material for public release.",),
            backup_targets=(
                BackupTarget.SERVER_PRIVATE,
                BackupTarget.DRIVE_GENERAL_PRIVATE,
            ),
            external_provider_delivery_allowed=True,
            raw_content_backup_allowed=True,
            backup_extension=_extension_for(asset),
        )

    return SecretHandlingDecision(
        classification=SecretClass.INTERNAL_CONFIDENTIAL,
        reasons=(
            "No explicit public-release authorization exists; default to "
            "internal confidential handling.",
        ),
        backup_targets=(
            BackupTarget.SERVER_PRIVATE,
            BackupTarget.DRIVE_GENERAL_PRIVATE,
        ),
        external_provider_delivery_allowed=False,
        raw_content_backup_allowed=True,
        backup_extension=_extension_for(asset),
    )


def build_backup_plan(
    *,
    asset: SecretAsset,
    decision: SecretHandlingDecision,
    last_completed_at: datetime | None,
    now: datetime | None = None,
    policy: BackupPolicy | None = None,
) -> BackupPlan:
    """Build a deterministic 30-minute backup plan.

    This is the engine-side schedule decision. Actual server/Drive writes are
    performed by storage adapters so secrets never need to be embedded here.
    """

    selected_policy = policy or BackupPolicy()
    current = now or datetime.now(timezone.utc)

    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if last_completed_at is not None and last_completed_at.tzinfo is None:
        raise ValueError("last_completed_at must be timezone-aware")

    if last_completed_at is None:
        next_due = current
        due = True
    else:
        next_due = last_completed_at + timedelta(
            minutes=selected_policy.interval_minutes
        )
        due = current >= next_due

    targets = list(decision.backup_targets)

    if selected_policy.require_server_copy:
        if not any(target.value.startswith("SERVER_") for target in targets):
            raise ValueError("Backup decision lacks a required server target.")

    if selected_policy.require_drive_copy:
        if not any(target.value.startswith("DRIVE_") for target in targets):
            raise ValueError("Backup decision lacks a required Drive target.")

    return BackupPlan(
        asset_id=asset.asset_id,
        due=due,
        classification=decision.classification,
        targets=tuple(targets),
        raw_content_backup_allowed=decision.raw_content_backup_allowed,
        backup_extension=decision.backup_extension,
        next_due_at=next_due,
        reasons=decision.reasons,
    )


def assert_external_delivery_allowed(
    decision: SecretHandlingDecision,
) -> None:
    """Fail closed before sending material to an external AI/provider."""

    if not decision.external_provider_delivery_allowed:
        raise ValueError(
            f"{decision.classification.value} material is not authorized for "
            "external-provider delivery."
        )


def _extension_for(asset: SecretAsset) -> str | None:
    if asset.kind is AssetKind.CHAT_TRANSCRIPT:
        return "txt"
    return None

