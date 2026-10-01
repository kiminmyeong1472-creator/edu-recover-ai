from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.domains.medical.models import MedicationRecord


MedicationState = Literal["CURRENT", "STOPPED", "COMPLETED", "UNKNOWN"]


@dataclass(frozen=True)
class MedicationPassportItem:
    medication_id: str
    product_name: str
    ingredient_name: str | None
    prescribing_org: str | None
    department: str | None
    prescribed_at: str | None
    actual_treatment_purpose: str | None
    verification_state: str
    medication_state: MedicationState
    safety_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MedicationPassport:
    subject_id: str
    items: tuple[MedicationPassportItem, ...]
    allergies: tuple[str, ...] = ()
    adverse_reactions: tuple[str, ...] = ()
    confirmation_needed: tuple[str, ...] = ()


def build_medication_passport(
    *,
    subject_id: str,
    records: list[MedicationRecord],
    states: dict[str, MedicationState] | None = None,
    safety_notes: dict[str, list[str]] | None = None,
    allergies: list[str] | None = None,
    adverse_reactions: list[str] | None = None,
) -> MedicationPassport:
    """Create a patient-facing projection without making prescribing judgments."""
    states = states or {}
    safety_notes = safety_notes or {}
    items: list[MedicationPassportItem] = []
    needs_confirmation: list[str] = []

    for record in records:
        if record.subject_id != subject_id:
            raise PermissionError("subject_scope_mismatch")

        if record.verification_state == "UNCONFIRMED" or not record.actual_treatment_purpose:
            needs_confirmation.append(record.medication_id)

        items.append(
            MedicationPassportItem(
                medication_id=record.medication_id,
                product_name=record.product_name,
                ingredient_name=record.ingredient_name,
                prescribing_org=record.prescribing_org,
                department=record.department,
                prescribed_at=record.prescribed_at,
                actual_treatment_purpose=record.actual_treatment_purpose,
                verification_state=record.verification_state,
                medication_state=states.get(record.medication_id, "UNKNOWN"),
                safety_notes=tuple(safety_notes.get(record.medication_id, [])),
            )
        )

    return MedicationPassport(
        subject_id=subject_id,
        items=tuple(items),
        allergies=tuple(allergies or []),
        adverse_reactions=tuple(adverse_reactions or []),
        confirmation_needed=tuple(needs_confirmation),
    )
