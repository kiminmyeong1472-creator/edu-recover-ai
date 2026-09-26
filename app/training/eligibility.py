"""Fail-closed training-data eligibility gate."""

from __future__ import annotations

from app.training.models import (
    ConsentState,
    EligibilityDecision,
    RightsState,
    SourceClass,
    TrainingDataStatus,
    TrainingPurpose,
    TrajectoryEnvelope,
)

_BLOCKED_SOURCES = {
    SourceClass.THIRD_PARTY_API_OUTPUT,
    SourceClass.CHATGPT_APP_REQUEST,
}


def evaluate_training_eligibility(
    item: TrajectoryEnvelope,
) -> EligibilityDecision:
    """Decide whether a trajectory may enter an owner-controlled dataset.

    The default is fail-closed. External managed-AI outputs are blocked from
    model training unless a future, separately reviewed policy explicitly
    changes that contract boundary.
    """

    reasons: list[str] = []

    if item.source_class in _BLOCKED_SOURCES:
        return EligibilityDecision(
            status=TrainingDataStatus.REJECTED,
            reasons=(
                "Managed third-party AI/App output is outside the default "
                "EduRecover training-rights boundary.",
            ),
        )

    if item.source_class is SourceClass.UNKNOWN:
        reasons.append("Source class is unknown.")

    if item.rights_state is RightsState.RESTRICTED:
        return EligibilityDecision(
            status=TrainingDataStatus.REJECTED,
            reasons=("Rights state does not permit training.",),
        )

    if item.rights_state is not RightsState.CONFIRMED:
        reasons.append("Training rights have not been confirmed.")

    if not item.provenance_complete:
        reasons.append("Provenance is incomplete.")

    if not item.source_integrity_verified:
        reasons.append("Source integrity is not verified.")

    if item.contains_personal_data:
        if item.consent_state is not ConsentState.CONFIRMED:
            reasons.append("Required consent is not confirmed.")
        if not item.deidentified:
            reasons.append("Personal tutoring data is not deidentified.")

    if reasons:
        return EligibilityDecision(
            status=TrainingDataStatus.QUARANTINED,
            reasons=tuple(reasons),
            allowed_purposes=(),
        )

    return EligibilityDecision(
        status=TrainingDataStatus.ELIGIBLE,
        reasons=("All mandatory training-data gates passed.",),
        allowed_purposes=(
            TrainingPurpose.SFT,
            TrainingPurpose.PREFERENCE,
            TrainingPurpose.REWARD,
            TrainingPurpose.EVAL_ONLY,
        ),
    )
