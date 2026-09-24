"""Vaccine Graph append-only event sourcing models.

This module models non-destructive recovery as an immutable, hash-linked event
ledger. Existing events are never updated or deleted; every correction is
represented as a new append event.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GENESIS_HASH = "GENESIS"


class AppendOnlyViolation(RuntimeError):
    """Raised when code attempts to mutate or delete historical events."""


class AIWorkerRecord(BaseModel):
    """Audit record for an AI worker involved in the mutation decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    worker_name: str = Field(min_length=1)
    verdict: str = Field(min_length=1)
    record: str = Field(min_length=1)


class MutationReason(BaseModel):
    """Human-readable reason plus the AI-worker audit trail."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(min_length=1)
    ai_worker_records: tuple[AIWorkerRecord, ...] = ()


class VaccineGraphEvent(BaseModel):
    """Single immutable state-transition event in the Vaccine Graph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    target_problem_id: int = Field(
        ge=1,
        le=28,
        description="Target item number within the 28-problem batch.",
    )
    previous_state_hash: str = Field(
        description=(
            "Previous event's current_state_hash for the same problem, "
            "or GENESIS for the first event."
        )
    )
    current_state_hash: str = Field(
        description="SHA-256 hash of the newly appended state."
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    mutation_reason: MutationReason
    operation: Literal["APPEND"] = "APPEND"

    @field_validator("previous_state_hash")
    @classmethod
    def validate_previous_hash(cls, value: str) -> str:
        normalized = value.strip()
        if normalized == GENESIS_HASH:
            return normalized

        normalized = normalized.lower()
        if not SHA256_HEX_PATTERN.fullmatch(normalized):
            raise ValueError(
                "previous_state_hash must be GENESIS or a 64-character "
                "lowercase SHA-256 hexadecimal digest."
            )
        return normalized

    @field_validator("current_state_hash")
    @classmethod
    def validate_current_hash(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not SHA256_HEX_PATTERN.fullmatch(normalized):
            raise ValueError(
                "current_state_hash must be a 64-character lowercase "
                "SHA-256 hexadecimal digest."
            )
        return normalized

    @field_validator("timestamp")
    @classmethod
    def require_timezone_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware.")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def prevent_noop_hash_transition(self) -> "VaccineGraphEvent":
        if (
            self.previous_state_hash != GENESIS_HASH
            and self.previous_state_hash == self.current_state_hash
        ):
            raise ValueError(
                "An append event must represent a new state hash."
            )
        return self


class VaccineGraphLedger(BaseModel):
    """Immutable event ledger with append-only chain validation.

    Events from multiple problems may be interleaved globally. Chain integrity
    is checked independently for each target_problem_id.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    events: tuple[VaccineGraphEvent, ...] = ()

    @model_validator(mode="after")
    def validate_event_chains(self) -> "VaccineGraphLedger":
        last_hash_by_problem: dict[int, str] = {}
        last_timestamp_by_problem: dict[int, datetime] = {}
        seen_event_ids: set[UUID] = set()

        for event in self.events:
            if event.event_id in seen_event_ids:
                raise ValueError(f"Duplicate event_id: {event.event_id}")
            seen_event_ids.add(event.event_id)

            previous_hash = last_hash_by_problem.get(event.target_problem_id)
            previous_timestamp = last_timestamp_by_problem.get(
                event.target_problem_id
            )

            if previous_hash is None:
                if event.previous_state_hash != GENESIS_HASH:
                    raise ValueError(
                        "The first event for each problem must start from GENESIS."
                    )
            elif event.previous_state_hash != previous_hash:
                raise ValueError(
                    "Broken Vaccine Graph chain: previous_state_hash does not "
                    "match the prior current_state_hash for "
                    f"problem {event.target_problem_id}."
                )

            if (
                previous_timestamp is not None
                and event.timestamp < previous_timestamp
            ):
                raise ValueError(
                    "Event timestamps must not move backwards within a "
                    "problem chain."
                )

            last_hash_by_problem[event.target_problem_id] = (
                event.current_state_hash
            )
            last_timestamp_by_problem[event.target_problem_id] = (
                event.timestamp
            )

        return self

    def append(self, event: VaccineGraphEvent) -> "VaccineGraphLedger":
        """Return a new ledger with one event appended and fully revalidated."""
        return VaccineGraphLedger(events=self.events + (event,))

    def update_event(self, *_args: object, **_kwargs: object) -> None:
        """Historical events cannot be updated."""
        raise AppendOnlyViolation(
            "UPDATE is forbidden. Append a new corrective event instead."
        )

    def delete_event(self, *_args: object, **_kwargs: object) -> None:
        """Historical events cannot be deleted."""
        raise AppendOnlyViolation(
            "DELETE is forbidden. Append an invalidation/recovery event instead."
        )
