from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.domains.medical.hospital_os import (
    ActualUseEvidence,
    ClinicalAction,
    MedicationExecutionObject,
    SourceRevision,
    apply_action_completion,
    evaluate_prescription_change,
    make_actual_use_evidence,
    make_er_workflow_item,
    assess_claim_readiness,
    make_handoff_snapshot,
    make_hospital_audit_event,
    make_hospital_event,
    make_hospital_operation_record,
    make_medication_execution_object,
    make_preop_exposure,
    make_report_snapshot,
    make_source_revision,
    selective_revalidation,
    to_payload,
    utcnow,
    verify_patient_identity,
)
from app.platform_core.life_auth import require_life_user
from app.platform_core.persistent_domain_store import get_resource, list_resources, put_resource


router = APIRouter(prefix="/life/api/medical/hospital-os", tags=["medical-recover-hospital-os"])

_SOURCE_KIND = "hospital_os_source_revision"
_ACTION_KIND = "hospital_os_clinical_action"
_USE_KIND = "hospital_os_actual_use"
_RECEIPT_KIND = "hospital_os_revalidation_receipt"
_IDENTITY_KIND = "hospital_os_identity_check"
_MEDICATION_KIND = "hospital_os_medication_object"
_AUDIT_KIND = "hospital_os_audit_event"
_HANDOFF_KIND = "hospital_os_handoff_snapshot"
_REPORT_KIND = "hospital_os_report_snapshot"
_ER_WORKFLOW_KIND = "hospital_os_er_workflow"
_PREOP_KIND = "hospital_os_preop_exposure"
_CLAIM_KIND = "hospital_os_claim_readiness"
_OPERATION_KIND = "hospital_os_operation_record"
_EVENT_KIND = "hospital_os_event"


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


class IdentityCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str = Field(min_length=1, max_length=160)
    expected: dict[str, str | None]
    observed: dict[str, str | None]
    required_fields: list[str] = Field(default_factory=lambda: ["name", "second_identifier"], min_length=1, max_length=10)


class MedicationObjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str = Field(min_length=1, max_length=160)
    clinical_action_id: str = Field(min_length=1, max_length=160)
    prescription_revision_id: str = Field(min_length=1, max_length=160)
    custody_stage: Literal[
        "PREPARED", "STORED", "DISPENSED", "RECEIVED", "AT_BEDSIDE",
        "ADMINISTERED", "RETURNED", "DISCARDED"
    ]
    prepared_fields: dict[str, str] = Field(default_factory=dict)


class PrescriptionChangeEvaluate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changed_from_revision_id: str = Field(min_length=1, max_length=160)
    changed_fields: list[str] = Field(default_factory=list, max_length=100)
    identity_status: Literal["VERIFIED", "MISMATCH", "UNRESOLVED"]


class RevalidationRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_before_revision_id: str = Field(min_length=1, max_length=160)
    source_after_revision_id: str = Field(min_length=1, max_length=160)
    dependency_state: Literal["RESOLVED", "UNRESOLVED"] = "RESOLVED"
    reason_code: str = Field(default="SOURCE_REVISION_CHANGED", min_length=1, max_length=120)


class ClinicalActionComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dependency_state: Literal["RESOLVED", "UNRESOLVED"] = "RESOLVED"


class SourceChangeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_before_revision_id: str = Field(min_length=1, max_length=160)
    payload: dict
    source_state: Literal[
        "CURRENT", "CORRECTED", "SUPERSEDED", "WITHDRAWN",
        "LATEST_UNCONFIRMED", "ACCESS_UNAVAILABLE"
    ] = "CURRENT"
    verification_state: Literal["VERIFIED", "UNVERIFIED", "UNRESOLVED"] = "UNVERIFIED"
    valid_time: str | None = Field(default=None, max_length=80)
    changed_fields: list[str] = Field(default_factory=list, max_length=100)
    dependency_state: Literal["RESOLVED", "UNRESOLVED"] = "RESOLVED"
    reason_code: str = Field(default="SOURCE_REVISION_CHANGED", min_length=1, max_length=120)


class HandoffSnapshotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str = Field(min_length=1, max_length=160)


class ReportSnapshotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str = Field(min_length=1, max_length=160)


class ERWorkflowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str = Field(min_length=1, max_length=160)
    workflow_state: Literal[
        "ARRIVED", "TRIAGED", "SEEN", "TESTING", "RESULTED",
        "CONSULTING", "DECISION", "ADMISSION", "TRANSFER", "DISCHARGE"
    ]
    pending_items: list[str] = Field(default_factory=list, max_length=100)
    responsible_role: str = Field(min_length=1, max_length=80)
    er_workflow_id: str | None = Field(default=None, max_length=160)


class PreopExposureCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str = Field(min_length=1, max_length=160)
    drug_name: str = Field(min_length=1, max_length=160)
    route: str | None = Field(default=None, max_length=80)
    dose_intensity: str | None = Field(default=None, max_length=120)
    start_at: str | None = Field(default=None, max_length=80)
    end_at: str | None = Field(default=None, max_length=80)
    last_exposure_at: str | None = Field(default=None, max_length=80)
    source_revision_id: str | None = Field(default=None, max_length=160)
    verification_state: Literal["VERIFIED", "UNVERIFIED", "UNRESOLVED"] = "UNVERIFIED"
    review_categories: list[str] = Field(default_factory=list, max_length=20)


class ClaimReadinessCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str = Field(min_length=1, max_length=160)
    record_complete: bool
    signature_complete: bool
    order_treatment_consistent: bool
    evidence_complete: bool
    payment_status: str = Field(min_length=1, max_length=40)
    billing_deadline: str | None = Field(default=None, max_length=80)


class HospitalOperationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_type: Literal[
        "STAFF_GAP", "INVENTORY_SHORTAGE", "EQUIPMENT_CHECK",
        "FACILITY_CHECK", "CONTRACT", "LICENSE", "TRAINING", "PURCHASE_ORDER"
    ]
    subject_ref: str = Field(min_length=1, max_length=160)
    state: Literal["OPEN", "IN_PROGRESS", "HOLD", "DONE"]
    owner_role: str = Field(min_length=1, max_length=80)
    due_at: str | None = Field(default=None, max_length=80)
    details: dict[str, str] = Field(default_factory=dict)


class HospitalEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: Literal[
        "source.revision.changed", "prescription.revision.changed",
        "medication.prepared", "medication.dispensed", "medication.received",
        "medication.administered", "identity.verified", "report.delivered",
        "handoff.created", "consult.requested", "consult.responded",
        "test.resulted", "imaging.reported", "preop.review.updated"
    ]
    patient_id: str | None = Field(default=None, max_length=160)
    source_revision_id: str | None = Field(default=None, max_length=160)
    clinical_action_id: str | None = Field(default=None, max_length=160)
    payload: dict = Field(default_factory=dict)


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


