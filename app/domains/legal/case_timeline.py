from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
import uuid


CaseStage = Literal[
    "INTAKE",
    "POLICE",
    "PROSECUTION",
    "COURT",
    "ADMINISTRATIVE",
    "CLOSED",
]

EventKind = Literal[
    "FACT",
    "FILING",
    "NOTICE",
    "DEADLINE",
    "EVIDENCE",
    "DECISION",
    "CONSULTATION",
]


@dataclass(frozen=True)
class CaseTimelineEvent:
    event_id: str
    case_id: str
    subject_id: str
    stage: CaseStage
    kind: EventKind
    title: str
    occurred_at: str
    source_ref: str | None = None
    supersedes_event_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        case_id: str,
        subject_id: str,
        stage: CaseStage,
        kind: EventKind,
        title: str,
        occurred_at: str | None = None,
        source_ref: str | None = None,
        supersedes_event_id: str | None = None,
    ) -> "CaseTimelineEvent":
        return cls(
            event_id=f"evt_{uuid.uuid4().hex}",
            case_id=case_id,
            subject_id=subject_id,
            stage=stage,
            kind=kind,
            title=title,
            occurred_at=occurred_at or datetime.now(timezone.utc).isoformat(),
            source_ref=source_ref,
            supersedes_event_id=supersedes_event_id,
        )


def revise_event(
    previous: CaseTimelineEvent,
    *,
    title: str,
    source_ref: str | None = None,
) -> CaseTimelineEvent:
    """Preserve the old event and append a successor revision."""
    return CaseTimelineEvent.create(
        case_id=previous.case_id,
        subject_id=previous.subject_id,
        stage=previous.stage,
        kind=previous.kind,
        title=title,
        source_ref=source_ref or previous.source_ref,
        supersedes_event_id=previous.event_id,
    )


def ordered_timeline(events: list[CaseTimelineEvent]) -> tuple[CaseTimelineEvent, ...]:
    if not events:
        return ()
    case_ids = {event.case_id for event in events}
    subject_ids = {event.subject_id for event in events}
    if len(case_ids) != 1 or len(subject_ids) != 1:
        raise PermissionError("mixed_case_or_subject_scope")
    return tuple(sorted(events, key=lambda event: (event.occurred_at, event.event_id)))
