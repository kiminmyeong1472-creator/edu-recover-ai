from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domains.medical.hospital_os_api import router as hospital_os_router
from app.platform_core.life_auth import router as life_auth_router


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("EDURECOVER_CLASSROOM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LIFERECOVER_SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("LIFERECOVER_REGISTRATION_INVITE", "invite-test")
    monkeypatch.setenv("LIFERECOVER_DATA_KEY", "22" * 32)
    app = FastAPI()
    app.include_router(life_auth_router)
    app.include_router(hospital_os_router)
    return TestClient(app, base_url="https://testserver")


def _register(client, username="hospital-user"):
    response = client.post(
        "/life/auth/register",
        json={
            "username": username,
            "password": "very-secure-password",
            "invite_code": "invite-test",
        },
    )
    assert response.status_code == 200, response.text


def test_hospital_os_persistent_selective_revalidation(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _register(client)

    before = client.post(
        "/life/api/medical/hospital-os/source-revisions",
        json={
            "source_id": "rx-1",
            "payload": {"dose": "old"},
            "source_state": "CURRENT",
            "verification_state": "VERIFIED",
        },
    )
    assert before.status_code == 200, before.text
    before_id = before.json()["source_revision_id"]

    after = client.post(
        "/life/api/medical/hospital-os/source-revisions",
        json={
            "source_id": "rx-1",
            "payload": {"dose": "new"},
            "source_state": "LATEST_UNCONFIRMED",
            "verification_state": "UNVERIFIED",
            "predecessor_revision_id": before_id,
            "changed_fields": ["dose"],
        },
    )
    assert after.status_code == 200, after.text
    after_id = after.json()["source_revision_id"]

    action = client.post(
        "/life/api/medical/hospital-os/clinical-actions",
        json={
            "patient_id": "patient-1",
            "action_type": "medication-review",
            "responsible_role": "NURSE",
            "action_state": "INCOMPLETE",
            "risk_tier": "HIGH",
        },
    )
    assert action.status_code == 200, action.text
    action_id = action.json()["clinical_action_id"]

    use = client.post(
        "/life/api/medical/hospital-os/actual-use-evidence",
        json={
            "clinical_action_id": action_id,
            "source_revision_id": before_id,
            "manifest_type": "execution_manifest",
            "actor_or_system": "nurse-ui",
            "input_set": {"source_revision_id": before_id},
            "context": {"patient_id": "patient-1"},
            "verification_state": "VERIFIED",
        },
    )
    assert use.status_code == 200, use.text

    result = client.post(
        "/life/api/medical/hospital-os/revalidation",
        json={
            "source_before_revision_id": before_id,
            "source_after_revision_id": after_id,
            "dependency_state": "UNRESOLVED",
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["post_states"][action_id] == "HOLD"

    status = client.get("/life/api/medical/hospital-os/status")
    assert status.status_code == 200
    assert status.json()["current_holds"] == 1
    assert status.json()["safety_rules"]["unknown_auto_verified"] is False

    db_bytes = (tmp_path / "liferecover" / "medical.sqlite3").read_bytes()
    assert b'"dose":"new"' not in db_bytes


def test_uncertain_verified_source_is_rejected(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _register(client, "hospital-user-2")
    response = client.post(
        "/life/api/medical/hospital-os/source-revisions",
        json={
            "source_id": "external-1",
            "payload": {"value": "unknown"},
            "source_state": "LATEST_UNCONFIRMED",
            "verification_state": "VERIFIED",
        },
    )
    assert response.status_code == 409
