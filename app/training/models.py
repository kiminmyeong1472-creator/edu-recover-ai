"""Domain models for EduRecover's owner-controlled training plane."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TrainingDataStatus(StrEnum):
    NOT_TRAINABLE = "NOT_TRAINABLE"
    QUARANTINED = "QUARANTINED"
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"


class SourceClass(StrEnum):
    FIRST_PARTY_TUTORING = "FIRST_PARTY_TUTORING"
    OWNER_AUTHORED = "OWNER_AUTHORED"
    LICENSED_CORPUS = "LICENSED_CORPUS"
    OPEN_WEIGHT_SELF_GENERATED = "OPEN_WEIGHT_SELF_GENERATED"
    THIRD_PARTY_API_OUTPUT = "THIRD_PARTY_API_OUTPUT"
    CHATGPT_APP_REQUEST = "CHATGPT_APP_REQUEST"
    UNKNOWN = "UNKNOWN"


class RightsState(StrEnum):
    CONFIRMED = "CONFIRMED"
    RESTRICTED = "RESTRICTED"
    UNKNOWN = "UNKNOWN"


class ConsentState(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    CONFIRMED = "CONFIRMED"
    MISSING = "MISSING"
    REVOKED = "REVOKED"


class PatentClearanceState(StrEnum):
    NOT_REVIEWED = "NOT_REVIEWED"
    HOLD = "HOLD"
    CLEARED = "CLEARED"


class TrainingUseScope(StrEnum):
    """Where a record may be used.

    Student-response tutoring data must never silently flow into question
    synthesis or distractor-attractiveness training.
    """

    TUTOR_POLICY = "TUTOR_POLICY"
    STUDENT_MODEL = "STUDENT_MODEL"
    ITEM_GENERATOR = "ITEM_GENERATOR"
    DISTRACTOR_RANKER = "DISTRACTOR_RANKER"
    EVAL_ONLY = "EVAL_ONLY"


class TrainingPurpose(StrEnum):
    SFT = "SFT"
    PREFERENCE = "PREFERENCE"
    REWARD = "REWARD"
    EVAL_ONLY = "EVAL_ONLY"


class ModelLifecycleState(StrEnum):
    DRAFT = "DRAFT"
    DATASET_LOCKED = "DATASET_LOCKED"
    TRAINING = "TRAINING"
    TRAINED = "TRAINED"
    EVALUATING = "EVALUATING"
    HOLD = "HOLD"
    PROMOTION_READY = "PROMOTION_READY"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


class TrajectoryEnvelope(BaseModel):
    """Metadata envelope for a tutoring or authoring trajectory.

    Raw personal content should stay behind a separate access boundary. This
    object carries references and integrity metadata, not secret/raw content.
    """

    trajectory_id: str = Field(min_length=1)
    source_class: SourceClass
    rights_state: RightsState = RightsState.UNKNOWN
    consent_state: ConsentState = ConsentState.NOT_REQUIRED
    patent_clearance: PatentClearanceState = PatentClearanceState.NOT_REVIEWED
    requested_use_scopes: tuple[TrainingUseScope, ...] = (
        TrainingUseScope.EVAL_ONLY,
    )
    contains_personal_data: bool = False
    deidentified: bool = False
    learner_pseudonymous_id: str | None = None
    session_hash: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    purpose_code: str = Field(min_length=1)
    provenance_complete: bool = False
    source_integrity_verified: bool = False
    evidence_refs: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class EligibilityDecision(BaseModel):
    status: TrainingDataStatus
    reasons: tuple[str, ...]
    allowed_purposes: tuple[TrainingPurpose, ...] = ()
    allowed_use_scopes: tuple[TrainingUseScope, ...] = ()


class BaseModelSpec(BaseModel):
    """A replaceable base-model description.

    Core training logic must not depend on a vendor or model family.
    """

    model_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    open_weight: bool
    license_id: str = Field(min_length=1)
    fine_tune_allowed: bool
    commercial_use_allowed: bool
    artifact_hash: str | None = None
    runtime: str | None = None


class TrainingRunManifest(BaseModel):
    run_id: str = Field(min_length=1)
    base_model: BaseModelSpec
    dataset_snapshot_hash: str = Field(min_length=1)
    recipe_hash: str = Field(min_length=1)
    lifecycle_state: ModelLifecycleState = ModelLifecycleState.DRAFT
    output_checkpoint_hash: str | None = None


class PromotionEvidence(BaseModel):
    candidate_checkpoint_hash: str = Field(min_length=1)
    baseline_checkpoint_hash: str = Field(min_length=1)
    dataset_snapshot_hash: str = Field(min_length=1)
    eval_snapshot_hash: str = Field(min_length=1)
    required_gates: dict[str, bool] = Field(default_factory=dict)
    unresolved_findings: tuple[str, ...] = ()


class PromotionDecision(BaseModel):
    state: ModelLifecycleState
    reasons: tuple[str, ...]
