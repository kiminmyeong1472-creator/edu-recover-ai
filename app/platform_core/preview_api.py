from __future__ import annotations

import uuid

from fastapi import APIRouter, Header

from app.platform_core.api_models import ConsentCreateRequest, SharingGrantCreateRequest
from app.platform_core.audit import AuditEvent
from app.platform_core.consent import ConsentReceipt
from app.platform_core.domain_scope import DomainScope
from app.platform_core.request_guards import require_domain_value, require_subject_header
from app.platform_core.sharing import SharingGrant


router = APIRouter(prefix="/preview/core", tags=["preview-core"])
_consents: dict[str, ConsentReceipt] = {}
_shares: dict[str, SharingGrant] = {}
_audits: list[AuditEvent] = []


def _record_audit(
    *,
    actor_id: str,
    domain: str,
    action: str,
    resource_id: str,
) -> None:
    _audits.append(
        AuditEvent.record(
            actor_id=actor_id,
            domain=domain,
            action=action,
            resource_id=resource_id,
        )
    )


@router.post("/consents")
def create_consent(
    payload: ConsentCreateRequest,
    x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
    x_domain_scope: str | None = Header(default=None, alias="X-Domain-Scope"),
) -> dict[str, str]:
    require_subject_header(payload.subject_id, x_subject_id)
    require_domain_value(DomainScope(payload.domain), x_domain_scope)

    consent_id = f"consent_{uuid.uuid4().hex}"
    _consents[consent_id] = ConsentReceipt.issue(
        subject_id=payload.subject_id,
        domain=payload.domain,
        purpose=payload.purpose,
        policy_version=payload.policy_version,
    )
    _record_audit(
        actor_id=payload.subject_id,
        domain=payload.domain,
        action="consent_issued",
        resource_id=consent_id,
    )
    return {"consent_id": consent_id, "domain": payload.domain}


@router.post("/shares")
def create_share(
    payload: SharingGrantCreateRequest,
    x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
    x_domain_scope: str | None = Header(default=None, alias="X-Domain-Scope"),
) -> dict[str, str]:
    require_subject_header(payload.subject_id, x_subject_id)
    require_domain_value(DomainScope(payload.source_domain), x_domain_scope)

    grant = SharingGrant.issue(
        subject_id=payload.subject_id,
        source_domain=payload.source_domain,
        target_party=payload.target_party,
        purpose=payload.purpose,
        resource_refs=payload.resource_refs,
        expires_at=payload.expires_at,
    )
    _shares[grant.grant_id] = grant
    _record_audit(
        actor_id=payload.subject_id,
        domain=payload.source_domain,
        action="sharing_grant_issued",
        resource_id=grant.grant_id,
    )
    return {"grant_id": grant.grant_id, "source_domain": payload.source_domain}


@router.get("/audits/{subject_id}")
def list_audits(
    subject_id: str,
    x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
) -> dict:
    require_subject_header(subject_id, x_subject_id)
    events = [event for event in _audits if event.actor_id == subject_id]
    return {
        "subject_id": subject_id,
        "events": [
            {
                "actor_id": event.actor_id,
                "domain": event.domain,
                "action": event.action,
                "resource_id": event.resource_id,
                "occurred_at": event.occurred_at,
            }
            for event in events
        ],
    }


@router.delete("/_test/reset")
def reset_preview_store() -> dict[str, str]:
    _consents.clear()
    _shares.clear()
    _audits.clear()
    return {"status": "reset"}
