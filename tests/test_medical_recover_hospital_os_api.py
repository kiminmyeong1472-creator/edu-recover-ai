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



def test_identity_and_medication_safety_api(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _register(client, "hospital-user-3")

    identity = client.post(
        "/life/api/medical/hospital-os/identity/verify",
        json={
            "patient_id": "patient-1",
            "expected": {"name": "Kim", "second_identifier": "1234"},
            "observed": {"name": "Kim", "second_identifier": ""},
        },
    )
    assert identity.status_code == 200, identity.text
    assert identity.json()["status"] == "UNRESOLVED"
    assert identity.json()["completion_allowed"] is False

    source = client.post(
        "/life/api/medical/hospital-os/source-revisions",
        json={
            "source_id": "rx-2",
            "payload": {"dose": "10mg"},
            "source_state": "CURRENT",
            "verification_state": "VERIFIED",
        },
    )
    source_id = source.json()["source_revision_id"]

    action = client.post(
        "/life/api/medical/hospital-os/clinical-actions",
        json={
            "patient_id": "patient-1",
            "action_type": "medication-administration",
            "responsible_role": "NURSE",
            "action_state": "INCOMPLETE",
            "risk_tier": "HIGH",
        },
    )
    action_id = action.json()["clinical_action_id"]

    medication = client.post(
        "/life/api/medical/hospital-os/medication-objects",
        json={
            "patient_id": "patient-1",
            "clinical_action_id": action_id,
            "prescription_revision_id": source_id,
            "custody_stage": "AT_BEDSIDE",
            "prepared_fields": {"drug": "A", "dose": "10mg"},
        },
    )
    assert medication.status_code == 200, medication.text
    medication_id = medication.json()["medication_object_id"]

    held = client.post(
        f"/life/api/medical/hospital-os/medication-objects/{medication_id}/prescription-change",
        json={
            "changed_from_revision_id": source_id,
            "changed_fields": ["dose"],
            "identity_status": "UNRESOLVED",
        },
    )
    assert held.status_code == 200, held.text
    assert held.json()["next_state"] == "HOLD"
    assert held.json()["required_action"] == "VERIFY_PATIENT_IDENTITY"

    status = client.get("/life/api/medical/hospital-os/status")
    assert status.status_code == 200
    assert status.json()["identity_checks"] == 1
    assert status.json()["medication_holds"] == 1



def test_completion_source_change_and_handoff_api(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _register(client, "hospital-user-4")

    before = client.post(
        "/life/api/medical/hospital-os/source-revisions",
        json={
            "source_id": "rx-complete",
            "payload": {"dose": "10mg"},
            "source_state": "CURRENT",
            "verification_state": "VERIFIED",
        },
    )
    assert before.status_code == 200, before.text
    before_id = before.json()["source_revision_id"]

    action = client.post(
        "/life/api/medical/hospital-os/clinical-actions",
        json={
            "patient_id": "patient-4",
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
            "context": {"patient_id": "patient-4"},
            "verification_state": "VERIFIED",
        },
    )
    assert use.status_code == 200, use.text

    blocked = client.post(
        f"/life/api/medical/hospital-os/clinical-actions/{action_id}/complete",
        json={"dependency_state": "RESOLVED"},
    )
    assert blocked.status_code == 409, blocked.text
    assert "PATIENT_IDENTITY_NOT_VERIFIED" in blocked.json()["detail"]["blocked_reasons"]

    identity = client.post(
        "/life/api/medical/hospital-os/identity/verify",
        json={
            "patient_id": "patient-4",
            "expected": {"name": "Kim", "second_identifier": "4444"},
            "observed": {"name": "Kim", "second_identifier": "4444"},
        },
    )
    assert identity.status_code == 200, identity.text
    assert identity.json()["status"] == "VERIFIED"

    handoff = client.post(
        "/life/api/medical/hospital-os/handoff/snapshot",
        json={"patient_id": "patient-4"},
    )
    assert handoff.status_code == 200, handoff.text
    handoff_id = handoff.json()["handoff_snapshot_id"]

    completed = client.post(
        f"/life/api/medical/hospital-os/clinical-actions/{action_id}/complete",
        json={"dependency_state": "RESOLVED"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["action_state"] == "CLOSED_HISTORICAL"

    delta = client.get(
        f"/life/api/medical/hospital-os/handoff/{handoff_id}/delta"
    )
    assert delta.status_code == 200, delta.text
    assert any(
        item["clinical_action_id"] == action_id
        and item["before"] == "INCOMPLETE"
        and item["after"] == "CLOSED_HISTORICAL"
        for item in delta.json()["changed_actions"]
    )

    audits = client.get("/life/api/medical/hospital-os/audit-events")
    assert audits.status_code == 200, audits.text
    assert len(audits.json()["audit_events"]) >= 2


def test_source_change_single_entrypoint_revalidates_actual_users(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _register(client, "hospital-user-5")

    before = client.post(
        "/life/api/medical/hospital-os/source-revisions",
        json={
            "source_id": "rx-source-change",
            "payload": {"dose": "old"},
            "source_state": "CURRENT",
            "verification_state": "VERIFIED",
        },
    )
    before_id = before.json()["source_revision_id"]

    action = client.post(
        "/life/api/medical/hospital-os/clinical-actions",
        json={
            "patient_id": "patient-5",
            "action_type": "medication-review",
            "responsible_role": "NURSE",
            "action_state": "INCOMPLETE",
            "risk_tier": "HIGH",
        },
    )
    action_id = action.json()["clinical_action_id"]

    use = client.post(
        "/life/api/medical/hospital-os/actual-use-evidence",
        json={
            "clinical_action_id": action_id,
            "source_revision_id": before_id,
            "manifest_type": "execution_manifest",
            "actor_or_system": "nurse-ui",
            "input_set": {"source_revision_id": before_id},
            "context": {"patient_id": "patient-5"},
            "verification_state": "VERIFIED",
        },
    )
    assert use.status_code == 200, use.text

    changed = client.post(
        "/life/api/medical/hospital-os/source-changes",
        json={
            "source_before_revision_id": before_id,
            "payload": {"dose": "new"},
            "source_state": "LATEST_UNCONFIRMED",
            "verification_state": "UNVERIFIED",
            "changed_fields": ["dose"],
            "dependency_state": "UNRESOLVED",
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["post_states"][action_id] == "HOLD"
    assert action_id in changed.json()["affected_action_ids"]
