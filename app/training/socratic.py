"""Structured Socratic tutoring trajectory schema."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class DiagnosticStage(StrEnum):
    INITIAL_RESPONSE = "INITIAL_RESPONSE"
    REASON_PROBE = "REASON_PROBE"
    HYPOTHESIS_SEPARATION = "HYPOTHESIS_SEPARATION"
    HINT = "HINT"
    SELF_CORRECTION = "SELF_CORRECTION"
    TRANSFER_CHECK = "TRANSFER_CHECK"
    DELAYED_REVIEW = "DELAYED_REVIEW"


class MisconceptionHypothesis(BaseModel):
    hypothesis_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    supporting_evidence_refs: tuple[str, ...] = ()
    contradicting_evidence_refs: tuple[str, ...] = ()


class TutoringTurn(BaseModel):
    turn_index: int = Field(ge=0)
    stage: DiagnosticStage
    tutor_action_code: str = Field(min_length=1)
    learner_response_hash: str | None = None
    evidence_refs: tuple[str, ...] = ()
    answer_revealed: bool = False
    hint_level: int = Field(default=0, ge=0, le=10)


class SocraticTrajectory(BaseModel):
    """Deidentified representation of a tutoring session.

    Raw learner text/audio is intentionally not required here. The training
    plane can bind to separately governed source material by hashes/refs.
    """

    trajectory_id: str = Field(min_length=1)
    educational_object_revision: str = Field(min_length=1)
    educational_object_hash: str = Field(min_length=1)
    subject_code: str = Field(min_length=1)
    difficulty_band: str = Field(min_length=1)
    learner_initial_response_hash: str = Field(min_length=1)
    learner_reason_hash: str | None = None
    learner_confidence_band: str | None = None
    hypotheses: tuple[MisconceptionHypothesis, ...] = ()
    turns: tuple[TutoringTurn, ...]
    concept_recovery_passed: bool = False
    transfer_check_passed: bool | None = None
    delayed_review_passed: bool | None = None

    def assert_no_early_answer_reveal(self) -> None:
        """Fail if the answer was exposed before self-correction/verification."""

        for turn in self.turns:
            if (
                turn.answer_revealed
                and turn.stage
                in {
                    DiagnosticStage.INITIAL_RESPONSE,
                    DiagnosticStage.REASON_PROBE,
                    DiagnosticStage.HYPOTHESIS_SEPARATION,
                    DiagnosticStage.HINT,
                }
            ):
                raise ValueError(
                    "Answer was revealed before diagnostic/self-correction "
                    "stages were completed."
                )
