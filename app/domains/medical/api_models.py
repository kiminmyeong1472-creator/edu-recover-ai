from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class MedicationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=1)
    product_name: str = Field(min_length=1)
    ingredient_name: str | None = None
    dose_text: str | None = None
    prescribing_org: str | None = None
    department: str | None = None
    prescribed_at: str | None = None
    approved_indications: list[str] = Field(default_factory=list)
    actual_treatment_purpose: str | None = None
    user_reported_purpose: str | None = None


class TreatmentPurposeConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=1)
    medication_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    source: Literal["CLINICAL_DOCUMENT", "USER_REPORT", "CLINICIAN_CONFIRMED"]
    source_ref: str | None = None


class MedicationPassportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str
    medication_ids: list[str]
    confirmation_needed: list[str]
    generated_from_verified_records_only: bool = False
