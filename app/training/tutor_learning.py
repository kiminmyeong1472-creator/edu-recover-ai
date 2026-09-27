"""Purpose-specific learning eligibility and reward evaluation for the AI tutor.

This module does not train a model.  It turns deidentified tutoring evidence
into append-only reward and eligibility decisions that a separately controlled
training worker may consume later.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterable

from pydantic import BaseModel, Field


class EvidenceType(StrEnum):
    PRE_FEEDBACK_RESPONSE = "PRE_FEEDBACK_RESPONSE"
    HINT_USE = "HINT_USE"
    SELF_CORRECTION = "SELF_CORRECTION"
    RETRY_RESPONSE = "RETRY_RESPONSE"
    INDEPENDENT_FOLLOWUP_RESPONSE = "INDEPENDENT_FOLLOWUP_RESPONSE"
    DELAYED_FOLLOWUP_RESPONSE = "DELAYED_FOLLOWUP_RESPONSE"
    TEACHER_VALIDATION = "TEACHER_VALIDATION"
    EVIDENCE_CORRECTION = "EVIDENCE_CORRECTION"


class LearningPurpose(StrEnum):
    MASTERY_MODEL = "MASTERY_MODEL"
    ERROR_PATTERN_MODEL = "ERROR_PATTERN_MODEL"
    TUTOR_POLICY_MODEL = "TUTOR_POLICY_MODEL"
    REVIEW_TIMING_MODEL = "REVIEW_TIMING_MODEL"
    CONTENT_RECOMMENDATION_MODEL = "CONTENT_RECOMMENDATION_MODEL"


class EligibilityState(StrEnum):
    NOT_TRAINABLE = "NOT_TRAINABLE"
    HOLD = "HOLD"
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    RESTRICTED = "RESTRICTED"
    CORRECTED = "CORRECTED"


class ContentTrustState(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    DEFECT_SUSPECTED = "DEFECT_SUSPECTED"
    DEFECT_CONFIRMED = "DEFECT_CONFIRMED"


class RewardEvaluation(BaseModel):
    components: dict[str, float] = Field(default_factory=dict)
    total: float
    hard_block_reasons: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()


class PurposeEligibilityDecision(BaseModel):
    purpose: LearningPurpose
    state: EligibilityState
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()
    reward_total: float | None = None


ACTIVE_TUTOR_PURPOSES: tuple[LearningPurpose, ...] = (
    LearningPurpose.MASTERY_MODEL,
    LearningPurpose.ERROR_PATTERN_MODEL,
    LearningPurpose.TUTOR_POLICY_MODEL,
    LearningPurpose.REVIEW_TIMING_MODEL,
)


def _truthy(rows: list[dict[str, Any]], key: str) -> bool:
    return any(bool(row.get(key)) for row in rows)


def evaluate_tutor_reward(evidence: Iterable[dict[str, Any]]) -> RewardEvaluation:
    """Score educational outcomes while preserving non-compensatory blocks.

    A high numeric score cannot cancel answer leakage, defective content, or a
    missing pseudonymization secret.  Those findings remain hard blocks.
    """

    rows = list(evidence)
    refs = tuple(str(row["evidence_revision_id"]) for row in rows if row.get("evidence_revision_id"))
    types = {str(row.get("evidence_type")) for row in rows}
    hard_blocks: list[str] = []

    if _truthy(rows, "answer_disclosed"):
        hard_blocks.append("ANSWER_DISCLOSED_BEFORE_CONCEPT_RECOVERY")
    if any(row.get("content_trust") == ContentTrustState.DEFECT_CONFIRMED.value for row in rows):
        hard_blocks.append("DEFECT_CONFIRMED_CONTENT")
    if any(row.get("source_integrity_verified") is False for row in rows):
        hard_blocks.append("SOURCE_INTEGRITY_NOT_VERIFIED")
    if any(row.get("pseudonym_salt_configured") is False for row in rows):
        hard_blocks.append("PSEUDONYMIZATION_SECRET_NOT_CONFIGURED")

    independent_correct = any(
        row.get("evidence_type") in {
            EvidenceType.INDEPENDENT_FOLLOWUP_RESPONSE.value,
            EvidenceType.DELAYED_FOLLOWUP_RESPONSE.value,
        }
        and row.get("correct") is True
        for row in rows
    )
    delayed_correct = any(
        row.get("evidence_type") == EvidenceType.DELAYED_FOLLOWUP_RESPONSE.value
        and row.get("correct") is True
        and float(row.get("delayed_hours") or 0) >= 24
        for row in rows
    )
    teacher_verified_reason = any(
        row.get("evidence_type") == EvidenceType.TEACHER_VALIDATION.value
        and row.get("teacher_validated") is True
        and row.get("reason_present") is True
        for row in rows
    )
    initial_incorrect = any(
        row.get("evidence_type") == EvidenceType.PRE_FEEDBACK_RESPONSE.value
        and row.get("correct") is False
        for row in rows
    )
    hint_used = EvidenceType.HINT_USE.value in types or any(int(row.get("hint_level") or 0) > 0 for row in rows)

    components = {
        "verified_self_explanation": 25.0 if teacher_verified_reason else 0.0,
        "unaided_same_concept_transfer": 35.0 if independent_correct else 0.0,
        "delayed_transfer": 20.0 if delayed_correct else 0.0,
        "misconception_resolution": 20.0 if initial_incorrect and independent_correct else 0.0,
        "hint_dependence_penalty": -15.0 if hint_used and not independent_correct else 0.0,
    }
    return RewardEvaluation(
        components=components,
        total=sum(components.values()),
        hard_block_reasons=tuple(sorted(set(hard_blocks))),
        evidence_refs=refs,
    )


def evaluate_purpose_eligibility(
    purpose: LearningPurpose,
    evidence: Iterable[dict[str, Any]],
    reward: RewardEvaluation,
) -> PurposeEligibilityDecision:
    """Evaluate one learning purpose without reusing evidence across purposes."""

    rows = list(evidence)
    refs = tuple(str(row["evidence_revision_id"]) for row in rows if row.get("evidence_revision_id"))
    types = {str(row.get("evidence_type")) for row in rows}

    if purpose is LearningPurpose.CONTENT_RECOMMENDATION_MODEL:
        return PurposeEligibilityDecision(
            purpose=purpose,
            state=EligibilityState.RESTRICTED,
            reasons=("Tutoring responses cannot feed content recommendation, item generation, or distractor optimization.",),
            evidence_refs=refs,
            reward_total=reward.total,
        )

    if reward.hard_block_reasons:
        state = (
            EligibilityState.INELIGIBLE
            if "DEFECT_CONFIRMED_CONTENT" in reward.hard_block_reasons
            or "ANSWER_DISCLOSED_BEFORE_CONCEPT_RECOVERY" in reward.hard_block_reasons
            else EligibilityState.HOLD
        )
        return PurposeEligibilityDecision(
            purpose=purpose,
            state=state,
            reasons=reward.hard_block_reasons,
            evidence_refs=refs,
            reward_total=reward.total,
        )

    teacher_validated = any(
        row.get("evidence_type") == EvidenceType.TEACHER_VALIDATION.value
        and row.get("teacher_validated") is True
        for row in rows
    )
    initial_present = EvidenceType.PRE_FEEDBACK_RESPONSE.value in types
    independent_correct = any(
        row.get("evidence_type") in {
            EvidenceType.INDEPENDENT_FOLLOWUP_RESPONSE.value,
            EvidenceType.DELAYED_FOLLOWUP_RESPONSE.value,
        }
        and row.get("correct") is True
        for row in rows
    )
    delayed_correct = any(
        row.get("evidence_type") == EvidenceType.DELAYED_FOLLOWUP_RESPONSE.value
        and row.get("correct") is True
        and float(row.get("delayed_hours") or 0) >= 24
        for row in rows
    )
    tutor_action_present = EvidenceType.HINT_USE.value in types or any(int(row.get("hint_level") or 0) > 0 for row in rows)

    requirements: dict[LearningPurpose, list[tuple[bool, str]]] = {
        LearningPurpose.MASTERY_MODEL: [
            (initial_present, "피드백 전 최초응답이 필요합니다."),
            (independent_correct, "같은 목표개념의 독립 후속문항 성공이 필요합니다."),
            (teacher_validated, "교사 검증 revision이 필요합니다."),
        ],
        LearningPurpose.ERROR_PATTERN_MODEL: [
            (initial_present, "피드백 전 최초응답이 필요합니다."),
            (teacher_validated, "오답근거에 대한 교사 검증 revision이 필요합니다."),
        ],
        LearningPurpose.TUTOR_POLICY_MODEL: [
            (tutor_action_present, "실제 힌트·설명 제공 증거가 필요합니다."),
            (independent_correct, "힌트와 독립된 후속문항 성공이 필요합니다."),
            (teacher_validated, "교사 검증 revision이 필요합니다."),
        ],
        LearningPurpose.REVIEW_TIMING_MODEL: [
            (delayed_correct, "24시간 이후 지연 후속검사 성공이 필요합니다."),
            (teacher_validated, "교사 검증 revision이 필요합니다."),
        ],
    }
    missing = [message for passed, message in requirements[purpose] if not passed]
    if missing:
        return PurposeEligibilityDecision(
            purpose=purpose,
            state=EligibilityState.HOLD,
            reasons=tuple(missing),
            evidence_refs=refs,
            reward_total=reward.total,
        )
    return PurposeEligibilityDecision(
        purpose=purpose,
        state=EligibilityState.ELIGIBLE,
        reasons=("해당 학습목적의 실제 노출·결과·검증 게이트를 모두 통과했습니다.",),
        evidence_refs=refs,
        reward_total=reward.total,
    )
