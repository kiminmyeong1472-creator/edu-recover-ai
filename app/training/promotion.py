"""Fail-closed model-promotion gate."""

from __future__ import annotations

from app.training.models import (
    ModelLifecycleState,
    PromotionDecision,
    PromotionEvidence,
)


def evaluate_model_promotion(
    evidence: PromotionEvidence,
) -> PromotionDecision:
    """Permit promotion only when every required gate passes.

    Threshold values and scoring formulas belong in owner-controlled policy,
    not in this module. This function enforces the non-compensatory rule.
    """

    reasons: list[str] = []

    if evidence.candidate_checkpoint_hash == evidence.baseline_checkpoint_hash:
        reasons.append("Candidate checkpoint is identical to baseline.")

    failed_gates = sorted(
        name for name, passed in evidence.required_gates.items() if not passed
    )
    if failed_gates:
        reasons.append(
            "Required gates failed: " + ", ".join(failed_gates)
        )

    if not evidence.required_gates:
        reasons.append("No required evaluation gates were supplied.")

    if evidence.unresolved_findings:
        reasons.append(
            "Unresolved negative findings remain: "
            + ", ".join(evidence.unresolved_findings)
        )

    if reasons:
        return PromotionDecision(
            state=ModelLifecycleState.HOLD,
            reasons=tuple(reasons),
        )

    return PromotionDecision(
        state=ModelLifecycleState.PROMOTION_READY,
        reasons=("All mandatory promotion gates passed.",),
    )
