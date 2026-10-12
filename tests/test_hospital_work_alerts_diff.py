from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domains.medical.hospital_access import make_role_grant, to_payload as access_payload
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
            "username": "hospital-work-user",
            "password": "very-secure-password",
            "invite_code": "invite-test",
        },
    )
    assert response.status_code == 200, response.text
    account_id = response.json()["account_id"]
    grant = make_role_grant(
        account_id=account_id,
        roles=["NURSE"],
        patient_scope=["patient-work"],
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind="hospital_os_role_grant",
        payload=access_payload(grant),
        resource_id=grant.grant_id,
    )
    return account_id


def test_my_work_and_safety_alerts(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    account_id = _register(client)

    action = client.post(
        "/life/api/medical/hospital-os/clinical-actions",
        json={
            "patient_id": "patient-work",
            "action_type": "review",
            "responsible_role": "NURSE",
            "action_state": "HOLD",
        },
    )
    assert action.status_code == 200, action.text

    put_resource(
        domain="medical",
        subject_id=account_id,
        kind="hospital_os_identity_check",
        payload={
            "patient_id": "patient-work",
            "status": "MISMATCH",
            "matched_fields": [],
            "mismatched_fields": ["second_identifier"],
            "unresolved_fields": [],
            "completion_allowed": False,
            "reason": "identity_mismatch",
        },
        resource_id="identity-work",
    )

    work = client.get("/life/api/medical/hospital-os/my-work")
    assert work.status_code == 200, work.text
    assert "NURSE" in work.json()["roles"]
    assert len(work.json()["clinical_actions"]) == 1
    assert work.json()["clinical_actions"][0]["action_state"] == "HOLD"

    alerts = client.get("/life/api/medical/hospital-os/safety-alerts")
    assert alerts.status_code == 200, alerts.text
    kinds = {item["kind"] for item in alerts.json()["alerts"]}
    assert "clinical_action" in kinds
    assert "identity" in kinds


def test_source_revision_diff_returns_metadata_not_unstored_payload(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _register(client)

    first = client.post(
        "/life/api/medical/hospital-os/source-revisions",
        json={
            "patient_id": "patient-work",
            "source_id": "source-work",
            "payload": {"status": "before"},
            "source_state": "CURRENT",
            "verification_state": "VERIFIED",
        },
    )
    assert first.status_code == 200, first.text

    second = client.post(
        "/life/api/medical/hospital-os/source-revisions",
        json={
            "patient_id": "patient-work",
            "source_id": "source-work",
            "payload": {"status": "after"},
            "source_state": "CORRECTED",
            "verification_state": "VERIFIED",
            "predecessor_revision_id": first.json()["source_revision_id"],
            "changed_fields": ["status"],
        },
    )
    assert second.status_code == 200, second.text

    diff = client.get(
        f"/life/api/medical/hospital-os/source-revisions/{second.json()['source_revision_id']}/diff"
    )
    assert diff.status_code == 200, diff.text
    data = diff.json()
    assert data["predecessor_revision_id"] == first.json()["source_revision_id"]
    assert data["changed_fields"] == ["status"]
    assert data["content_changed"] is True
    assert "payload" not in data
    assert "metadata only" in data["note"]