def _medication_from_payload(payload: dict) -> MedicationExecutionObject:
    return MedicationExecutionObject(
        medication_object_id=payload["medication_object_id"],
        patient_id=payload["patient_id"],
        clinical_action_id=payload["clinical_action_id"],
        prescription_revision_id=payload["prescription_revision_id"],
        custody_stage=payload["custody_stage"],
        safety_state=payload["safety_state"],
        prepared_fields=dict(payload.get("prepared_fields", {})),
        created_at=payload["created_at"],
        updated_at=payload["updated_at"],
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


def _latest_identity_status(account_id: str, patient_id: str) -> str:
    for item in list_resources(domain="medical", subject_id=account_id, kind=_IDENTITY_KIND):
        payload = item["payload"]
        if payload.get("patient_id") == patient_id:
            return payload.get("status", "UNRESOLVED")
    return "UNRESOLVED"


def _action_source_context(account_id: str, action_id: str) -> tuple[list[SourceRevision], int, int]:
    uses = [
        _use_from_payload(item["payload"])
        for item in list_resources(domain="medical", subject_id=account_id, kind=_USE_KIND)
        if item["payload"].get("clinical_action_id") == action_id
    ]
    source_ids = list(dict.fromkeys(use.source_revision_id for use in uses))
    sources: list[SourceRevision] = []
    missing = 0
    for source_id in source_ids:
        item = get_resource(domain="medical", subject_id=account_id, resource_id=source_id)
        if not item or item["kind"] != _SOURCE_KIND:
            missing += 1
            continue
        sources.append(_source_from_payload(item["payload"]))
    return sources, len(uses), missing


def _store_audit(
    *,
    account_id: str,
    event_type: str,
    patient_id: str,
    clinical_action_id: str | None,
    source_revision_ids: list[str],
    result: str,
    reason_codes: list[str],
) -> str:
    event = make_hospital_audit_event(
        actor_id=account_id,
        event_type=event_type,
        patient_id=patient_id,
        clinical_action_id=clinical_action_id,
        source_revision_ids=source_revision_ids,
        result=result,
        reason_codes=reason_codes,
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_AUDIT_KIND,
        payload=to_payload(event),
        resource_id=event.audit_event_id,
    )
    return event.audit_event_id


@router.get("/status")
def hospital_os_status(request: Request) -> dict:
    account_id = require_life_user(request)
    sources = list_resources(domain="medical", subject_id=account_id, kind=_SOURCE_KIND)
    actions = list_resources(domain="medical", subject_id=account_id, kind=_ACTION_KIND)
    receipts = list_resources(domain="medical", subject_id=account_id, kind=_RECEIPT_KIND)
    identities = list_resources(domain="medical", subject_id=account_id, kind=_IDENTITY_KIND)
    medications = list_resources(domain="medical", subject_id=account_id, kind=_MEDICATION_KIND)
    return {
        "product": "Medical Recover Hospital OS",
        "sources": len(sources),
        "actions": len(actions),
        "current_holds": sum(1 for x in actions if x["payload"].get("action_state") == "HOLD"),
        "revalidation_required": sum(
            1 for x in actions if x["payload"].get("action_state") == "REVALIDATION_REQUIRED"
        ),
        "receipts": len(receipts),
        "identity_checks": len(identities),
        "medication_objects": len(medications),
        "medication_holds": sum(1 for x in medications if x["payload"].get("safety_state") == "HOLD"),
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


@router.post("/identity/verify")
def verify_identity(payload: IdentityCheckRequest, request: Request) -> dict:
    account_id = require_life_user(request)
    result = verify_patient_identity(
        patient_id=payload.patient_id,
        expected=payload.expected,
        observed=payload.observed,
        required_fields=tuple(payload.required_fields),
    )
    resource_id = "idchk_" + __import__("uuid").uuid4().hex
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_IDENTITY_KIND,
        payload=to_payload(result),
        resource_id=resource_id,
    )
    return {
        "identity_check_id": resource_id,
        "status": result.status,
        "completion_allowed": result.completion_allowed,
        "mismatched_fields": list(result.mismatched_fields),
        "unresolved_fields": list(result.unresolved_fields),
    }


@router.post("/medication-objects")
def create_medication_object(payload: MedicationObjectCreate, request: Request) -> dict:
    account_id = require_life_user(request)
    action = _owned_resource(account_id, payload.clinical_action_id, _ACTION_KIND)
    if action["payload"].get("patient_id") != payload.patient_id:
        raise HTTPException(status_code=409, detail="patient_action_mismatch")
    _owned_resource(account_id, payload.prescription_revision_id, _SOURCE_KIND)
    medication = make_medication_execution_object(
        patient_id=payload.patient_id,
        clinical_action_id=payload.clinical_action_id,
        prescription_revision_id=payload.prescription_revision_id,
        custody_stage=payload.custody_stage,
        prepared_fields=payload.prepared_fields,
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_MEDICATION_KIND,
        payload=to_payload(medication),
        resource_id=medication.medication_object_id,
    )
    return {
        "medication_object_id": medication.medication_object_id,
        "safety_state": medication.safety_state,
        "custody_stage": medication.custody_stage,
    }


@router.post("/medication-objects/{medication_object_id}/prescription-change")
def evaluate_medication_change(
    medication_object_id: str,
    payload: PrescriptionChangeEvaluate,
    request: Request,
) -> dict:
    account_id = require_life_user(request)
    item = _owned_resource(account_id, medication_object_id, _MEDICATION_KIND)
    medication = _medication_from_payload(item["payload"])
    decision = evaluate_prescription_change(
        medication=medication,
        changed_from_revision_id=payload.changed_from_revision_id,
        changed_fields=payload.changed_fields,
        identity_status=payload.identity_status,
    )
    if decision.next_state != medication.safety_state:
        updated = MedicationExecutionObject(
            medication_object_id=medication.medication_object_id,
            patient_id=medication.patient_id,
            clinical_action_id=medication.clinical_action_id,
            prescription_revision_id=medication.prescription_revision_id,
            custody_stage=medication.custody_stage,
            safety_state=decision.next_state,
            prepared_fields=medication.prepared_fields,
            created_at=medication.created_at,
            updated_at=utcnow(),
        )
        put_resource(
            domain="medical",
            subject_id=account_id,
            kind=_MEDICATION_KIND,
            payload=to_payload(updated),
            resource_id=updated.medication_object_id,
        )
    return to_payload(decision)


@router.get("/medication-objects")
def list_medication_objects(request: Request) -> dict:
    account_id = require_life_user(request)
    return {
        "medication_objects": [
            item["payload"]
            for item in list_resources(domain="medical", subject_id=account_id, kind=_MEDICATION_KIND)
        ]
    }


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



@router.post("/clinical-actions/{clinical_action_id}/complete")
def complete_clinical_action(
    clinical_action_id: str,
    payload: ClinicalActionComplete,
    request: Request,
) -> dict:
    account_id = require_life_user(request)
    item = _owned_resource(account_id, clinical_action_id, _ACTION_KIND)
    action = _action_from_payload(item["payload"])
    identity_status = _latest_identity_status(account_id, action.patient_id)
    sources, use_count, missing_source_count = _action_source_context(account_id, clinical_action_id)
    updated, decision = apply_action_completion(
        action=action,
        identity_status=identity_status,
        source_revisions=sources,
        actual_use_count=use_count,
        missing_source_count=missing_source_count,
        dependency_state=payload.dependency_state,
    )
    audit_id = _store_audit(
        account_id=account_id,
        event_type="clinical_action.complete",
        patient_id=action.patient_id,
        clinical_action_id=action.clinical_action_id,
        source_revision_ids=list(decision.checked_source_revision_ids),
        result="COMPLETED" if decision.allowed else "BLOCKED",
        reason_codes=list(decision.blocked_reasons),
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "clinical_action_completion_blocked",
                "blocked_reasons": list(decision.blocked_reasons),
                "audit_event_id": audit_id,
            },
        )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_ACTION_KIND,
        payload=to_payload(updated),
        resource_id=updated.clinical_action_id,
    )
    return {
        "clinical_action_id": updated.clinical_action_id,
        "action_state": updated.action_state,
        "completed_at": updated.completed_at,
        "audit_event_id": audit_id,
    }


