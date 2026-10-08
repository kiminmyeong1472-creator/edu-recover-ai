from __future__ import annotations

import os
from dataclasses import replace
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.domains.medical.hospital_access import (
    HospitalPermission,
    HospitalRole,
    grant_allows,
    grant_from_payload,
    make_role_grant,
    to_payload,
)
from app.platform_core.life_auth import require_life_user
from app.platform_core.persistent_domain_store import get_resource, list_resources, put_resource
from app.services.owner_basic_auth import require_owner


router = APIRouter(prefix="/life/api/medical/hospital-access", tags=["medical-recover-hospital-access"])
owner_router = APIRouter(prefix="/owner/life/medical/hospital-access", tags=["owner"])

_ROLE_GRANT_KIND = "hospital_os_role_grant"


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
    return _bool_env("EDURECOVER_HOSPITAL_RBAC_ENFORCED", False)


def _grants(account_id: str):
    return [
        grant_from_payload(item["payload"])
        for item in list_resources(
            domain="medical",
            subject_id=account_id,
            kind=_ROLE_GRANT_KIND,
        )
    ]


def require_hospital_permission(
    request: Request,
    permission: HospitalPermission,
    *,
    patient_id: str | None = None,
    department_id: str | None = None,
) -> str:
    account_id = require_life_user(request)
    if not rbac_enforced():
        return account_id
    grants = _grants(account_id)
    if any(
        grant_allows(
            grant,
            permission=permission,
            patient_id=patient_id,
            department_id=department_id,
        )
        for grant in grants
    ):
        return account_id
    raise HTTPException(
        status_code=403,
        detail={
            "code": "hospital_permission_denied",
            "permission": permission,
            "patient_id": patient_id,
            "department_id": department_id,
        },
    )


class RoleGrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=160)
    roles: list[Literal[
        "DOCTOR", "FELLOW", "RESIDENT", "NURSE", "PHARMACIST",
        "SURGERY_COORDINATOR", "ER_STAFF", "CLAIMS_STAFF",
        "ADMIN_STAFF", "HOSPITAL_ADMIN"
    ]] = Field(min_length=1, max_length=10)
    patient_scope: list[str] = Field(default_factory=list, max_length=1000)
    department_scope: list[str] = Field(default_factory=list, max_length=200)


@owner_router.post("/grants")
def create_role_grant(payload: RoleGrantCreate, _: None = Depends(require_owner)) -> dict:
    grant = make_role_grant(
        account_id=payload.account_id,
        roles=list(payload.roles),
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
    return {
        "account_id": account_id,
        "rbac_enforced": rbac_enforced(),
        "grants": [to_payload(grant) for grant in grants],
    }
