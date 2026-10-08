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
            "username": "event-outbox-user",
            "password": "very-secure-password",
            "invite_code": "invite-test",
        },
    )
    assert response.status_code == 200, response.text
    account_id = response.json()["account_id"]
    grant = make_role_grant(
        account_id=account_id,
        roles=["DOCTOR", "HOSPITAL_ADMIN"],
        patient_scope=["patient-event"],
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind="hospital_os_role_grant",
        payload=access_payload(grant),
        resource_id=grant.grant_id,
    )
    return account_id


def test_event_subscription_creates_durable_outbox_and_ack(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _register_with_roles(client)

    subscription = client.post(
        "/life/api/medical/hospital-os/event-subscriptions",
        json={
            "consumer_id": "notification-service",
            "event_types": ["test.resulted", "consult.responded"],
        },
    )
    assert subscription.status_code == 200, subscription.text

    event = client.post(
        "/life/api/medical/hospital-os/events",
        json={
            "event_type": "test.resulted",
            "patient_id": "patient-event",
            "payload": {
                "test_code": "sample-test",
                "status": "resulted",
            },
        },
    )
    assert event.status_code == 200, event.text
    event_id = event.json()["event_id"]
    assert event.json()["payload"]["test_code"] == "sample-test"

    outbox = client.get(
        "/life/api/medical/hospital-os/event-outbox/notification-service"
    )
    assert outbox.status_code == 200, outbox.text
    assert outbox.json()["pending_count"] == 1
    row = outbox.json()["items"][0]
    assert row["event"]["event_id"] == event_id
    assert row["event"]["payload"]["status"] == "resulted"
    delivery_id = row["delivery"]["delivery_id"]

    ack = client.post(
        f"/life/api/medical/hospital-os/event-outbox/{delivery_id}/ack"
    )
    assert ack.status_code == 200, ack.text
    assert ack.json()["state"] == "ACKED"
    assert ack.json()["attempts"] == 1
    assert ack.json()["acknowledged_at"] is not None

    empty = client.get(
        "/life/api/medical/hospital-os/event-outbox/notification-service"
    )
    assert empty.status_code == 200, empty.text
    assert empty.json()["pending_count"] == 0
    assert empty.json()["items"] == []
