from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PurposeSource = Literal["OFFICIAL_LABEL", "CLINICAL_DOCUMENT", "USER_REPORT", "CLINICIAN_CONFIRMED"]


@dataclass(frozen=True)
class TreatmentPurposeEvidence:
    source: PurposeSource
    value: str
    source_ref: str | None = None


@dataclass(frozen=True)
class TreatmentPurposeAssessment:
    display_purpose: str | None
    status: Literal["CONFIRMED", "USER_REPORTED", "DOCUMENTED", "NEEDS_CONFIRMATION"]
    reason: str


def assess_treatment_purpose(
    evidence: list[TreatmentPurposeEvidence],
) -> TreatmentPurposeAssessment:
    """Select the strongest supported treatment purpose without inferring one.

    An official drug indication is not treated as proof of why a particular
    patient received the medicine.
    """
    clinician = next((x for x in evidence if x.source == "CLINICIAN_CONFIRMED"), None)
    if clinician:
        return TreatmentPurposeAssessment(
            display_purpose=clinician.value,
            status="CONFIRMED",
            reason="clinician_confirmed",
        )

    documented = next((x for x in evidence if x.source == "CLINICAL_DOCUMENT"), None)
    if documented:
        return TreatmentPurposeAssessment(
            display_purpose=documented.value,
            status="DOCUMENTED",
            reason="clinical_document_present",
        )

    user = next((x for x in evidence if x.source == "USER_REPORT"), None)
    if user:
        return TreatmentPurposeAssessment(
            display_purpose=user.value,
            status="USER_REPORTED",
            reason="user_report_only",
        )

    return TreatmentPurposeAssessment(
        display_purpose=None,
        status="NEEDS_CONFIRMATION",
        reason="no_patient_specific_treatment_purpose_evidence",
    )