@router.post("/source-changes")
def process_source_change(payload: SourceChangeCreate, request: Request) -> dict:
    account_id = require_life_user(request)
    before_item = _owned_resource(account_id, payload.source_before_revision_id, _SOURCE_KIND)
    before = _source_from_payload(before_item["payload"])
    try:
        after = make_source_revision(
            source_id=before.source_id,
            payload=payload.payload,
            source_state=payload.source_state,
            verification_state=payload.verification_state,
            predecessor_revision_id=before.source_revision_id,
            valid_time=payload.valid_time,
            changed_fields=payload.changed_fields,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_SOURCE_KIND,
        payload=to_payload(after),
        resource_id=after.source_revision_id,
    )
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
        "source_after_revision_id": after.source_revision_id,
        "receipt_id": receipt.receipt_id,
        "affected_action_ids": list(receipt.affected_action_ids),
        "unaffected_action_ids": list(receipt.unaffected_action_ids),
        "unresolved_scope": list(receipt.unresolved_scope),
        "post_states": receipt.post_states,
    }


@router.post("/handoff/snapshot")
def create_handoff_snapshot(payload: HandoffSnapshotCreate, request: Request) -> dict:
    account_id = require_life_user(request)
    actions = [
        item["payload"]
        for item in list_resources(domain="medical", subject_id=account_id, kind=_ACTION_KIND)
        if item["payload"].get("patient_id") == payload.patient_id
    ]
    action_ids = {item["clinical_action_id"] for item in actions}
    uses = [
        item["payload"]
        for item in list_resources(domain="medical", subject_id=account_id, kind=_USE_KIND)
        if item["payload"].get("clinical_action_id") in action_ids
    ]
    source_ids = list(dict.fromkeys(item["source_revision_id"] for item in uses))
    medications = [
        item["payload"]
        for item in list_resources(domain="medical", subject_id=account_id, kind=_MEDICATION_KIND)
        if item["payload"].get("patient_id") == payload.patient_id
    ]
    snapshot = make_handoff_snapshot(
        patient_id=payload.patient_id,
        source_revision_ids=source_ids,
        action_states={item["clinical_action_id"]: item["action_state"] for item in actions},
        medication_states={item["medication_object_id"]: item["safety_state"] for item in medications},
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_HANDOFF_KIND,
        payload=to_payload(snapshot),
        resource_id=snapshot.handoff_snapshot_id,
    )
    return {
        "handoff_snapshot_id": snapshot.handoff_snapshot_id,
        "patient_id": snapshot.patient_id,
        "created_at": snapshot.created_at,
    }


