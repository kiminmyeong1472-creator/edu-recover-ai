from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.domains.medical.hospital_os import (
    ActualUseEvidence,
    ClinicalAction,
    SourceRevision,
    make_actual_use_evidence,
    make_source_revision,
    selective_revalidation,
    to_payload,
    utcnow,
)
from app.platform_core.life_auth import require_life_user
from app.platform_core.persistent_domain_store import get_resource, list_resources, put_resource


router = APIRouter(prefix="/life/api/medical/hospital-os", tags=["medical-recover-hospital-os"])

_SOURCE_KIND = "hospital_os_source_revision"
_ACTION_KIND = "hospital_os_clinical_action"
_USE_KIND = "hospital_os_actual_use"
_RECEIPT_KIND = "hospital_os_revalidation_receipt"


class SourceRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=160)
    payload: dict
    source_state: Literal[
        "CURRENT", "CORRECTED", "SUPERSEDED", "WITHDRAWN",
        "LATEST_UNCONFIRMED", "ACCESS_UNAVAILABLE"
    ] = "CURRENT"
    verification_state: Literal["VERIFIED", "UNVERIFIED", "UNRESOLVED"] = "UNVERIFIED"
    predecessor_revision_id: str | None = Field(default=None, max_length=160)
    valid_time: str | None = Field(default=None, max_length=80)
    changed_fields: list[str] = Field(default_factory=list, max_length=100)


class ClinicalActionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str = Field(min_length=1, max_length=160)
    action_type: str = Field(min_length=1, max_length=120)
    responsible_role: str = Field(min_length=1, max_length=80)
    action_state: Literal[
        "INCOMPLETE", "CURRENT", "HOLD", "STALE",
        "REVALIDATION_REQUIRED", "CLOSED_HISTORICAL"
    ] = "INCOMPLETE"
    due_at: str | None = Field(default=None, max_length=80)
    current_validity: str = Field(default="CURRENT", min_length=1, max_length=40)
    downstream_action_refs: list[str] = Field(default_factory=list, max_length=100)
    risk_tier: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"


class ActualUseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clinical_action_id: str = Field(min_length=1, max_length=160)
    source_revision_id: str = Field(min_length=1, max_length=160)
    manifest_type: str = Field(min_length=1, max_length=120)
    actor_or_system: str = Field(min_length=1, max_length=160)
    input_set: dict = Field(default_factory=dict)
    context: dict = Field(default_factory=dict)
    verification_state: Literal["VERIFIED", "UNVERIFIED", "UNRESOLVED"] = "UNVERIFIED"


class RevalidationRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_before_revision_id: str = Field(min_length=1, max_length=160)
    source_after_revision_id: str = Field(min_length=1, max_length=160)
    dependency_state: Literal["RESOLVED", "UNRESOLVED"] = "RESOLVED"
    reason_code: str = Field(default="SOURCE_REVISION_CHANGED", min_length=1, max_length=120)


def _source_from_payload(payload: dict) -> SourceRevision:
    return SourceRevision(
        source_revision_id=payload["source_revision_id"],
        source_id=payload["source_id"],
        predecessor_revision_id=payload.get("predecessor_revision_id"),
        successor_revision_id=payload.get("successor_revision_id"),
        recorded_time=payload["recorded_time"],
        valid_time=payload.get("valid_time"),
        verification_state=payload["verification_state"],
        source_state=payload["source_state"],
        content_hash=payload["content_hash"],
        changed_fields=tuple(payload.get("changed_fields", [])),
    )


def _action_from_payload(payload: dict) -> ClinicalAction:
    return ClinicalAction(
        clinical_action_id=payload["clinical_action_id"],
        patient_id=payload["patient_id"],
        action_type=payload["action_type"],
        responsible_role=payload["responsible_role"],
        action_state=payload["action_state"],
        created_at=payload["created_at"],
        due_at=payload.get("due_at"),
        completed_at=payload.get("completed_at"),
        current_validity=payload.get("current_validity", "CURRENT"),
        downstream_action_refs=tuple(payload.get("downstream_action_refs", [])),
        risk_tier=payload.get("risk_tier", "MEDIUM"),
    )


def _use_from_payload(payload: dict) -> ActualUseEvidence:
    return ActualUseEvidence(
        evidence_id=payload["evidence_id"],
        clinical_action_id=payload["clinical_action_id"],
        source_revision_id=payload["source_revision_id"],
        manifest_type=payload["manifest_type"],
        actor_or_system=payload["actor_or_system"],
        generated_at=payload["generated_at"],
        input_set_hash=payload["input_set_hash"],
        context_hash=payload["context_hash"],
        verification_state=payload["verification_state"],
    )


def _owned_resource(account_id: str, resource_id: str, kind: str) -> dict:
    item = get_resource(
        domain="medical",
        subject_id=account_id,
        resource_id=resource_id,
    )
    if not item or item["kind"] != kind:
        raise HTTPException(status_code=404, detail="resource_not_found")
    return item


