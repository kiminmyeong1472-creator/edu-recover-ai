from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


VerificationState = Literal["OFFICIAL", "DOCUMENT", "USER_CONFIRMED", "UNCONFIRMED"]


@dataclass
class MedicationRecord:
    medication_id: str
    subject_id: str
    product_name: str
    ingredient_name: str | None = None
    dose_text: str | None = None
    prescribing_org: str | None = None
    department: str | None = None
    prescribed_at: str | None = None
    approved_indications: list[str] = field(default_factory=list)
    actual_treatment_purpose: str | None = None
    user_reported_purpose: str | None = None
    verification_state: VerificationState = "UNCONFIRMED"
