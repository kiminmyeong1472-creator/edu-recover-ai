"""Fail-closed training-data eligibility gate."""

from __future__ import annotations

from app.training.models import (
    ConsentState,
    EligibilityDecision,
    PatentClearanceState,
    RightsState,
    SourceClass,
    TrainingDataStatus,
    TrainingPurpose,
    TrainingUseScope,
    TrajectoryEnvelope,
)

_BLOCKED_SOURCES = {
    SourceClass.THIRD_PARTY_API_OUTPUT,
    SourceClass.CHATGPT_APP_REQUEST,
}

_TUTORING_RESPONSE_BLOCKED_SCOPES = {
    TrainingUseScope.ITEM_GENERATOR,
    TrainingUseScope.DISTRACTOR_RANKER,
}


def evaluate_training_eligibility(
    item: TrajectoryEnvelope,
) -> EligibilityDecision:
    """Decide whether a trajectory may enter an owner-controlled dataset.

    The default is fail-closed. In addition to rights/privacy/provenance,
    first-party tutoring trajectories require explicit patent/FTO clearance.
    Under the current design boundary, student-response tutoring data is not
    admitted into item-generation or distractor-attractiveness training.
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

    if item.source_class is SourceClass.FIRST_PARTY_TUTORING:
        blocked_scopes = sorted(
            scope.value
            for scope in item.requested_use_scopes
            if scope in _TUTORING_RESPONSE_BLOCKED_SCOPES
        )
        if blocked_scopes:
            return EligibilityDecision(
                status=TrainingDataStatus.REJECTED,
                reasons=(
                    "Student-response tutoring data is isolated from "
                    "question-generation/distractor-attractiveness training "
                    "under the current patent-boundary policy: "
                    + ", ".join(blocked_scopes),
                ),
            )

        if item.patent_clearance is not PatentClearanceState.CLEARED:
            reasons.append(
                "First-party tutoring data is on patent/FTO HOLD until "
                "the Socratic tutoring boundary is explicitly cleared."
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
            allowed_use_scopes=(),
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
        allowed_use_scopes=item.requested_use_scopes,
    )
