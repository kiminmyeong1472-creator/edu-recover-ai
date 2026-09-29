from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SignalKind = Literal[
    "DUPLICATE_INGREDIENT",
    "DUPLICATE_THERAPEUTIC_CLASS",
    "CONTRAINDICATED_COMBINATION",
    "AGE_CAUTION",
    "PREGNANCY_CAUTION",
    "DOSE_CAUTION",
    "DURATION_CAUTION",
    "ELDERLY_CAUTION",
    "ALLERGY_OR_ADVERSE_REACTION",
    "OTHER",
]

SignalSeverity = Literal["INFO", "REVIEW", "URGENT_REVIEW"]


@dataclass(frozen=True)
class MedicationSafetySignal:
    signal_id: str
    subject_id: str
    medication_refs: tuple[str, ...]
    kind: SignalKind
    severity: SignalSeverity
    source_ref: str
    explanation: str
    clinician_or_pharmacist_review_required: bool = True


def user_message(signal: MedicationSafetySignal) -> str:
    """Return a neutral review message, never a diagnosis or stop-medication command."""
    return (
        f"{signal.explanation} "
        "의학적으로 적절한 사유가 있을 수 있으므로 임의로 약을 중단하지 말고 "
        "의사 또는 약사에게 확인하세요."
    )