@router.get("/handoff/{handoff_snapshot_id}/delta")
def handoff_delta(handoff_snapshot_id: str, request: Request) -> dict:
    account_id = require_life_user(request)
    item = _owned_resource(account_id, handoff_snapshot_id, _HANDOFF_KIND)
    snapshot = item["payload"]
    patient_id = snapshot["patient_id"]
    actions = [
        x["payload"]
        for x in list_resources(domain="medical", subject_id=account_id, kind=_ACTION_KIND)
        if x["payload"].get("patient_id") == patient_id
    ]
    action_ids = {x["clinical_action_id"] for x in actions}
    uses = [
        x["payload"]
        for x in list_resources(domain="medical", subject_id=account_id, kind=_USE_KIND)
        if x["payload"].get("clinical_action_id") in action_ids
    ]
    current_source_ids = list(dict.fromkeys(x["source_revision_id"] for x in uses))
    baseline_sources = set(snapshot.get("source_revision_ids", []))
    baseline_actions = snapshot.get("action_states", {})
    medications = [
        x["payload"]
        for x in list_resources(domain="medical", subject_id=account_id, kind=_MEDICATION_KIND)
        if x["payload"].get("patient_id") == patient_id
    ]
    baseline_medications = snapshot.get("medication_states", {})
    changed_actions = [
        {
            "clinical_action_id": x["clinical_action_id"],
            "before": baseline_actions.get(x["clinical_action_id"]),
            "after": x["action_state"],
        }
        for x in actions
        if baseline_actions.get(x["clinical_action_id"]) != x["action_state"]
    ]
    changed_medications = [
        {
            "medication_object_id": x["medication_object_id"],
            "before": baseline_medications.get(x["medication_object_id"]),
            "after": x["safety_state"],
        }
        for x in medications
        if baseline_medications.get(x["medication_object_id"]) != x["safety_state"]
    ]
    return {
        "handoff_snapshot_id": handoff_snapshot_id,
        "patient_id": patient_id,
        "new_source_revision_ids": [x for x in current_source_ids if x not in baseline_sources],
        "changed_actions": changed_actions,
        "changed_medications": changed_medications,
    }


@router.get("/audit-events")
def list_audit_events(request: Request) -> dict:
    account_id = require_life_user(request)
    return {
        "audit_events": [
            item["payload"]
            for item in list_resources(domain="medical", subject_id=account_id, kind=_AUDIT_KIND)
        ]
    }


