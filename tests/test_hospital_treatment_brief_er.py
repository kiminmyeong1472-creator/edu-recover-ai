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


def _register_with_roles(client):
    response = client.post(
        "/life/auth/register",
        json={
            "username": "hospital-treatment-user",
            "password": "very-secure-password",
            "invite_code": "invite-test",
        },
    )
    assert response.status_code == 200, response.text
    account_id = response.json()["account_id"]
    grant = make_role_grant(
        account_id=account_id,
        roles=["DOCTOR", "ER_STAFF"],
        patient_scope=["patient-9"],
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind="hospital_os_role_grant",
        payload=access_payload(grant),
        resource_id=grant.grant_id,
    )
    return account_id


def test_treatment_memory_flags_review_only_when_context_is_relevant(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _register_with_roles(client)

    source = client.post(
        "/life/api/medical/hospital-os/source-revisions",
        json={
            "source_id": "tx-source-9",
            "patient_id": "patient-9",
            "payload": {"note": "historical treatment record"},
            "source_state": "CURRENT",
            "verification_state": "VERIFIED",
        },
    )
    assert source.status_code == 200, source.text
    source_id = source.json()["source_revision_id"]

    episode = client.post(
        "/life/api/medical/hospital-os/treatment-memory/episodes",
        json={
            "patient_id": "patient-9",
            "intervention_type": "historical-treatment",
            "indication": "recorded indication",
            "source_refs": [source_id],
            "outcome": "INEFFECTIVE",
            "adverse_event": None,
            "discontinuation_reason": "recorded lack of benefit",
        },
    )
    assert episode.status_code == 200, episode.text
    episode_id = episode.json()["episode_id"]

    unrelated = client.post(
        f"/life/api/medical/hospital-os/treatment-memory/episodes/{episode_id}/context-review",
        json={"context_relevant": False},
    )
    assert unrelated.status_code == 200, unrelated.text
    assert unrelated.json()["review_required"] is False

    related = client.post(
        f"/life/api/medical/hospital-os/treatment-memory/episodes/{episode_id}/context-review",
        json={"context_relevant": True},
    )
    assert related.status_code == 200, related.text
    assert related.json()["review_required"] is True
    assert "PRIOR_INEFFECTIVE" in related.json()["reason_codes"]

    memory = client.get(
        "/life/api/medical/hospital-os/patients/patient-9/treatment-memory"
    )
    assert memory.status_code == 200, memory.text
    assert len(memory.json()["episodes"]) == 1


def test_professor_brief_and_er_stall_tracking_are_structural(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _register_with_roles(client)

    action = client.post(
        "/life/api/medical/hospital-os/clinical-actions",
        json={
            "patient_id": "patient-9",
            "action_type": "review-result",
            "responsible_role": "DOCTOR",
            "action_state": "INCOMPLETE",
            "risk_tier": "MEDIUM",
        },
    )
    assert action.status_code == 200, action.text
    action_id = action.json()["clinical_action_id"]

    event = client.post(
        "/life/api/medical/hospital-os/events",
        json={
            "event_type": "test.resulted",
            "patient_id": "patient-9",
            "clinical_action_id": action_id,
            "payload": {"test": "sample"},
        },
    )
    assert event.status_code == 200, event.text

    brief = client.get(
        "/life/api/medical/hospital-os/patients/patient-9/professor-brief"
    )
    assert brief.status_code == 200, brief.text
    assert action_id in brief.json()["thirty_second"]["physician_attention_action_ids"]
    assert len(brief.json()["two_minute"]["test_delta"]) == 1
    assert "no diagnosis or treatment decision" in brief.json()["note"]

    er = client.post(
        "/life/api/medical/hospital-os/er/workflows",
        json={
            "patient_id": "patient-9",
            "workflow_state": "TESTING",
            "pending_items": ["imaging interpretation pending"],
            "responsible_role": "ER_STAFF",
        },
    )
    assert er.status_code == 200, er.text

    stalled = client.get(
        "/life/api/medical/hospital-os/er/unfinished-loops",
        params={"stalled_after_minutes": 0},
    )
    assert stalled.status_code == 200, stalled.text
    row = stalled.json()["unfinished_loops"][0]
    assert row["stalled"] is True
    assert row["pending_reasons"] == ["imaging interpretation pending"]
    assert row["responsible_role"] == "ER_STAFF"

