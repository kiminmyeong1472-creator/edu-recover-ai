from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from app.domains.life.activity_ledger import ActivityEntry
from app.domains.life.api_models import ActivityCreateRequest, PublicSupportProgramCreateRequest
from app.domains.life.public_support import PublicSupportProgram, is_actionable
from app.platform_core.domain_scope import DomainScope
from app.platform_core.request_guards import domain_guard, require_subject_header

router = APIRouter(prefix="/preview/life", tags=["preview-life"])
_activities: list[ActivityEntry] = []
_programs: dict[str, PublicSupportProgram] = {}
_life_guard = domain_guard(DomainScope.LIFE)

@router.post("/activities")
def create_activity(
    payload: ActivityCreateRequest,
    x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
    _: DomainScope = Depends(_life_guard),
) -> dict[str, str]:
    require_subject_header(payload.subject_id, x_subject_id)
    activity = ActivityEntry.create(
        subject_id=payload.subject_id,
        domain=payload.domain,
        action=payload.action,
        resource_ref=payload.resource_ref,
        source_ref=payload.source_ref,
    )
    _activities.append(activity)
    return {"activity_id": activity.activity_id, "state": activity.state}

@router.get("/activities/{subject_id}")
def list_activities(
    subject_id: str,
    x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
    _: DomainScope = Depends(_life_guard),
) -> dict:
    require_subject_header(subject_id, x_subject_id)
    activities = [item for item in _activities if item.subject_id == subject_id]
    return {
        "subject_id": subject_id,
        "activities": [
            {
                "activity_id": item.activity_id,
                "domain": item.domain,
                "action": item.action,
                "resource_ref": item.resource_ref,
                "state": item.state,
                "occurred_at": item.occurred_at,
            }
            for item in activities
        ],
    }

@router.post("/support-programs")
def create_support_program(
    payload: PublicSupportProgramCreateRequest,
    _: DomainScope = Depends(_life_guard),
) -> dict[str, object]:
    program = PublicSupportProgram(**payload.model_dump())
    _programs[program.program_id] = program
    return {"program_id": program.program_id, "actionable": is_actionable(program)}

@router.get("/support-programs/actionable")
def list_actionable_programs(
    _: DomainScope = Depends(_life_guard),
) -> dict:
    programs = [program for program in _programs.values() if is_actionable(program)]
    return {
        "programs": [
            {
                "program_id": program.program_id,
                "domain": program.domain,
                "title": program.title,
                "provider": program.provider,
                "official_source_ref": program.official_source_ref,
                "state": program.state,
                "actionable": True,
            }
            for program in programs
        ]
    }

@router.delete("/_test/reset")
def reset_preview_store() -> dict[str, str]:
    _activities.clear()
    _programs.clear()
    return {"status": "reset"}
