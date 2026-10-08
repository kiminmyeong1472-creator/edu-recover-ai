from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domains.medical.hospital_access import grant_allows, make_role_grant, to_payload
from app.domains.medical.hospital_access_api import router as hospital_access_router
from app.domains.medical.hospital_os_api import router as hospital_os_router
from app.platform_core.life_auth import router as life_auth_router
from app.platform_core.persistent_domain_store import put_resource


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("EDURECOVER_CLASSROOM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LIFERECOVER_SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("LIFERECOVER_REGISTRATION_INVITE", "invite-test")
    monkeypatch.setenv("LIFERECOVER_DATA_KEY", "22" * 32)
    monkeypatch.setenv("EDURECOVER_HOSPITAL_RBAC_ENFORCED", "true")
    app = FastAPI()
    app.include_router(life_auth_router)
    app.include_router(hospital_access_router)
    app.include_router(hospital_os_router)
    return TestClient(app, base_url="https://testserver")


def _register(client):
    response = client.post(
        "/life/auth/register",
        json={
            "username": "rbac-user",
            "password": "very-secure-password",
            "invite_code": "invite-test",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["account_id"]


def test_role_policy_is_least_privilege_and_patient_scoped():
    grant = make_role_grant(
        account_id="a1",
        roles=["NURSE"],
        patient_scope=["patient-1"],
    )
    assert grant_allows(grant, permission="clinical.read", patient_id="patient-1")
    assert grant_allows(grant, permission="identity.verify", patient_id="patient-1")
    assert not grant_allows(grant, permission="claims.manage", patient_id="patient-1")
    assert not grant_allows(grant, permission="clinical.read", patient_id="patient-2")


def test_hospital_api_enforces_granted_role_and_patient_scope(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    account_id = _register(client)

    denied = client.post(
        "/life/api/medical/hospital-os/clinical-actions",
        json={
            "patient_id": "patient-1",
            "action_type": "review",
            "responsible_role": "NURSE",
        },
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "hospital_permission_denied"

    grant = make_role_grant(
        account_id=account_id,
        roles=["NURSE"],
        patient_scope=["patient-1"],
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind="hospital_os_role_grant",
        payload=to_payload(grant),
        resource_id=grant.grant_id,
    )

    allowed = client.post(
        "/life/api/medical/hospital-os/clinical-actions",
        json={
            "patient_id": "patient-1",
            "action_type": "review",
            "responsible_role": "NURSE",
        },
    )
    assert allowed.status_code == 200, allowed.text

    other_patient = client.post(
        "/life/api/medical/hospital-os/clinical-actions",
        json={
            "patient_id": "patient-2",
            "action_type": "review",
            "responsible_role": "NURSE",
        },
    )
    assert other_patient.status_code == 403

    dashboard = client.get("/life/api/medical/hospital-os/hospital/today")
    assert dashboard.status_code == 403

    access = client.get("/life/api/medical/hospital-access/me")
    assert access.status_code == 200
    assert access.json()["rbac_enforced"] is True
    assert access.json()["grants"][0]["roles"] == ["NURSE"]
