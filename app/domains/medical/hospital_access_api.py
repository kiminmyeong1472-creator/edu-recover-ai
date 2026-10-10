from __future__ import annotations

import os
from dataclasses import replace
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.domains.medical.hospital_access import (
    HospitalDataSubject,
    HospitalPermission,
    HospitalRole,
    grant_allows,
    grant_from_payload,
    make_role_grant,
    make_tenant_membership,
    membership_from_payload,
    to_payload,
)
from app.platform_core.life_auth import require_life_user
from app.platform_core.persistent_domain_store import get_resource, list_resources, put_resource
from app.services.owner_basic_auth import require_owner


router = APIRouter(prefix="/life/api/medical/hospital-access", tags=["medical-recover-hospital-access"])
owner_router = APIRouter(prefix="/owner/life/medical/hospital-access", tags=["owner"])

_ROLE_GRANT_KIND = "hospital_os_role_grant"
_TENANT_MEMBERSHIP_KIND = "hospital_os_tenant_membership"


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def rbac_enforced() -> bool:
    return tenant_mode_enforced() or _bool_env("EDURECOVER_HOSPITAL_RBAC_ENFORCED", False)


def tenant_mode_enforced() -> bool:
    return _bool_env("EDURECOVER_HOSPITAL_TENANT_ENFORCED", False)


def _memberships(account_id: str):
    return [
        membership_from_payload(item["payload"])
        for item in list_resources(
            domain="medical",
            subject_id=account_id,
            kind=_TENANT_MEMBERSHIP_KIND,
        )
        if item["payload"].get("active", True)
        and item["payload"].get("account_id") == account_id
    ]


def _grants(account_id: str):
    return [
        grant_from_payload(item["payload"])
        for item in list_resources(
            domain="medical",
            subject_id=account_id,
            kind=_ROLE_GRANT_KIND,
        )
        if item["payload"].get("account_id") == account_id
    ]


def require_hospital_permission(
    request: Request,
    permission: HospitalPermission,
    *,
    patient_id: str | None = None,
    department_id: str | None = None,
    allow_scoped: bool = True,
) -> HospitalDataSubject:
    account_id = require_life_user(request)
    tenant_id: str | None = None
    patient_scope = None
    memberships = _memberships(account_id)

    if tenant_mode_enforced():
        requested_tenant = request.headers.get("X-Hospital-Tenant-Id", "").strip()
        if requested_tenant:
            matching = [m for m in memberships if m.tenant_id == requested_tenant]
        else:
            matching = memberships
        if len(matching) != 1:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "hospital_tenant_membership_required",
                    "tenant_selection_required": len(memberships) > 1 and not requested_tenant,
                },
            )
        membership = matching[0]
        tenant_id = membership.tenant_id
        if department_id is not None and membership.department_ids and department_id not in membership.department_ids:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "hospital_department_membership_denied",
                    "department_id": department_id,
                },
            )

    if rbac_enforced():
        grants = _grants(account_id)
        grants = [grant for grant in grants if grant.tenant_id == tenant_id]
        allowed_grants = [grant for grant in grants if
            grant_allows(
                grant,
                permission=permission,
                patient_id=patient_id,
                department_id=department_id,
            )
        ]
        if not allowed_grants:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "hospital_permission_denied",
                    "permission": permission,
                    "patient_id": patient_id,
                    "department_id": department_id,
                    "tenant_id": tenant_id,
                },
            )

        if all(grant.patient_scope for grant in allowed_grants):
            patient_scope = frozenset(p for grant in allowed_grants for p in grant.patient_scope)
        if not allow_scoped and (patient_scope is not None or all(g.department_scope for g in allowed_grants)):
            raise HTTPException(status_code=403, detail="hospital_unrestricted_scope_required")

    data_subject_id = f"hospital_tenant:{tenant_id}" if tenant_id else account_id
    return HospitalDataSubject(
        data_subject_id,
        actor_id=account_id,
        tenant_id=tenant_id,
        patient_scope=patient_scope,
    )


def hospital_resource_visible(subject_id: str, item: dict, seen: frozenset[str] = frozenset()) -> bool:
    scope = getattr(subject_id, "patient_scope", None)
    if scope is None:
        return True
    payload = item["payload"]
    patient_id = payload.get("patient_id")
    if patient_id:
        return patient_id in scope
    # Use evidence and deliveries inherit scope from their canonical referenced record.
    for field in ("clinical_action_id", "event_id"):
        ref = payload.get(field)
        if ref and ref != item["resource_id"] and ref not in seen:
            parent = get_resource(domain="medical", subject_id=subject_id, resource_id=ref)
            if parent:
                return hospital_resource_visible(subject_id, parent, seen | {ref})
    return False