@router.post("/reports/snapshot")
def create_report_snapshot(payload: ReportSnapshotCreate, request: Request) -> dict:
    account_id = require_life_user(request)
    actions = [
        item["payload"]
        for item in list_resources(domain="medical", subject_id=account_id, kind=_ACTION_KIND)
        if item["payload"].get("patient_id") == payload.patient_id
    ]
    action_ids = {item["clinical_action_id"] for item in actions}
    uses = [
        item["payload"]
        for item in list_resources(domain="medical", subject_id=account_id, kind=_USE_KIND)
        if item["payload"].get("clinical_action_id") in action_ids
    ]
    source_ids = list(dict.fromkeys(item["source_revision_id"] for item in uses))
    snapshot = make_report_snapshot(
        patient_id=payload.patient_id,
        source_revision_ids=source_ids,
        action_states={item["clinical_action_id"]: item["action_state"] for item in actions},
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_REPORT_KIND,
        payload=to_payload(snapshot),
        resource_id=snapshot.report_snapshot_id,
    )
    return {
        "report_snapshot_id": snapshot.report_snapshot_id,
        "patient_id": snapshot.patient_id,
        "created_at": snapshot.created_at,
        "unresolved_action_ids": list(snapshot.unresolved_action_ids),
        "pending_action_ids": list(snapshot.pending_action_ids),
        "source_revision_ids": list(snapshot.source_revision_ids),
    }


@router.get("/reports/{report_snapshot_id}/delta")
def report_delta(report_snapshot_id: str, request: Request) -> dict:
    account_id = require_life_user(request)
    item = _owned_resource(account_id, report_snapshot_id, _REPORT_KIND)
    snapshot = item["payload"]
    patient_id = snapshot["patient_id"]
    actions = [
        x["payload"]
        for x in list_resources(domain="medical", subject_id=account_id, kind=_ACTION_KIND)
        if x["payload"].get("patient_id") == patient_id
    ]
    baseline_actions = snapshot.get("action_states", {})
    action_ids = {x["clinical_action_id"] for x in actions}
    uses = [
        x["payload"]
        for x in list_resources(domain="medical", subject_id=account_id, kind=_USE_KIND)
        if x["payload"].get("clinical_action_id") in action_ids
    ]
    current_source_ids = list(dict.fromkeys(x["source_revision_id"] for x in uses))
    baseline_sources = set(snapshot.get("source_revision_ids", []))
    return {
        "report_snapshot_id": report_snapshot_id,
        "patient_id": patient_id,
        "new_source_revision_ids": [x for x in current_source_ids if x not in baseline_sources],
        "changed_actions": [
            {
                "clinical_action_id": x["clinical_action_id"],
                "before": baseline_actions.get(x["clinical_action_id"]),
                "after": x["action_state"],
            }
            for x in actions
            if baseline_actions.get(x["clinical_action_id"]) != x["action_state"]
        ],
    }


@router.post("/er/workflows")
def upsert_er_workflow(payload: ERWorkflowCreate, request: Request) -> dict:
    account_id = require_life_user(request)
    item = make_er_workflow_item(
        patient_id=payload.patient_id,
        workflow_state=payload.workflow_state,
        pending_items=payload.pending_items,
        responsible_role=payload.responsible_role,
        er_workflow_id=payload.er_workflow_id,
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_ER_WORKFLOW_KIND,
        payload=to_payload(item),
        resource_id=item.er_workflow_id,
    )
    return to_payload(item)


@router.get("/er/unfinished-loops")
def er_unfinished_loops(request: Request) -> dict:
    account_id = require_life_user(request)
    finished = {"ADMISSION", "TRANSFER", "DISCHARGE"}
    workflows = [
        item["payload"]
        for item in list_resources(domain="medical", subject_id=account_id, kind=_ER_WORKFLOW_KIND)
    ]
    claims = [
        item["payload"]
        for item in list_resources(domain="medical", subject_id=account_id, kind=_CLAIM_KIND)
    ]
    operations = [
        item["payload"]
        for item in list_resources(domain="medical", subject_id=account_id, kind=_OPERATION_KIND)
    ]
    return {
        "unfinished_loops": [
            item for item in workflows
            if item.get("workflow_state") not in finished or item.get("pending_items")
        ]
    }


