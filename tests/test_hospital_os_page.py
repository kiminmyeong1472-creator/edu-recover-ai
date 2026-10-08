from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domains.medical.hospital_access import (
    make_role_grant,
    make_tenant_membership,
    to_payload,
)
from app.liferecover_runtime import router as runtime_router
from app.platform_core.life_auth import router as life_auth_router
from app.platform_core.persistent_domain_store import put_resource


def test_hospital_today_page_uses_tenant_subject_and_expanded_cards(monkeypatch, tmp_path):
    monkeypatch.setenv("EDURECOVER_CLASSROOM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LIFERECOVER_SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("LIFERECOVER_REGISTRATION_INVITE", "invite-test")
    monkeypatch.setenv("LIFERECOVER_DATA_KEY", "22" * 32)
    monkeypatch.setenv("EDURECOVER_HOSPITAL_RBAC_ENFORCED", "true")
    monkeypatch.setenv("EDURECOVER_HOSPITAL_TENANT_ENFORCED", "true")

    app = FastAPI()
    app.include_router(life_auth_router)
    app.include_router(runtime_router)
    client = TestClient(app, base_url="https://testserver")

    registered = client.post(
        "/life/auth/register",
        json={
            "username": "hospital-ui-user",
            "password": "very-secure-password",
            "invite_code": "invite-test",
        },
    )
    assert registered.status_code == 200, registered.text
    account_id = registered.json()["account_id"]
    tenant_id = "hospital-ui"

    membership = make_tenant_membership(
        account_id=account_id,
        tenant_id=tenant_id,
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
        roles=["HOSPITAL_ADMIN"],
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind="hospital_os_role_grant",
        payload=to_payload(grant),
        resource_id=grant.grant_id,
    )

    subject_id = f"hospital_tenant:{tenant_id}"
    put_resource(
        domain="medical",
        subject_id=subject_id,
        kind="hospital_os_patient_operational_status",
        payload={
            "patient_status_id": "pstat_test",
            "patient_id": "patient-ui",
            "care_setting": "OUTPATIENT",
            "location": "clinic-a",
            "attending_team": "team-a",
            "discharge_planned": False,
            "surgery_planned": False,
            "state": "CURRENT",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        resource_id="pstat_test",
    )
    put_resource(
        domain="medical",
        subject_id=subject_id,
        kind="hospital_os_operation_record",
        payload={
            "operation_record_id": "ops_test",
            "operation_type": "BED_WAIT",
            "subject_ref": "bed-a",
            "state": "OPEN",
            "owner_role": "HOSPITAL_ADMIN",
            "due_at": None,
            "details": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        resource_id="ops_test",
    )
    put_resource(
        domain="medical",
        subject_id=subject_id,
        kind="hospital_os_event",
        payload={
            "event_id": "event_test",
            "event_type": "test.resulted",
            "patient_id": "patient-ui",
            "source_revision_id": None,
            "clinical_action_id": None,
            "payload_hash": "hash",
            "payload": {"status": "resulted"},
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
        resource_id="event_test",
    )

    page = client.get("/life/medical/hospital-os")
    assert page.status_code == 200, page.text
    assert "Hospital Today" in page.text
    assert "병원 조직: hospital-ui" in page.text
    assert "외래환자</span><strong>1" in page.text
    assert "병상대기</span><strong>1" in page.text
    assert "test.resulted" in page.text
