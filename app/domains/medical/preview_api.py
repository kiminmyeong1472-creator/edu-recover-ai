from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header

from app.domains.medical.api_models import MedicationCreateRequest
from app.domains.medical.medication_passport import build_medication_passport
from app.domains.medical.models import MedicationRecord
from app.platform_core.domain_scope import DomainScope
from app.platform_core.request_guards import domain_guard, require_subject_header


router = APIRouter(prefix="/preview/medical", tags=["preview-medical"])
_records: dict[str, MedicationRecord] = {}
_medical_guard = domain_guard(DomainScope.MEDICAL)


@router.post("/medications")
def create_medication(
    payload: MedicationCreateRequest,
    x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
    _: DomainScope = Depends(_medical_guard),
) -> dict[str, str]:
    require_subject_header(payload.subject_id, x_subject_id)
    medication_id = f"med_{uuid.uuid4().hex}"
    verification_state = (
        "USER_CONFIRMED"
        if payload.actual_treatment_purpose
        else "UNCONFIRMED"
    )
    _records[medication_id] = MedicationRecord(
        medication_id=medication_id,
        subject_id=payload.subject_id,
        product_name=payload.product_name,
        ingredient_name=payload.ingredient_name,
        dose_text=payload.dose_text,
        prescribing_org=payload.prescribing_org,
        department=payload.department,
        prescribed_at=payload.prescribed_at,
        approved_indications=payload.approved_indications,
        actual_treatment_purpose=payload.actual_treatment_purpose,
        user_reported_purpose=payload.user_reported_purpose,
        verification_state=verification_state,
    )
    return {"medication_id": medication_id, "verification_state": verification_state}


@router.get("/passport/{subject_id}")
def medication_passport(
    subject_id: str,
    x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
    _: DomainScope = Depends(_medical_guard),
) -> dict:
    require_subject_header(subject_id, x_subject_id)
    records = [record for record in _records.values() if record.subject_id == subject_id]
    passport = build_medication_passport(subject_id=subject_id, records=records)
    return {
        "subject_id": passport.subject_id,
        "items": [
            {
                "medication_id": item.medication_id,
                "product_name": item.product_name,
                "ingredient_name": item.ingredient_name,
                "prescribing_org": item.prescribing_org,
                "department": item.department,
                "actual_treatment_purpose": item.actual_treatment_purpose,
                "verification_state": item.verification_state,
                "medication_state": item.medication_state,
            }
            for item in passport.items
        ],
        "confirmation_needed": list(passport.confirmation_needed),
    }


@router.delete("/_test/reset")
def reset_preview_store() -> dict[str, str]:
    _records.clear()
    return {"status": "reset"}