@router.post("/preop/exposures")
def create_preop_exposure(payload: PreopExposureCreate, request: Request) -> dict:
    account_id = require_life_user(request)
    if payload.source_revision_id:
        _owned_resource(account_id, payload.source_revision_id, _SOURCE_KIND)
    exposure = make_preop_exposure(
        patient_id=payload.patient_id,
        drug_name=payload.drug_name,
        route=payload.route,
        dose_intensity=payload.dose_intensity,
        start_at=payload.start_at,
        end_at=payload.end_at,
        last_exposure_at=payload.last_exposure_at,
        source_revision_id=payload.source_revision_id,
        verification_state=payload.verification_state,
        review_categories=payload.review_categories,
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_PREOP_KIND,
        payload=to_payload(exposure),
        resource_id=exposure.exposure_id,
    )
    return to_payload(exposure)


@router.get("/patients/{patient_id}/preop-exposures")
def list_preop_exposures(patient_id: str, request: Request) -> dict:
    account_id = require_life_user(request)
    return {
        "preop_exposures": [
            item["payload"]
            for item in list_resources(domain="medical", subject_id=account_id, kind=_PREOP_KIND)
            if item["payload"].get("patient_id") == patient_id
        ]
    }


@router.get("/patients/{patient_id}/current-actions")
def patient_current_actions(patient_id: str, request: Request) -> dict:
    account_id = require_life_user(request)
    live_states = {"INCOMPLETE", "CURRENT", "HOLD", "STALE", "REVALIDATION_REQUIRED"}
    return {
        "actions": [
            item["payload"]
            for item in list_resources(domain="medical", subject_id=account_id, kind=_ACTION_KIND)
            if item["payload"].get("patient_id") == patient_id
            and item["payload"].get("action_state") in live_states
        ]
    }


@router.get("/patients/{patient_id}/medications")
def patient_medications(patient_id: str, request: Request) -> dict:
    account_id = require_life_user(request)
    return {
        "medication_objects": [
            item["payload"]
            for item in list_resources(domain="medical", subject_id=account_id, kind=_MEDICATION_KIND)
            if item["payload"].get("patient_id") == patient_id
        ]
    }


@router.get("/hospital/today")
def hospital_today(request: Request) -> dict:
    account_id = require_life_user(request)
    actions = [
        item["payload"]
        for item in list_resources(domain="medical", subject_id=account_id, kind=_ACTION_KIND)
    ]
    medications = [
        item["payload"]
        for item in list_resources(domain="medical", subject_id=account_id, kind=_MEDICATION_KIND)
    ]
    workflows = [
        item["payload"]
        for item in list_resources(domain="medical", subject_id=account_id, kind=_ER_WORKFLOW_KIND)
    ]
    return {
        "current_holds": sum(1 for x in actions if x.get("action_state") == "HOLD"),
        "revalidation_required": sum(
            1 for x in actions if x.get("action_state") == "REVALIDATION_REQUIRED"
        ),
        "medication_holds": sum(1 for x in medications if x.get("safety_state") == "HOLD"),
        "er_unfinished_loops": sum(
            1 for x in workflows
            if x.get("workflow_state") not in {"ADMISSION", "TRANSFER", "DISCHARGE"}
            or x.get("pending_items")
        ),
        "claim_review_needed": sum(1 for x in claims if x.get("review_reasons")),
        "inventory_shortages": sum(
            1 for x in operations
            if x.get("operation_type") == "INVENTORY_SHORTAGE" and x.get("state") != "DONE"
        ),
        "staff_gaps": sum(
            1 for x in operations
            if x.get("operation_type") == "STAFF_GAP" and x.get("state") != "DONE"
        ),
        "operations_hold": sum(1 for x in operations if x.get("state") == "HOLD"),
    }



@router.post("/claims/readiness")
def create_claim_readiness(payload: ClaimReadinessCreate, request: Request) -> dict:
    account_id = require_life_user(request)
    assessment = assess_claim_readiness(
        patient_id=payload.patient_id,
        record_complete=payload.record_complete,
        signature_complete=payload.signature_complete,
        order_treatment_consistent=payload.order_treatment_consistent,
        evidence_complete=payload.evidence_complete,
        payment_status=payload.payment_status,
        billing_deadline=payload.billing_deadline,
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_CLAIM_KIND,
        payload=to_payload(assessment),
        resource_id=assessment.claim_readiness_id,
    )
    return to_payload(assessment)


