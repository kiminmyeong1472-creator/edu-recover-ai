from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal
import uuid


HospitalRole = Literal[
    "DOCTOR",
    "FELLOW",
    "RESIDENT",
    "NURSE",
    "PHARMACIST",
    "SURGERY_COORDINATOR",
    "ER_STAFF",
    "CLAIMS_STAFF",
    "ADMIN_STAFF",
    "HOSPITAL_ADMIN",
]

HospitalPermission = Literal[
    "clinical.read",
    "clinical.write",
    "identity.verify",
    "medication.manage",
    "medication.verify",
    "medication.decide",
    "handoff.manage",
    "report.manage",
    "er.manage",
    "preop.manage",
    "claims.manage",
    "operations.manage",
    "hospital.dashboard",
    "events.manage",
]

_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "DOCTOR": frozenset({
        "clinical.read", "clinical.write", "identity.verify", "medication.manage", "medication.decide",
        "handoff.manage", "report.manage", "preop.manage", "hospital.dashboard",
    }),
    "FELLOW": frozenset({
        "clinical.read", "clinical.write", "identity.verify", "medication.manage", "medication.decide",
        "handoff.manage", "report.manage", "preop.manage",
    }),
    "RESIDENT": frozenset({
        "clinical.read", "clinical.write", "identity.verify", "medication.manage", "medication.decide",
        "handoff.manage", "report.manage", "preop.manage",
    }),
    "NURSE": frozenset({
        "clinical.read", "clinical.write", "identity.verify", "medication.manage",
        "handoff.manage",
    }),
    "PHARMACIST": frozenset({
        "clinical.read", "medication.manage", "medication.verify",
    }),
    "SURGERY_COORDINATOR": frozenset({
        "clinical.read", "preop.manage", "handoff.manage",
    }),
    "ER_STAFF": frozenset({
        "clinical.read", "clinical.write", "identity.verify", "medication.manage",
        "handoff.manage", "er.manage",
    }),
    "CLAIMS_STAFF": frozenset({
        "clinical.read", "claims.manage",
    }),
    "ADMIN_STAFF": frozenset({
        "operations.manage", "hospital.dashboard", "events.manage",
    }),
    "HOSPITAL_ADMIN": frozenset({
        "clinical.read", "hospital.dashboard", "operations.manage", "claims.manage", "events.manage",
    }),
}


@dataclass(frozen=True)
class HospitalTenantMembership:
    membership_id: str
    account_id: str
    tenant_id: str
    department_ids: tuple[str, ...]
    active: bool
    issued_at: str


class HospitalDataSubject(str):
    """String-compatible storage subject carrying the authenticated actor identity."""

    actor_id: str
    tenant_id: str | None
    patient_scope: frozenset[str] | None
    department_ids: tuple[str, ...]

    def __new__(
        cls,
        value: str,
        *,
        actor_id: str,
        tenant_id: str | None = None,
        patient_scope: frozenset[str] | None = None,
        department_ids: tuple[str, ...] = (),
    ):
        obj = str.__new__(cls, value)
        obj.actor_id = actor_id
        obj.tenant_id = tenant_id
        obj.patient_scope = patient_scope
        obj.department_ids = tuple(department_ids)
        return obj


def make_tenant_membership(
    *,
    account_id: str,
    tenant_id: str,
    department_ids: list[str] | None = None,
) -> HospitalTenantMembership:
    tenant_id = tenant_id.strip()
    if not tenant_id:
        raise ValueError("hospital_tenant_id_required")
    return HospitalTenantMembership(
        membership_id=f"hmem_{uuid.uuid4().hex}",
        account_id=account_id,
        tenant_id=tenant_id,
        department_ids=tuple(dict.fromkeys(department_ids or ())),
        active=True,
        issued_at=datetime.now(timezone.utc).isoformat(),
    )


def membership_from_payload(payload: dict) -> HospitalTenantMembership:
    return HospitalTenantMembership(
        membership_id=payload["membership_id"],
        account_id=payload["account_id"],
        tenant_id=payload["tenant_id"],
        department_ids=tuple(payload.get("department_ids", ())),
        active=bool(payload.get("active", True)),
        issued_at=payload["issued_at"],
    )


@dataclass(frozen=True)
class HospitalRoleGrant:
    grant_id: str
    account_id: str
    tenant_id: str | None
    roles: tuple[HospitalRole, ...]
    patient_scope: tuple[str, ...]
    department_scope: tuple[str, ...]
    issued_at: str
    revoked: bool = False


def make_role_grant(
    *,
    account_id: str,
    roles: list[HospitalRole],
    tenant_id: str | None = None,
    patient_scope: list[str] | None = None,
    department_scope: list[str] | None = None,
) -> HospitalRoleGrant:
    normalized_roles = tuple(dict.fromkeys(roles))
    if not normalized_roles:
        raise ValueError("hospital_role_required")
    return HospitalRoleGrant(
        grant_id=f"hrole_{uuid.uuid4().hex}",
        account_id=account_id,
        tenant_id=(tenant_id.strip() if tenant_id else None),
        roles=normalized_roles,
        patient_scope=tuple(dict.fromkeys(patient_scope or ())),
        department_scope=tuple(dict.fromkeys(department_scope or ())),
        issued_at=datetime.now(timezone.utc).isoformat(),
    )


def grant_allows(
    grant: HospitalRoleGrant,
    *,
    permission: HospitalPermission,
    patient_id: str | None = None,
    department_id: str | None = None,
) -> bool:
    if grant.revoked:
        return False
    if not any(permission in _ROLE_PERMISSIONS.get(role, ()) for role in grant.roles):
        return False
    if patient_id is not None and grant.patient_scope and patient_id not in grant.patient_scope:
        return False
    if department_id is not None and grant.department_scope and department_id not in grant.department_scope:
        return False
    return True


def grant_from_payload(payload: dict) -> HospitalRoleGrant:
    return HospitalRoleGrant(
        grant_id=payload["grant_id"],
        account_id=payload["account_id"],
        tenant_id=payload.get("tenant_id"),
        roles=tuple(payload.get("roles", ())),
        patient_scope=tuple(payload.get("patient_scope", ())),
        department_scope=tuple(payload.get("department_scope", ())),
        issued_at=payload["issued_at"],
        revoked=bool(payload.get("revoked", False)),
    )


def to_payload(grant: HospitalRoleGrant) -> dict:
    return asdict(grant)

