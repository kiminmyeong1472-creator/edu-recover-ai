from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from app.domains.legal.api_models import CaseEventCreateRequest, LegalDeadlineCreateRequest
from app.domains.legal.case_timeline import CaseTimelineEvent, ordered_timeline
from app.domains.legal.deadlines import LegalDeadline
from app.platform_core.domain_scope import DomainScope
from app.platform_core.request_guards import domain_guard, require_subject_header


router = APIRouter(prefix="/preview/legal", tags=["preview-legal"])
_events: list[CaseTimelineEvent] = []
_deadlines: dict[str, LegalDeadline] = {}
_legal_guard = domain_guard(DomainScope.LEGAL)


@router.post("/events")
def create_case_event(
    payload: CaseEventCreateRequest,
    x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
    _: DomainScope = Depends(_legal_guard),
) -> dict[str, str]:
    require_subject_header(payload.subject_id, x_subject_id)
    event = CaseTimelineEvent.create(
        case_id=payload.case_id,
        subject_id=payload.subject_id,
        stage=payload.stage,
        kind=payload.kind,
        title=payload.title,
        occurred_at=payload.occurred_at,
        source_ref=payload.source_ref,
    )
    _events.append(event)
    return {"event_id": event.event_id}


@router.get("/cases/{case_id}/timeline")
def get_case_timeline(
    case_id: str,
    subject_id: str,
    x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
    _: DomainScope = Depends(_legal_guard),
) -> dict:
    require_subject_header(subject_id, x_subject_id)
    events = [
        event for event in _events
        if event.case_id == case_id and event.subject_id == subject_id
    ]
    return {
        "case_id": case_id,
        "events": [
            {
                "event_id": event.event_id,
                "stage": event.stage,
                "kind": event.kind,
                "title": event.title,
                "occurred_at": event.occurred_at,
                "source_ref": event.source_ref,
            }
            for event in ordered_timeline(events)
        ],
    }


@router.post("/deadlines")
def create_deadline(
    payload: LegalDeadlineCreateRequest,
    x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
    _: DomainScope = Depends(_legal_guard),
) -> dict[str, str]:
    require_subject_header(payload.subject_id, x_subject_id)
    deadline = LegalDeadline.create(
        case_id=payload.case_id,
        subject_id=payload.subject_id,
        title=payload.title,
        due_at=payload.due_at,
        source_ref=payload.source_ref,
        legal_basis_ref=payload.legal_basis_ref,
    )
    _deadlines[deadline.deadline_id] = deadline
    return {"deadline_id": deadline.deadline_id, "state": deadline.state}


@router.delete("/_test/reset")
def reset_preview_store() -> dict[str, str]:
    _events.clear()
    _deadlines.clear()
    return {"status": "reset"}
