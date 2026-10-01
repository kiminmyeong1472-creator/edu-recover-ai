from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import uuid
from typing import Literal


SourceState = Literal[
    "CURRENT",
    "CORRECTED",
    "SUPERSEDED",
    "WITHDRAWN",
    "LATEST_UNCONFIRMED",
    "ACCESS_UNAVAILABLE",
]
ActionState = Literal[
    "INCOMPLETE",
    "CURRENT",
    "HOLD",
    "STALE",
    "REVALIDATION_REQUIRED",
    "CLOSED_HISTORICAL",
]
VerificationState = Literal["VERIFIED", "UNVERIFIED", "UNRESOLVED"]
RiskTier = Literal["LOW", "MEDIUM", "HIGH"]
DependencyState = Literal["RESOLVED", "UNRESOLVED"]


@dataclass(frozen=True)
class SourceRevision:
    source_revision_id: str
    source_id: str
    predecessor_revision_id: str | None
    successor_revision_id: str | None
    recorded_time: str
    valid_time: str | None
    verification_state: VerificationState
    source_state: SourceState
    content_hash: str
    changed_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClinicalAction:
    clinical_action_id: str
    patient_id: str
    action_type: str
    responsible_role: str
    action_state: ActionState
    created_at: str
    due_at: str | None = None
    completed_at: str | None = None
    current_validity: str = "CURRENT"
    downstream_action_refs: tuple[str, ...] = ()
    risk_tier: RiskTier = "MEDIUM"


@dataclass(frozen=True)
class ActualUseEvidence:
    evidence_id: str
    clinical_action_id: str
    source_revision_id: str
    manifest_type: str
    actor_or_system: str
    generated_at: str
    input_set_hash: str
    context_hash: str
    verification_state: VerificationState


@dataclass(frozen=True)
class RevalidationReceipt:
    receipt_id: str
    change_event_id: str
    source_before: str
    source_after: str
    affected_action_ids: tuple[str, ...]
    unaffected_action_ids: tuple[str, ...]
    pre_states: dict[str, str]
    post_states: dict[str, str]
    unresolved_scope: tuple[str, ...]
    reason_code: str
    generated_at: str


@dataclass(frozen=True)
class RevalidationResult:
    actions: tuple[ClinicalAction, ...]
    receipt: RevalidationReceipt


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def make_source_revision(
    *,
    source_id: str,
    payload: dict,
    source_state: SourceState = "CURRENT",
    verification_state: VerificationState = "UNVERIFIED",
    predecessor_revision_id: str | None = None,
    valid_time: str | None = None,
    changed_fields: list[str] | None = None,
) -> SourceRevision:
    # Safety invariant: uncertainty never becomes VERIFIED merely by ingestion.
    if source_state in {"LATEST_UNCONFIRMED", "ACCESS_UNAVAILABLE"} and verification_state == "VERIFIED":
        raise ValueError("uncertain_source_cannot_be_auto_verified")
    return SourceRevision(
        source_revision_id=f"src_{uuid.uuid4().hex}",
        source_id=source_id,
        predecessor_revision_id=predecessor_revision_id,
        successor_revision_id=None,
        recorded_time=utcnow(),
        valid_time=valid_time,
        verification_state=verification_state,
        source_state=source_state,
        content_hash=stable_hash(payload),
        changed_fields=tuple(changed_fields or ()),
    )


def make_actual_use_evidence(
    *,
    clinical_action_id: str,
    source_revision_id: str,
    manifest_type: str,
    actor_or_system: str,
    input_set: object,
    context: object,
    verification_state: VerificationState = "UNVERIFIED",
) -> ActualUseEvidence:
    return ActualUseEvidence(
        evidence_id=f"use_{uuid.uuid4().hex}",
        clinical_action_id=clinical_action_id,
        source_revision_id=source_revision_id,
        manifest_type=manifest_type,
        actor_or_system=actor_or_system,
        generated_at=utcnow(),
        input_set_hash=stable_hash(input_set),
        context_hash=stable_hash(context),
        verification_state=verification_state,
    )


def selective_revalidation(
    *,
    source_before: SourceRevision,
    source_after: SourceRevision,
    actions: list[ClinicalAction],
    actual_use_evidence: list[ActualUseEvidence],
    dependency_state: DependencyState = "RESOLVED",
    reason_code: str = "SOURCE_REVISION_CHANGED",
) -> RevalidationResult:
    """Revalidate only live actions that actually used the changed source revision.

    This engine does not infer a diagnosis, prescribe, change an order, or decide
    whether a procedure is clinically appropriate. It only changes workflow
    safety state from explicit lineage + caller supplied dependency/risk state.
    """
    linked_action_ids = {
        evidence.clinical_action_id
        for evidence in actual_use_evidence
        if evidence.source_revision_id == source_before.source_revision_id
    }
    live_states = {"CURRENT", "INCOMPLETE"}
    pre_states = {action.clinical_action_id: action.action_state for action in actions}
    updated: list[ClinicalAction] = []
    affected: list[str] = []
    unaffected: list[str] = []
    unresolved: list[str] = []

    source_uncertain = (
        source_after.source_state in {"LATEST_UNCONFIRMED", "ACCESS_UNAVAILABLE"}
        or source_after.verification_state != "VERIFIED"
    )

    for action in actions:
        action_id = action.clinical_action_id
        is_actual_user = action_id in linked_action_ids
        is_live = action.action_state in live_states

        if not (is_actual_user and is_live):
            updated.append(action)
            unaffected.append(action_id)
            continue

        affected.append(action_id)
        dependency_unresolved = dependency_state == "UNRESOLVED" or source_uncertain
        if dependency_unresolved:
            unresolved.append(action_id)

        # High-risk unresolved work is held; otherwise it is routed for revalidation.
        next_state: ActionState
        if dependency_unresolved and action.risk_tier == "HIGH":
            next_state = "HOLD"
        else:
            next_state = "REVALIDATION_REQUIRED"

        updated.append(replace(action, action_state=next_state))

    post_states = {action.clinical_action_id: action.action_state for action in updated}
    receipt = RevalidationReceipt(
        receipt_id=f"rcpt_{uuid.uuid4().hex}",
        change_event_id=f"chg_{uuid.uuid4().hex}",
        source_before=source_before.source_revision_id,
        source_after=source_after.source_revision_id,
        affected_action_ids=tuple(affected),
        unaffected_action_ids=tuple(unaffected),
        pre_states=pre_states,
        post_states=post_states,
        unresolved_scope=tuple(unresolved),
        reason_code=reason_code,
        generated_at=utcnow(),
    )
    return RevalidationResult(actions=tuple(updated), receipt=receipt)


def to_payload(value: object) -> dict:
    return asdict(value)
