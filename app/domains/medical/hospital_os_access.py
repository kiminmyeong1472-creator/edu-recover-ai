from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException

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

Permission = Literal[
    "READ_CLINICAL",
    "WRITE_CLINICAL",
    "VERIFY_IDENTITY",
    "MANAGE_MEDICATION",
    "MANAGE_HANDOFF",
    "MANAGE_ER",
    "MANAGE_PREOP",
    "MANAGE_CLAIMS",
    "MANAGE_OPERATIONS",
    "ASSIGN_HOSPITAL_ROLES",
]

_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "DOCTOR": {"READ_CLINICAL", "WRITE_CLINICAL", "MANAGE_HANDOFF", "MANAGE_PREOP"},
    "FELLOW": {"READ_CLINICAL", "WRITE_CLINICAL", "MANAGE_HANDOFF", "MANAGE_PREOP"},
    "RESIDENT": {"READ_CLINICAL", "WRITE_CLINICAL", "MANAGE_HANDOFF", "MANAGE_ER"},
    "NURSE": {"READ_CLINICAL", "VERIFY_IDENTITY", "MANAGE_MEDICATION", "MANAGE_HANDOFF"},
    "PHARMACIST": {"READ_CLINICAL", "MANAGE_MEDICATION"},
    "SURGERY_COORDINATOR": {"READ_CLINICAL", "MANAGE_PREOP"},
    "ER_STAFF": {"READ_CLINICAL", "WRITE_CLINICAL", "VERIFY_IDENTITY", "MANAGE_ER", "MANAGE_HANDOFF"},
    "CLAIMS_STAFF": {"READ_CLINICAL", "MANAGE_CLAIMS"},
    "ADMIN_STAFF": {"MANAGE_OPERATIONS"},
    "HOSPITAL_ADMIN": {
        "READ_CLINICAL",
        "WRITE_CLINICAL",
        "VERIFY_IDENTITY",
        "MANAGE_MEDICATION",
        "MANAGE_HANDOFF",
        "MANAGE_ER",
        "MANAGE_PREOP",
        "MANAGE_CLAIMS",
        "MANAGE_OPERATIONS",
        "ASSIGN_HOSPITAL_ROLES",
    },
}


@dataclass(frozen=True)
class HospitalAccessProfile:
    account_id: str
    role: HospitalRole
    patient_scope: tuple[str, ...] = ()
    department_scope: tuple[str, ...] = ()


def role_allows(role: str, permission: str) -> bool:
    return permission in _ROLE_PERMISSIONS.get(role, set())


def enforce_role_permission(*, role: str, permission: str) -> None:
    if not role_allows(role, permission):
        raise HTTPException(status_code=403, detail="hospital_role_forbidden")


def enforce_patient_scope(*, patient_scope: tuple[str, ...], patient_id: str | None) -> None:
    if not patient_id or not patient_scope:
        return
    if patient_id not in patient_scope:
        raise HTTPException(status_code=403, detail="hospital_patient_scope_forbidden")
