"""IP/FTO hold gate for first-party tutoring model training.

Legal status is intentionally not inferred from feature similarity. Training
from real tutoring trajectories stays on HOLD until a reviewed evidence packet
identifies the intended use and confirms the applicable boundary.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class IPReviewState(StrEnum):
    HOLD = "HOLD"
    REVIEWED_LIMITED_USE = "REVIEWED_LIMITED_USE"
    CLEARED_FOR_TUTOR_MODEL_TRAINING = "CLEARED_FOR_TUTOR_MODEL_TRAINING"


class TutoringTrainingUse(StrEnum):
    RUNTIME_DIAGNOSIS = "RUNTIME_DIAGNOSIS"
    SAME_CONCEPT_TRANSFER_EVAL = "SAME_CONCEPT_TRANSFER_EVAL"
    TUTOR_MODEL_TRAINING = "TUTOR_MODEL_TRAINING"
    ASSESSMENT_ITEM_GENERATION = "ASSESSMENT_ITEM_GENERATION"
    DISTRACTOR_ATTRACTIVENESS = "DISTRACTOR_ATTRACTIVENESS"
    CHOICE_RATE_DPO = "CHOICE_RATE_DPO"


_ALWAYS_BLOCKED = {
    TutoringTrainingUse.ASSESSMENT_ITEM_GENERATION,
    TutoringTrainingUse.DISTRACTOR_ATTRACTIVENESS,
    TutoringTrainingUse.CHOICE_RATE_DPO,
}


class TutoringIPReview(BaseModel):
    review_id: str = Field(min_length=1)
    state: IPReviewState = IPReviewState.HOLD
    reviewed_sources: tuple[str, ...] = ()
    reviewed_claim_sets: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def assert_tutoring_use_allowed(
    *,
    intended_use: TutoringTrainingUse,
    review: TutoringIPReview,
) -> None:
    if intended_use in _ALWAYS_BLOCKED:
        raise ValueError(
            f"{intended_use.value} is outside the Socratic tutoring data "
            "boundary and remains blocked."
        )

    if intended_use in {
        TutoringTrainingUse.RUNTIME_DIAGNOSIS,
        TutoringTrainingUse.SAME_CONCEPT_TRANSFER_EVAL,
    }:
        if review.state is IPReviewState.HOLD:
            raise ValueError(
                "Tutoring IP/FTO review is still on HOLD."
            )
        return

    if intended_use is TutoringTrainingUse.TUTOR_MODEL_TRAINING:
        if (
            review.state
            is not IPReviewState.CLEARED_FOR_TUTOR_MODEL_TRAINING
        ):
            raise ValueError(
                "Tutor-model training requires explicit IP/FTO clearance."
            )
        return

    raise ValueError("Unsupported tutoring data use.")