def hospital_list_resources(*, domain, subject_id, kind=None):
    return [item for item in list_resources(domain=domain, subject_id=subject_id, kind=kind)
            if hospital_resource_visible(subject_id, item)]


def hospital_get_resource(*, domain, subject_id, resource_id):
    item = get_resource(domain=domain, subject_id=subject_id, resource_id=resource_id)
    return item if item and hospital_resource_visible(subject_id, item) else None


class RoleGrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=160)
    tenant_id: str | None = Field(default=None, max_length=160)
    roles: list[Literal[
        "DOCTOR", "FELLOW", "RESIDENT", "NURSE", "PHARMACIST",
        "SURGERY_COORDINATOR", "ER_STAFF", "CLAIMS_STAFF",
        "ADMIN_STAFF", "HOSPITAL_ADMIN"
    ]] = Field(min_length=1, max_length=10)
    patient_scope: list[str] = Field(default_factory=list, max_length=1000)
    department_scope: list[str] = Field(default_factory=list, max_length=200)


class TenantMembershipCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=160)
    tenant_id: str = Field(min_length=1, max_length=160)
    department_ids: list[str] = Field(default_factory=list, max_length=200)


@owner_router.post("/memberships")
def create_tenant_membership(
    payload: TenantMembershipCreate,
    _: None = Depends(require_owner),
) -> dict:
    membership = make_tenant_membership(
        account_id=payload.account_id,
        tenant_id=payload.tenant_id,
        department_ids=payload.department_ids,
    )
    put_resource(
        domain="medical",
        subject_id=payload.account_id,
        kind=_TENANT_MEMBERSHIP_KIND,
        payload=to_payload(membership),
        resource_id=membership.membership_id,
    )
    return to_payload(membership)


@owner_router.post("/memberships/{account_id}/{membership_id}/revoke")
def revoke_tenant_membership(
    account_id: str,
    membership_id: str,
    _: None = Depends(require_owner),
) -> dict:
    item = get_resource(
        domain="medical",
        subject_id=account_id,
        resource_id=membership_id,
    )
    if not item or item["kind"] != _TENANT_MEMBERSHIP_KIND:
        raise HTTPException(status_code=404, detail="hospital_tenant_membership_not_found")
    membership = membership_from_payload(item["payload"])
    revoked = replace(membership, active=False)
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_TENANT_MEMBERSHIP_KIND,
        payload=to_payload(revoked),
        resource_id=membership_id,
    )
    return to_payload(revoked)


@owner_router.post("/grants")
def create_role_grant(payload: RoleGrantCreate, _: None = Depends(require_owner)) -> dict:
    grant = make_role_grant(
        account_id=payload.account_id,
        roles=list(payload.roles),
        tenant_id=payload.tenant_id,
        patient_scope=payload.patient_scope,
        department_scope=payload.department_scope,
    )
    put_resource(
        domain="medical",
        subject_id=payload.account_id,
        kind=_ROLE_GRANT_KIND,
        payload=to_payload(grant),
        resource_id=grant.grant_id,
    )
    return to_payload(grant)


@owner_router.post("/grants/{account_id}/{grant_id}/revoke")
def revoke_role_grant(
    account_id: str,
    grant_id: str,
    _: None = Depends(require_owner),
) -> dict:
    item = get_resource(
        domain="medical",
        subject_id=account_id,
        resource_id=grant_id,
    )
    if not item or item["kind"] != _ROLE_GRANT_KIND:
        raise HTTPException(status_code=404, detail="hospital_role_grant_not_found")
    grant = grant_from_payload(item["payload"])
    revoked = replace(grant, revoked=True)
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind=_ROLE_GRANT_KIND,
        payload=to_payload(revoked),
        resource_id=grant_id,
    )
    return to_payload(revoked)


@router.get("/me")
def my_hospital_access(request: Request) -> dict:
    account_id = require_life_user(request)
    grants = _grants(account_id)
    memberships = _memberships(account_id)
    return {
        "account_id": account_id,
        "rbac_enforced": rbac_enforced(),
        "tenant_mode_enforced": tenant_mode_enforced(),
        "memberships": [to_payload(membership) for membership in memberships],
        "grants": [to_payload(grant) for grant in grants],
    }

