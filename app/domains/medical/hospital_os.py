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
IdentityStatus = Literal["VERIFIED", "MISMATCH", "UNRESOLVED"]
CustodyStage = Literal[
    "PREPARED", "STORED", "DISPENSED", "RECEIVED", "AT_BEDSIDE",
    "ADMINISTERED", "RETURNED", "DISCARDED"
]
MedicationSafetyState = Literal["READY", "HOLD", "REVALIDATION_REQUIRED", "CLOSED"]


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


@dataclass(frozen=True)
class IdentityCheck:
    patient_id: str
    status: IdentityStatus
    matched_fields: tuple[str, ...]
    mismatched_fields: tuple[str, ...]
    unresolved_fields: tuple[str, ...]
    completion_allowed: bool
    reason: str


@dataclass(frozen=True)
class MedicationExecutionObject:
    medication_object_id: str
    patient_id: str
    clinical_action_id: str
    prescription_revision_id: str
    custody_stage: CustodyStage
    safety_state: MedicationSafetyState
    prepared_fields: dict[str, str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MedicationRevisionDecision:
    medication_object_id: str
    previous_state: MedicationSafetyState
    next_state: MedicationSafetyState
    affected: bool
    reason: str
    required_action: str


def verify_patient_identity(
    *,
    patient_id: str,
    expected: dict[str, str | None],
    observed: dict[str, str | None],
    required_fields: tuple[str, ...] = ("name", "second_identifier"),
) -> IdentityCheck:
    """Compare only explicit identifiers; never infer identity from partial context."""
    matched: list[str] = []
    mismatched: list[str] = []
    unresolved: list[str] = []

    for field in required_fields:
        expected_value = (expected.get(field) or "").strip()
        observed_value = (observed.get(field) or "").strip()
        if not expected_value or not observed_value:
            unresolved.append(field)
        elif expected_value == observed_value:
            matched.append(field)
        else:
            mismatched.append(field)

    if mismatched:
        status: IdentityStatus = "MISMATCH"
        reason = "identity_mismatch"
    elif unresolved:
        status = "UNRESOLVED"
        reason = "identity_information_incomplete"
    else:
        status = "VERIFIED"
        reason = "required_identifiers_match"

    return IdentityCheck(
        patient_id=patient_id,
        status=status,
        matched_fields=tuple(matched),
        mismatched_fields=tuple(mismatched),
        unresolved_fields=tuple(unresolved),
        completion_allowed=status == "VERIFIED",
        reason=reason,
    )


def make_medication_execution_object(
    *,
    patient_id: str,
    clinical_action_id: str,
    prescription_revision_id: str,
    custody_stage: CustodyStage,
    prepared_fields: dict[str, str],
) -> MedicationExecutionObject:
    now = utcnow()
    return MedicationExecutionObject(
        medication_object_id=f"medobj_{uuid.uuid4().hex}",
        patient_id=patient_id,
        clinical_action_id=clinical_action_id,
        prescription_revision_id=prescription_revision_id,
        custody_stage=custody_stage,
        safety_state="CLOSED" if custody_stage in {"ADMINISTERED", "RETURNED", "DISCARDED"} else "READY",
        prepared_fields=dict(prepared_fields),
        created_at=now,
        updated_at=now,
    )


def evaluate_prescription_change(
    *,
    medication: MedicationExecutionObject,
    changed_from_revision_id: str,
    changed_fields: list[str],
    identity_status: IdentityStatus,
) -> MedicationRevisionDecision:
    """Apply selective medication hold without making a prescribing decision."""
    if medication.prescription_revision_id != changed_from_revision_id:
        return MedicationRevisionDecision(
            medication_object_id=medication.medication_object_id,
            previous_state=medication.safety_state,
            next_state=medication.safety_state,
            affected=False,
            reason="different_prescription_revision",
            required_action="NONE",
        )

    if medication.custody_stage in {"ADMINISTERED", "RETURNED", "DISCARDED"}:
        return MedicationRevisionDecision(
            medication_object_id=medication.medication_object_id,
            previous_state=medication.safety_state,
            next_state="CLOSED",
            affected=False,
            reason="medication_object_already_closed",
            required_action="NONE",
        )

    safety_relevant_fields = {"drug", "dose", "concentration", "route", "time", "cancelled"}
    normalized = {field.strip().lower() for field in changed_fields if field and field.strip()}
    impact_unknown = not normalized
    medication_impact = bool(normalized & safety_relevant_fields)

    if identity_status != "VERIFIED":
        return MedicationRevisionDecision(
            medication_object_id=medication.medication_object_id,
            previous_state=medication.safety_state,
            next_state="HOLD",
            affected=True,
            reason="patient_identity_not_verified",
            required_action="VERIFY_PATIENT_IDENTITY",
        )

    if impact_unknown:
        return MedicationRevisionDecision(
            medication_object_id=medication.medication_object_id,
            previous_state=medication.safety_state,
            next_state="HOLD",
            affected=True,
            reason="prescription_change_impact_unknown",
            required_action="REVIEW_CHANGED_PRESCRIPTION",
        )

    if not medication_impact:
        return MedicationRevisionDecision(
            medication_object_id=medication.medication_object_id,
            previous_state=medication.safety_state,
            next_state=medication.safety_state,
            affected=False,
            reason="changed_fields_do_not_affect_prepared_medication",
            required_action="NONE",
        )

    action_by_stage = {
        "PREPARED": "STOP_DISPENSING_AND_REPREPARE",
        "STORED": "STOP_DISPENSING_AND_REPREPARE",
        "DISPENSED": "RECALL_OR_RECHECK",
        "RECEIVED": "RECALL_OR_RECHECK",
        "AT_BEDSIDE": "BLOCK_ADMINISTRATION_AND_RECHECK",
    }
    return MedicationRevisionDecision(
        medication_object_id=medication.medication_object_id,
        previous_state=medication.safety_state,
        next_state="HOLD",
        affected=True,
        reason="safety_relevant_prescription_field_changed",
        required_action=action_by_stage.get(medication.custody_stage, "RECHECK_BEFORE_ADMINISTRATION"),
    )


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



@dataclass(frozen=True)
class CompletionDecision:
    clinical_action_id: str
    allowed: bool
    next_state: ActionState
    blocked_reasons: tuple[str, ...]
    checked_source_revision_ids: tuple[str, ...]
    identity_status: IdentityStatus
    dependency_state: DependencyState


@dataclass(frozen=True)
class HospitalAuditEvent:
    audit_event_id: str
    actor_id: str
    event_type: str
    patient_id: str
    clinical_action_id: str | None
    source_revision_ids: tuple[str, ...]
    occurred_at: str
    result: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class HandoffSnapshot:
    handoff_snapshot_id: str
    patient_id: str
    created_at: str
    source_revision_ids: tuple[str, ...]
    action_states: dict[str, str]
    medication_states: dict[str, str]


def evaluate_action_completion(
    *,
    action: ClinicalAction,
    identity_status: IdentityStatus,
    source_revisions: list[SourceRevision],
    actual_use_count: int,
    missing_source_count: int = 0,
    dependency_state: DependencyState = "RESOLVED",
) -> CompletionDecision:
    """Fail closed when required identity or source evidence is unresolved.

    This is a workflow safety gate. It does not decide diagnosis, treatment,
    prescription, or clinical appropriateness.
    """
    reasons: list[str] = []

    if action.action_state in {"HOLD", "STALE", "REVALIDATION_REQUIRED"}:
        reasons.append("ACTION_NOT_COMPLETABLE_IN_CURRENT_STATE")
    if identity_status != "VERIFIED":
        reasons.append("PATIENT_IDENTITY_NOT_VERIFIED")
    if dependency_state != "RESOLVED":
        reasons.append("DEPENDENCY_UNRESOLVED")
    if actual_use_count <= 0:
        reasons.append("ACTUAL_USE_EVIDENCE_REQUIRED")
    if missing_source_count > 0:
        reasons.append("USED_SOURCE_REVISION_MISSING")

    blocked_source_states = {
        "SUPERSEDED", "WITHDRAWN", "LATEST_UNCONFIRMED", "ACCESS_UNAVAILABLE"
    }
    for source in source_revisions:
        if source.verification_state != "VERIFIED":
            reasons.append("USED_SOURCE_NOT_VERIFIED")
        if source.source_state in blocked_source_states:
            reasons.append("USED_SOURCE_NOT_CURRENTLY_USABLE")

    unique_reasons = tuple(dict.fromkeys(reasons))
    allowed = not unique_reasons
    return CompletionDecision(
        clinical_action_id=action.clinical_action_id,
        allowed=allowed,
        next_state="CLOSED_HISTORICAL" if allowed else action.action_state,
        blocked_reasons=unique_reasons,
        checked_source_revision_ids=tuple(
            source.source_revision_id for source in source_revisions
        ),
        identity_status=identity_status,
        dependency_state=dependency_state,
    )


def apply_action_completion(
    *,
    action: ClinicalAction,
    identity_status: IdentityStatus,
    source_revisions: list[SourceRevision],
    actual_use_count: int,
    missing_source_count: int = 0,
    dependency_state: DependencyState = "RESOLVED",
) -> tuple[ClinicalAction, CompletionDecision]:
    decision = evaluate_action_completion(
        action=action,
        identity_status=identity_status,
        source_revisions=source_revisions,
        actual_use_count=actual_use_count,
        missing_source_count=missing_source_count,
        dependency_state=dependency_state,
    )
    if not decision.allowed:
        return action, decision
    return (
        replace(
            action,
            action_state="CLOSED_HISTORICAL",
            completed_at=utcnow(),
            current_validity="COMPLETED",
        ),
        decision,
    )


def make_hospital_audit_event(
    *,
    actor_id: str,
    event_type: str,
    patient_id: str,
    clinical_action_id: str | None = None,
    source_revision_ids: list[str] | tuple[str, ...] = (),
    result: str,
    reason_codes: list[str] | tuple[str, ...] = (),
) -> HospitalAuditEvent:
    return HospitalAuditEvent(
        audit_event_id=f"audit_{uuid.uuid4().hex}",
        actor_id=actor_id,
        event_type=event_type,
        patient_id=patient_id,
        clinical_action_id=clinical_action_id,
        source_revision_ids=tuple(source_revision_ids),
        occurred_at=utcnow(),
        result=result,
        reason_codes=tuple(reason_codes),
    )


def make_handoff_snapshot(
    *,
    patient_id: str,
    source_revision_ids: list[str],
    action_states: dict[str, str],
    medication_states: dict[str, str],
) -> HandoffSnapshot:
    return HandoffSnapshot(
        handoff_snapshot_id=f"handoff_{uuid.uuid4().hex}",
        patient_id=patient_id,
        created_at=utcnow(),
        source_revision_ids=tuple(source_revision_ids),
        action_states=dict(action_states),
        medication_states=dict(medication_states),
    )


ERWorkflowState = Literal[
    "ARRIVED", "TRIAGED", "SEEN", "TESTING", "RESULTED",
    "CONSULTING", "DECISION", "ADMISSION", "TRANSFER", "DISCHARGE"
]


@dataclass(frozen=True)
class ReportSnapshot:
    report_snapshot_id: str
    patient_id: str
    created_at: str
    source_revision_ids: tuple[str, ...]
    action_states: dict[str, str]
    unresolved_action_ids: tuple[str, ...]
    pending_action_ids: tuple[str, ...]


@dataclass(frozen=True)
class ERWorkflowItem:
    er_workflow_id: str
    patient_id: str
    workflow_state: ERWorkflowState
    pending_items: tuple[str, ...]
    responsible_role: str
    updated_at: str


@dataclass(frozen=True)
class PreopExposure:
    exposure_id: str
    patient_id: str
    drug_name: str
    ingredient: str | None
    purpose: str | None
    route: str | None
    dose_intensity: str | None
    start_at: str | None
    end_at: str | None
    last_exposure_at: str | None
    source_revision_id: str | None
    verification_state: VerificationState
    review_categories: tuple[str, ...]
    missing_fields: tuple[str, ...]
    is_steroid_exposure: bool
    steroid_route_category: str | None
    created_at: str


def make_report_snapshot(
    *,
    patient_id: str,
    source_revision_ids: list[str],
    action_states: dict[str, str],
) -> ReportSnapshot:
    unresolved_states = {"HOLD", "STALE", "REVALIDATION_REQUIRED"}
    pending_states = {"INCOMPLETE", "CURRENT"}
    return ReportSnapshot(
        report_snapshot_id=f"report_{uuid.uuid4().hex}",
        patient_id=patient_id,
        created_at=utcnow(),
        source_revision_ids=tuple(source_revision_ids),
        action_states=dict(action_states),
        unresolved_action_ids=tuple(
            action_id for action_id, state in action_states.items()
            if state in unresolved_states
        ),
        pending_action_ids=tuple(
            action_id for action_id, state in action_states.items()
            if state in pending_states
        ),
    )


def make_er_workflow_item(
    *,
    patient_id: str,
    workflow_state: ERWorkflowState,
    pending_items: list[str],
    responsible_role: str,
    er_workflow_id: str | None = None,
) -> ERWorkflowItem:
    return ERWorkflowItem(
        er_workflow_id=er_workflow_id or f"er_{uuid.uuid4().hex}",
        patient_id=patient_id,
        workflow_state=workflow_state,
        pending_items=tuple(item for item in pending_items if item),
        responsible_role=responsible_role,
        updated_at=utcnow(),
    )


def make_preop_exposure(
    *,
    patient_id: str,
    drug_name: str,
    ingredient: str | None = None,
    purpose: str | None = None,
    route: str | None,
    dose_intensity: str | None,
    start_at: str | None,
    end_at: str | None,
    last_exposure_at: str | None,
    source_revision_id: str | None,
    verification_state: VerificationState,
    review_categories: list[str],
    is_steroid_exposure: bool = False,
) -> PreopExposure:
    values = {
        "ingredient": ingredient,
        "purpose": purpose,
        "route": route,
        "dose_intensity": dose_intensity,
        "last_exposure_at": last_exposure_at,
        "source_revision_id": source_revision_id,
    }
    missing_fields = tuple(name for name, value in values.items() if not value)
    normalized_route = (route or "").strip().lower().replace("_", "-")
    steroid_routes = {
        "oral": "ORAL",
        "iv": "IV",
        "intravenous": "IV",
        "im": "IM",
        "intramuscular": "IM",
        "epidural": "EPIDURAL",
        "intra-articular": "INTRA_ARTICULAR",
        "inhaled": "INHALED",
        "topical": "TOPICAL",
    }
    steroid_route_category = (
        steroid_routes.get(normalized_route) if is_steroid_exposure else None
    )
    categories = list(dict.fromkeys(review_categories))
    if is_steroid_exposure and steroid_route_category is None:
        categories.append("EXPOSURE_UNKNOWN")
    return PreopExposure(
        exposure_id=f"preop_{uuid.uuid4().hex}",
        patient_id=patient_id,
        drug_name=drug_name,
        ingredient=ingredient,
        purpose=purpose,
        route=route,
        dose_intensity=dose_intensity,
        start_at=start_at,
        end_at=end_at,
        last_exposure_at=last_exposure_at,
        source_revision_id=source_revision_id,
        verification_state=verification_state,
        review_categories=tuple(dict.fromkeys(categories)),
        missing_fields=missing_fields,
        is_steroid_exposure=is_steroid_exposure,
        steroid_route_category=steroid_route_category,
        created_at=utcnow(),
    )


CareSetting = Literal["OUTPATIENT", "INPATIENT", "ER"]
PatientOperationalState = Literal["CURRENT", "DISCHARGED"]


@dataclass(frozen=True)
class PatientOperationalStatus:
    patient_status_id: str
    patient_id: str
    care_setting: CareSetting
    location: str | None
    attending_team: str | None
    discharge_planned: bool
    surgery_planned: bool
    state: PatientOperationalState
    updated_at: str


def make_patient_operational_status(
    *,
    patient_id: str,
    care_setting: CareSetting,
    location: str | None,
    attending_team: str | None,
    discharge_planned: bool,
    surgery_planned: bool,
    state: PatientOperationalState,
    patient_status_id: str | None = None,
) -> PatientOperationalStatus:
    return PatientOperationalStatus(
        patient_status_id=patient_status_id or f"pstat_{uuid.uuid4().hex}",
        patient_id=patient_id,
        care_setting=care_setting,
        location=location,
        attending_team=attending_team,
        discharge_planned=discharge_planned,
        surgery_planned=surgery_planned,
        state=state,
        updated_at=utcnow(),
    )


@dataclass(frozen=True)
class ClaimReadiness:
    claim_readiness_id: str
    patient_id: str
    record_complete: bool
    signature_complete: bool
    order_treatment_consistent: bool
    evidence_complete: bool
    payment_status: str
    billing_deadline: str | None
    review_reasons: tuple[str, ...]
    ready_for_human_review: bool
    created_at: str


@dataclass(frozen=True)
class HospitalOperationRecord:
    operation_record_id: str
    operation_type: str
    subject_ref: str
    state: str
    owner_role: str
    due_at: str | None
    details: dict[str, str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class HospitalEvent:
    event_id: str
    event_type: str
    patient_id: str | None
    source_revision_id: str | None
    clinical_action_id: str | None
    payload_hash: str
    occurred_at: str


def assess_claim_readiness(
    *,
    patient_id: str,
    record_complete: bool,
    signature_complete: bool,
    order_treatment_consistent: bool,
    evidence_complete: bool,
    payment_status: str,
    billing_deadline: str | None,
) -> ClaimReadiness:
    reasons: list[str] = []
    if not record_complete:
        reasons.append("RECORD_INCOMPLETE")
    if not signature_complete:
        reasons.append("SIGNATURE_INCOMPLETE")
    if not order_treatment_consistent:
        reasons.append("ORDER_TREATMENT_MISMATCH_REVIEW")
    if not evidence_complete:
        reasons.append("EVIDENCE_INCOMPLETE")
    if payment_status.strip().upper() in {"UNPAID", "PARTIAL", "UNKNOWN"}:
        reasons.append("PAYMENT_REVIEW")
    return ClaimReadiness(
        claim_readiness_id=f"claim_{uuid.uuid4().hex}",
        patient_id=patient_id,
        record_complete=record_complete,
        signature_complete=signature_complete,
        order_treatment_consistent=order_treatment_consistent,
        evidence_complete=evidence_complete,
        payment_status=payment_status,
        billing_deadline=billing_deadline,
        review_reasons=tuple(reasons),
        ready_for_human_review=not reasons,
        created_at=utcnow(),
    )


def make_hospital_operation_record(
    *,
    operation_type: str,
    subject_ref: str,
    state: str,
    owner_role: str,
    due_at: str | None,
    details: dict[str, str],
    operation_record_id: str | None = None,
) -> HospitalOperationRecord:
    now = utcnow()
    return HospitalOperationRecord(
        operation_record_id=operation_record_id or f"ops_{uuid.uuid4().hex}",
        operation_type=operation_type,
        subject_ref=subject_ref,
        state=state,
        owner_role=owner_role,
        due_at=due_at,
        details=dict(details),
        created_at=now,
        updated_at=now,
    )


def make_hospital_event(
    *,
    event_type: str,
    patient_id: str | None,
    source_revision_id: str | None,
    clinical_action_id: str | None,
    payload: dict,
) -> HospitalEvent:
    return HospitalEvent(
        event_id=f"evt_{uuid.uuid4().hex}",
        event_type=event_type,
        patient_id=patient_id,
        source_revision_id=source_revision_id,
        clinical_action_id=clinical_action_id,
        payload_hash=stable_hash(payload),
        occurred_at=utcnow(),
    )


CarriedMedicationState = Literal[
    "CANDIDATE", "UNVERIFIED", "PHARMACIST_VERIFIED",
    "CONTINUE", "STOP", "SUBSTITUTE", "MORE_INFO",
]


@dataclass(frozen=True)
class CarriedMedicationRecord:
    carried_medication_id: str
    patient_id: str
    captured_description: str
    imprint: str | None
    packaging: str | None
    ingredient_candidates: tuple[str, ...]
    formulary_matches: tuple[str, ...]
    external_record_refs: tuple[str, ...]
    state: CarriedMedicationState
    pharmacist_actor_id: str | None
    verified_ingredient: str | None
    physician_actor_id: str | None
    physician_decision: str | None
    created_at: str
    updated_at: str


def make_carried_medication_record(
    *,
    patient_id: str,
    captured_description: str,
    imprint: str | None = None,
    packaging: str | None = None,
    ingredient_candidates: list[str] | None = None,
    formulary_matches: list[str] | None = None,
    external_record_refs: list[str] | None = None,
) -> CarriedMedicationRecord:
    return CarriedMedicationRecord(
        carried_medication_id=f"carried_{uuid.uuid4().hex}",
        patient_id=patient_id,
        captured_description=captured_description,
        imprint=imprint,
        packaging=packaging,
        ingredient_candidates=tuple(dict.fromkeys(ingredient_candidates or ())),
        formulary_matches=tuple(dict.fromkeys(formulary_matches or ())),
        external_record_refs=tuple(dict.fromkeys(external_record_refs or ())),
        state="CANDIDATE",
        pharmacist_actor_id=None,
        verified_ingredient=None,
        physician_actor_id=None,
        physician_decision=None,
        created_at=utcnow(),
        updated_at=utcnow(),
    )


def pharmacist_verify_carried_medication(
    *,
    record: CarriedMedicationRecord,
    pharmacist_actor_id: str,
    verified_ingredient: str | None,
    needs_more_info: bool = False,
) -> CarriedMedicationRecord:
    if record.state not in {"CANDIDATE", "UNVERIFIED", "MORE_INFO"}:
        raise ValueError("carried_medication_not_verifiable")
    if needs_more_info or not (verified_ingredient or "").strip():
        return replace(
            record,
            state="MORE_INFO",
            pharmacist_actor_id=pharmacist_actor_id,
            verified_ingredient=None,
            updated_at=utcnow(),
        )
    return replace(
        record,
        state="PHARMACIST_VERIFIED",
        pharmacist_actor_id=pharmacist_actor_id,
        verified_ingredient=verified_ingredient.strip(),
        updated_at=utcnow(),
    )


def physician_decide_carried_medication(
    *,
    record: CarriedMedicationRecord,
    physician_actor_id: str,
    decision: Literal["CONTINUE", "STOP", "SUBSTITUTE"],
) -> CarriedMedicationRecord:
    if record.state != "PHARMACIST_VERIFIED":
        raise ValueError("pharmacist_verification_required")
    return replace(
        record,
        state=decision,
        physician_actor_id=physician_actor_id,
        physician_decision=decision,
        updated_at=utcnow(),
    )


IntegrationStandard = Literal["FHIR_R4", "HL7_V2", "VENDOR_JSON"]
IntegrationAdapterType = Literal[
    "EMR", "MEDICATION", "LAB_IMAGING", "IDENTITY", "PUBLIC_DATA"
]


@dataclass(frozen=True)
class IntegrationEnvelope:
    integration_id: str
    adapter_type: IntegrationAdapterType
    standard: IntegrationStandard
    source_system: str
    external_object_id: str
    patient_id: str | None
    source_revision_id: str
    payload_hash: str
    verification_state: VerificationState
    received_at: str


def make_integration_envelope(
    *,
    adapter_type: IntegrationAdapterType,
    standard: IntegrationStandard,
    source_system: str,
    external_object_id: str,
    patient_id: str | None,
    source_revision_id: str,
    payload: dict,
    verification_state: VerificationState,
) -> IntegrationEnvelope:
    return IntegrationEnvelope(
        integration_id=f"int_{uuid.uuid4().hex}",
        adapter_type=adapter_type,
        standard=standard,
        source_system=source_system,
        external_object_id=external_object_id,
        patient_id=patient_id,
        source_revision_id=source_revision_id,
        payload_hash=stable_hash(payload),
        verification_state=verification_state,
        received_at=utcnow(),
    )


TreatmentOutcomeState = Literal[
    "EFFECTIVE", "PARTIALLY_EFFECTIVE", "INEFFECTIVE", "ADVERSE_EVENT",
    "DISCONTINUED", "UNKNOWN", "CONFLICTING_EVIDENCE",
]


@dataclass(frozen=True)
class TreatmentEpisode:
    episode_id: str
    patient_id: str
    intervention_type: str
    indication: str | None
    start_at: str | None
    end_at: str | None
    dose_intensity: str | None
    source_refs: tuple[str, ...]
    pre_state: str | None
    outcome: TreatmentOutcomeState
    adverse_event: str | None
    discontinuation_reason: str | None
    created_at: str


@dataclass(frozen=True)
class TreatmentContextReview:
    episode_id: str
    context_relevant: bool
    review_required: bool
    reason_codes: tuple[str, ...]


def make_treatment_episode(
    *,
    patient_id: str,
    intervention_type: str,
    indication: str | None,
    start_at: str | None,
    end_at: str | None,
    dose_intensity: str | None,
    source_refs: list[str],
    pre_state: str | None,
    outcome: TreatmentOutcomeState,
    adverse_event: str | None,
    discontinuation_reason: str | None,
) -> TreatmentEpisode:
    return TreatmentEpisode(
        episode_id=f"tx_{uuid.uuid4().hex}",
        patient_id=patient_id,
        intervention_type=intervention_type,
        indication=indication,
        start_at=start_at,
        end_at=end_at,
        dose_intensity=dose_intensity,
        source_refs=tuple(dict.fromkeys(source_refs)),
        pre_state=pre_state,
        outcome=outcome,
        adverse_event=adverse_event,
        discontinuation_reason=discontinuation_reason,
        created_at=utcnow(),
    )


def review_treatment_context(
    *,
    episode: TreatmentEpisode,
    context_relevant: bool,
) -> TreatmentContextReview:
    """Flag human review only; never accept/reject a proposed treatment."""
    reasons: list[str] = []
    if context_relevant and episode.outcome in {
        "INEFFECTIVE", "ADVERSE_EVENT", "DISCONTINUED", "CONFLICTING_EVIDENCE"
    }:
        reasons.append(f"PRIOR_{episode.outcome}")
    if context_relevant and episode.outcome == "UNKNOWN":
        reasons.append("PRIOR_OUTCOME_UNKNOWN")
    return TreatmentContextReview(
        episode_id=episode.episode_id,
        context_relevant=context_relevant,
        review_required=bool(reasons),
        reason_codes=tuple(reasons),
    )

def to_payload(value: object) -> dict:
    return asdict(value)
