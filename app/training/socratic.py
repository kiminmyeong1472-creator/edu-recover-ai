"""Structured Socratic tutoring trajectory schema.

This module encodes the owner's concept-first tutoring philosophy and keeps it
separate from assessment-item optimization. A tutoring trajectory may diagnose
and teach one precommitted target concept through changing probes, but it must
not silently become a distractor-attractiveness or item-generation feedback
loop.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class DiagnosticStage(StrEnum):
    INITIAL_RESPONSE = "INITIAL_RESPONSE"
    REASON_PROBE = "REASON_PROBE"
    HYPOTHESIS_SEPARATION = "HYPOTHESIS_SEPARATION"
    TRAP_PROBE = "TRAP_PROBE"
    BOUNDARY_PROBE = "BOUNDARY_PROBE"
    COUNTEREXAMPLE_PROBE = "COUNTEREXAMPLE_PROBE"
    HINT = "HINT"
    SELF_CORRECTION = "SELF_CORRECTION"
    TRANSFER_CHECK = "TRANSFER_CHECK"
    DELAYED_REVIEW = "DELAYED_REVIEW"


class AnswerPolicy(StrEnum):
    WITHHOLD_UNTIL_CONCEPT_RECOVERY = "WITHHOLD_UNTIL_CONCEPT_RECOVERY"


class MisconceptionHypothesis(BaseModel):
    hypothesis_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    supporting_evidence_refs: tuple[str, ...] = ()
    contradicting_evidence_refs: tuple[str, ...] = ()


class ConceptTargetPlan(BaseModel):
    """Precommitted concept and probe plan.

    Trap/boundary questions are tied to misconception hypotheses of the SAME
    target concept. They are not selected to maximize distractor attractiveness.
    """

    target_concept_id: str = Field(min_length=1)
    target_concept_revision: str = Field(min_length=1)
    learning_goal: str = Field(min_length=1)
    misconception_hypothesis_ids: tuple[str, ...] = ()
    predesigned_trap_probe_ids: tuple[str, ...] = ()
    predesigned_boundary_probe_ids: tuple[str, ...] = ()
    transfer_dimensions: tuple[str, ...] = (
        "surface_expression",
        "condition",
        "context",
    )
    answer_policy: AnswerPolicy = (
        AnswerPolicy.WITHHOLD_UNTIL_CONCEPT_RECOVERY
    )
    question_plan_hash: str = Field(min_length=1)


class TutoringTurn(BaseModel):
    turn_index: int = Field(ge=0)
    stage: DiagnosticStage
    tutor_action_code: str = Field(min_length=1)
    target_concept_id: str = Field(min_length=1)
    probe_id: str | None = None
    variation_dimension: str | None = None
    learner_response_hash: str | None = None
    evidence_refs: tuple[str, ...] = ()
    answer_revealed: bool = False
    hint_level: int = Field(default=0, ge=0, le=10)


class SocraticTrajectory(BaseModel):
    """Deidentified representation of one concept-bound tutoring session."""

    trajectory_id: str = Field(min_length=1)
    educational_object_revision: str = Field(min_length=1)
    educational_object_hash: str = Field(min_length=1)
    subject_code: str = Field(min_length=1)
    difficulty_band: str = Field(min_length=1)
    concept_plan: ConceptTargetPlan
    learner_initial_response_hash: str = Field(min_length=1)
    learner_reason_hash: str | None = None
    learner_confidence_band: str | None = None
    hypotheses: tuple[MisconceptionHypothesis, ...] = ()
    turns: tuple[TutoringTurn, ...]
    concept_recovery_passed: bool = False
    transfer_check_passed: bool | None = None
    delayed_review_passed: bool | None = None

    # Hard separation from the assessment-authoring feedback loop.
    used_for_item_generation: bool = False
    used_for_distractor_optimization: bool = False
    used_for_choice_rate_ranking: bool = False

    def assert_owner_tutoring_policy(self) -> None:
        """Enforce concept-first, no-answer-leak and non-ADG boundaries."""

        target = self.concept_plan.target_concept_id

        if self.used_for_item_generation:
            raise ValueError(
                "Socratic tutoring responses cannot feed assessment-item "
                "generation through this trajectory."
            )
        if self.used_for_distractor_optimization:
            raise ValueError(
                "Socratic tutoring responses cannot optimize distractor "
                "attractiveness through this trajectory."
            )
        if self.used_for_choice_rate_ranking:
            raise ValueError(
                "Socratic tutoring responses cannot be converted into "
                "choice-rate ranking signals through this trajectory."
            )

        for turn in self.turns:
            if turn.target_concept_id != target:
                raise ValueError(
                    "All probes and transfer checks must remain bound to the "
                    "precommitted target concept."
                )

            if (
                turn.answer_revealed
                and not self.concept_recovery_passed
            ):
                raise ValueError(
                    "The answer was revealed before concept recovery was "
                    "established."
                )

    def assert_transfer_is_same_concept(self) -> None:
        target = self.concept_plan.target_concept_id
        for turn in self.turns:
            if (
                turn.stage in {
                    DiagnosticStage.TRANSFER_CHECK,
                    DiagnosticStage.DELAYED_REVIEW,
                }
                and turn.target_concept_id != target
            ):
                raise ValueError(
                    "Transfer/delayed checks must test the same target concept."
                )
