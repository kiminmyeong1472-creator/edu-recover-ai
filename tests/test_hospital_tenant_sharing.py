from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domains.medical.hospital_access import (
    make_role_grant,
    make_tenant_membership,
    to_payload,
)
from app.domains.medical.hospital_access_api import router as hospital_access_router
from app.domains.medical.hospital_os_api import router as hospital_os_router
from app.platform_core.life_auth import router as life_auth_router
from app.platform_core.persistent_domain_store import put_resource


def _app(monkeypatch, tmp_path):
    monkeypatch.setenv("EDURECOVER_CLASSROOM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LIFERECOVER_SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("LIFERECOVER_REGISTRATION_INVITE", "invite-test")
    monkeypatch.setenv("LIFERECOVER_DATA_KEY", "22" * 32)
    monkeypatch.setenv("EDURECOVER_HOSPITAL_RBAC_ENFORCED", "true")
    monkeypatch.setenv("EDURECOVER_HOSPITAL_TENANT_ENFORCED", "true")
    app = FastAPI()
    app.include_router(life_auth_router)
    app.include_router(hospital_access_router)
    app.include_router(hospital_os_router)
    return app


def _register(client, username):
    response = client.post(
        "/life/auth/register",
        json={
            "username": username,
            "password": "very-secure-password",
            "invite_code": "invite-test",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["account_id"]


def _provision(account_id, tenant_id, roles, patient_scope=None, departments=None):
    membership = make_tenant_membership(
        account_id=account_id,
        tenant_id=tenant_id,
        department_ids=departments or [],
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind="hospital_os_tenant_membership",
        payload=to_payload(membership),
        resource_id=membership.membership_id,
    )
    grant = make_role_grant(
        account_id=account_id,
        tenant_id=tenant_id,
        roles=roles,
        patient_scope=patient_scope or [],
        department_scope=departments or [],
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind="hospital_os_role_grant",
        payload=to_payload(grant),
        resource_id=grant.grant_id,
    )


def test_two_staff_accounts_share_one_hospital_tenant(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    writer = TestClient(app, base_url="https://testserver")
    reader = TestClient(app, base_url="https://testserver")

    writer_id = _register(writer, "tenant-writer")
    reader_id = _register(reader, "tenant-reader")
    tenant_id = "hospital-alpha"

    _provision(
        writer_id,
        tenant_id,
        roles=["NURSE"],
        patient_scope=["patient-shared"],
        departments=["ward-a"],
    )
    _provision(
        reader_id,
        tenant_id,
        roles=["DOCTOR"],
        patient_scope=["patient-shared"],
        departments=["ward-a"],
    )

    created = writer.post(
        "/life/api/medical/hospital-os/clinical-actions",
        json={
            "patient_id": "patient-shared",
            "action_type": "shared-review",
            "responsible_role": "NURSE",
            "action_state": "INCOMPLETE",
        },
    )
    assert created.status_code == 200, created.text
    action_id = created.json()["clinical_action_id"]

    visible = reader.get(
        "/life/api/medical/hospital-os/patients/patient-shared/current-actions"
    )
    assert visible.status_code == 200, visible.text
    assert [x["clinical_action_id"] for x in visible.json()["actions"]] == [action_id]

    audits = reader.get("/life/api/medical/hospital-os/audit-events")
    assert audits.status_code == 200, audits.text
    created_audit = next(
        x for x in audits.json()["audit_events"]
        if x["event_type"] == "clinical_action.create"
    )
    assert created_audit["actor_id"] == writer_id

    access = reader.get("/life/api/medical/hospital-access/me")
    assert access.status_code == 200, access.text
    assert access.json()["tenant_mode_enforced"] is True
    assert access.json()["memberships"][0]["tenant_id"] == tenant_id


def test_tenant_membership_and_tenant_bound_grant_are_required(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    client = TestClient(app, base_url="https://testserver")
    account_id = _register(client, "tenant-denied")

    denied = client.get("/life/api/medical/hospital-os/clinical-actions")
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "hospital_tenant_membership_required"

    membership = make_tenant_membership(
        account_id=account_id,
        tenant_id="hospital-beta",
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind="hospital_os_tenant_membership",
        payload=to_payload(membership),
        resource_id=membership.membership_id,
    )

    wrong_tenant_grant = make_role_grant(
        account_id=account_id,
        tenant_id="hospital-other",
        roles=["DOCTOR"],
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind="hospital_os_role_grant",
        payload=to_payload(wrong_tenant_grant),
        resource_id=wrong_tenant_grant.grant_id,
    )

    denied_grant = client.get("/life/api/medical/hospital-os/clinical-actions")
    assert denied_grant.status_code == 403
    assert denied_grant.json()["detail"]["code"] == "hospital_permission_denied"