@router.get("/status")
def hospital_os_status(request: Request) -> dict:
    account_id = require_life_user(request)
    sources = list_resources(domain="medical", subject_id=account_id, kind=_SOURCE_KIND)
    actions = list_resources(domain="medical", subject_id=account_id, kind=_ACTION_KIND)
    receipts = list_resources(domain="medical", subject_id=account_id, kind=_RECEIPT_KIND)
    return {
        "product": "Medical Recover Hospital OS",
        "sources": len(sources),
        "actions": len(actions),
        "current_holds": sum(1 for x in actions if x["payload"].get("action_state") == "HOLD"),
        "revalidation_required": sum(
            1 for x in actions if x["payload"].get("action_state") == "REVALIDATION_REQUIRED"
        ),
        "receipts": len(receipts),
        "safety_rules": {
            "unknown_auto_verified": False,
            "historical_actions_preserved": True,
            "selective_revalidation": True,
            "automatic_prescribing": False,
        },
    }


@router.post("/source-revisions")
def create_source_revision(payload: SourceRevisionCreate, request: Request) -> dict:
    account_id = require_life_user(request)
    try:
        revision = make_source_revision(
            source_id=payload.source_id,
            payload=payload.payload,
            source_state=payload.source_state,
            verification_state=payload.verification_state,
            predecessor_revision_id=payload.predecessor_revision_id,
            valid_time=payload.valid_time,
            changed_fields=payload.changed_fields,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_SOURCE_KIND,
        payload=to_payload(revision),
        resource_id=revision.source_revision_id,
    )
    return {"source_revision_id": revision.source_revision_id, "status": revision.source_state}


@router.post("/clinical-actions")
def create_clinical_action(payload: ClinicalActionCreate, request: Request) -> dict:
    account_id = require_life_user(request)
    action_id = "act_" + __import__("uuid").uuid4().hex
    action = ClinicalAction(
        clinical_action_id=action_id,
        patient_id=payload.patient_id,
        action_type=payload.action_type,
        responsible_role=payload.responsible_role,
        action_state=payload.action_state,
        created_at=utcnow(),
        due_at=payload.due_at,
        current_validity=payload.current_validity,
        downstream_action_refs=tuple(payload.downstream_action_refs),
        risk_tier=payload.risk_tier,
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_ACTION_KIND,
        payload=to_payload(action),
        resource_id=action_id,
    )
    return {"clinical_action_id": action_id, "action_state": action.action_state}


@router.post("/actual-use-evidence")
def create_actual_use(payload: ActualUseCreate, request: Request) -> dict:
    account_id = require_life_user(request)
    _owned_resource(account_id, payload.clinical_action_id, _ACTION_KIND)
    _owned_resource(account_id, payload.source_revision_id, _SOURCE_KIND)

    evidence = make_actual_use_evidence(
        clinical_action_id=payload.clinical_action_id,
        source_revision_id=payload.source_revision_id,
        manifest_type=payload.manifest_type,
        actor_or_system=payload.actor_or_system,
        input_set=payload.input_set,
        context=payload.context,
        verification_state=payload.verification_state,
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_USE_KIND,
        payload=to_payload(evidence),
        resource_id=evidence.evidence_id,
    )
    return {"evidence_id": evidence.evidence_id, "status": "recorded"}


@router.post("/revalidation")
def run_revalidation(payload: RevalidationRun, request: Request) -> dict:
    account_id = require_life_user(request)
    before_item = _owned_resource(account_id, payload.source_before_revision_id, _SOURCE_KIND)
    after_item = _owned_resource(account_id, payload.source_after_revision_id, _SOURCE_KIND)
    before = _source_from_payload(before_item["payload"])
    after = _source_from_payload(after_item["payload"])

    actions = [
        _action_from_payload(item["payload"])
        for item in list_resources(domain="medical", subject_id=account_id, kind=_ACTION_KIND)
    ]
    uses = [
        _use_from_payload(item["payload"])
        for item in list_resources(domain="medical", subject_id=account_id, kind=_USE_KIND)
    ]
    result = selective_revalidation(
        source_before=before,
        source_after=after,
        actions=actions,
        actual_use_evidence=uses,
        dependency_state=payload.dependency_state,
        reason_code=payload.reason_code,
    )

    for action in result.actions:
        put_resource(
            domain="medical",
            subject_id=account_id,
            kind=_ACTION_KIND,
            payload=to_payload(action),
            resource_id=action.clinical_action_id,
        )
    receipt = result.receipt
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_RECEIPT_KIND,
        payload=to_payload(receipt),
        resource_id=receipt.receipt_id,
    )
    return {
        "receipt_id": receipt.receipt_id,
        "affected_action_ids": list(receipt.affected_action_ids),
        "unaffected_action_ids": list(receipt.unaffected_action_ids),
        "unresolved_scope": list(receipt.unresolved_scope),
        "post_states": receipt.post_states,
    }


@router.get("/clinical-actions")
def list_clinical_actions(request: Request) -> dict:
    account_id = require_life_user(request)
    return {
        "actions": [
            item["payload"]
            for item in list_resources(domain="medical", subject_id=account_id, kind=_ACTION_KIND)
        ]
    }
