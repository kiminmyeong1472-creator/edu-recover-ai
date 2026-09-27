"""Provider-neutral training plan with gpt-oss as an initial candidate.

This module does not download weights or start GPU work. It produces a
reproducible manifest that a separately provisioned training worker can execute.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.training.models import BaseModelSpec, TrainingPurpose


class TrainingRecipe(BaseModel):
    recipe_id: str = Field(min_length=1)
    purposes: tuple[TrainingPurpose, ...]
    method: str = Field(
        description="Examples: LoRA, QLoRA, full-parameter SFT, preference."
    )
    precision: str = Field(min_length=1)
    max_sequence_length: int = Field(gt=0)
    epochs: float = Field(gt=0)
    learning_rate: float = Field(gt=0)
    notes: tuple[str, ...] = ()


class TrainingExecutionPlan(BaseModel):
    base_model: BaseModelSpec
    dataset_snapshot_hash: str = Field(min_length=64, max_length=64)
    recipe: TrainingRecipe
    output_namespace: str = Field(min_length=1)


def default_gpt_oss_20b_spec() -> BaseModelSpec:
    """Initial candidate specification; replaceable by policy."""

    return BaseModelSpec(
        model_id="openai/gpt-oss-20b",
        family="gpt-oss",
        open_weight=True,
        license_id="Apache-2.0",
        fine_tune_allowed=True,
        commercial_use_allowed=True,
        runtime="transformers-or-compatible",
    )


def build_training_plan(
    *,
    dataset_snapshot_hash: str,
    recipe: TrainingRecipe,
    base_model: BaseModelSpec | None = None,
    output_namespace: str = "edurecover/tutor-candidate",
) -> TrainingExecutionPlan:
    selected = base_model or default_gpt_oss_20b_spec()

    if not selected.open_weight:
        raise ValueError("Owned training requires an open-weight base model.")
    if not selected.fine_tune_allowed:
        raise ValueError("Base-model terms do not permit fine-tuning.")
    if not selected.commercial_use_allowed:
        raise ValueError(
            "Base-model terms do not permit intended commercial use."
        )

    return TrainingExecutionPlan(
        base_model=selected,
        dataset_snapshot_hash=dataset_snapshot_hash,
        recipe=recipe,
        output_namespace=output_namespace,
    )