@router.get("/claims/readiness")
def list_claim_readiness(request: Request) -> dict:
    account_id = require_life_user(request)
    return {
        "claims": [
            item["payload"]
            for item in list_resources(domain="medical", subject_id=account_id, kind=_CLAIM_KIND)
        ]
    }


@router.post("/operations")
def create_hospital_operation(payload: HospitalOperationCreate, request: Request) -> dict:
    account_id = require_life_user(request)
    record = make_hospital_operation_record(
        operation_type=payload.operation_type,
        subject_ref=payload.subject_ref,
        state=payload.state,
        owner_role=payload.owner_role,
        due_at=payload.due_at,
        details=payload.details,
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_OPERATION_KIND,
        payload=to_payload(record),
        resource_id=record.operation_record_id,
    )
    return to_payload(record)


@router.get("/operations")
def list_hospital_operations(request: Request) -> dict:
    account_id = require_life_user(request)
    return {
        "operations": [
            item["payload"]
            for item in list_resources(domain="medical", subject_id=account_id, kind=_OPERATION_KIND)
        ]
    }


@router.post("/events")
def create_hospital_event(payload: HospitalEventCreate, request: Request) -> dict:
    account_id = require_life_user(request)
    if payload.source_revision_id:
        _owned_resource(account_id, payload.source_revision_id, _SOURCE_KIND)
    if payload.clinical_action_id:
        _owned_resource(account_id, payload.clinical_action_id, _ACTION_KIND)
    event = make_hospital_event(
        event_type=payload.event_type,
        patient_id=payload.patient_id,
        source_revision_id=payload.source_revision_id,
        clinical_action_id=payload.clinical_action_id,
        payload=payload.payload,
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_EVENT_KIND,
        payload=to_payload(event),
        resource_id=event.event_id,
    )
    return to_payload(event)


@router.get("/patients/{patient_id}/timeline")
def patient_timeline(patient_id: str, request: Request) -> dict:
    account_id = require_life_user(request)
    rows: list[dict] = []
    mappings = (
        (_SOURCE_KIND, "recorded_time", "source_revision"),
        (_ACTION_KIND, "created_at", "clinical_action"),
        (_IDENTITY_KIND, None, "identity_check"),
        (_MEDICATION_KIND, "updated_at", "medication"),
        (_PREOP_KIND, "created_at", "preop_exposure"),
        (_EVENT_KIND, "occurred_at", "event"),
    )
    for kind, time_field, item_type in mappings:
        for item in list_resources(domain="medical", subject_id=account_id, kind=kind):
            payload = item["payload"]
            payload_patient = payload.get("patient_id")
            if kind == _SOURCE_KIND:
                continue
            if payload_patient != patient_id:
                continue
            rows.append({
                "type": item_type,
                "time": payload.get(time_field) if time_field else item.get("updated_at"),
                "payload": payload,
            })
    rows.sort(key=lambda row: row.get("time") or "")
    return {"patient_id": patient_id, "timeline": rows}


@router.get("/patients/{patient_id}/deltas")
def patient_deltas(patient_id: str, request: Request, since: str | None = None) -> dict:
    account_id = require_life_user(request)
    events = [
        item["payload"]
        for item in list_resources(domain="medical", subject_id=account_id, kind=_EVENT_KIND)
        if item["payload"].get("patient_id") == patient_id
        and (since is None or (item["payload"].get("occurred_at") or "") > since)
    ]
    events.sort(key=lambda item: item.get("occurred_at") or "")
    return {"patient_id": patient_id, "since": since, "events": events}


@router.get("/clinical-actions")
def list_clinical_actions(request: Request) -> dict:
    account_id = require_life_user(request)
    return {
        "actions": [
            item["payload"]
            for item in list_resources(domain="medical", subject_id=account_id, kind=_ACTION_KIND)
        ]
    }
