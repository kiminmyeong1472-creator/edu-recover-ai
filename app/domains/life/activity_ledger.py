from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
import uuid


ActivityDomain = Literal["life", "medical", "legal"]
ActivityState = Literal["OPEN", "DONE", "CANCELLED", "HOLD"]


@dataclass(frozen=True)
class ActivityEntry:
    activity_id: str
    subject_id: str
    domain: ActivityDomain
    action: str
    resource_ref: str
    state: ActivityState
    occurred_at: str
    source_ref: str | None = None
    parent_activity_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        subject_id: str,
        domain: ActivityDomain,
        action: str,
        resource_ref: str,
        state: ActivityState = "OPEN",
        source_ref: str | None = None,
        parent_activity_id: str | None = None,
    ) -> "ActivityEntry":
        return cls(
            activity_id=f"act_{uuid.uuid4().hex}",
            subject_id=subject_id,
            domain=domain,
            action=action,
            resource_ref=resource_ref,
            state=state,
            occurred_at=datetime.now(timezone.utc).isoformat(),
            source_ref=source_ref,
            parent_activity_id=parent_activity_id,
        )


def append_state_transition(
    previous: ActivityEntry,
    *,
    state: ActivityState,
    action: str,
) -> ActivityEntry:
    """Append a successor event instead of mutating prior history."""
    return ActivityEntry.create(
        subject_id=previous.subject_id,
        domain=previous.domain,
        action=action,
        resource_ref=previous.resource_ref,
        state=state,
        source_ref=previous.source_ref,
        parent_activity_id=previous.activity_id,
    )
