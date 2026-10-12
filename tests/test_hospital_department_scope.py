from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domains.medical.hospital_access import (
    make_role_grant,
    make_tenant_membership,
    to_payload as access_payload,
)
from app.domains.medical.hospital_access_api import router as hospital_access_router
from app.domains.medical.hospital_os import ClinicalAction, make_hospital_operation_record, to_payload
from app.domains.medical.hospital_os_api import router as hospital_os_router
from app.platform_core.life_auth import router as life_auth_router
from app.platform_core.persistent_domain_store import put_resource


def _client(monkeypatch, tmp_path):
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
    return TestClient(app, base_url="https://testserver")


def _register_and_provision(client, departments):
    response = client.post(
        "/life/auth/register",
        json={
            "username": "department-user-" + "-".join(departments),
            "password": "very-secure-password",
            "invite_code": "invite-test",
        },
    )
    assert response.status_code == 200, response.text
    account_id = response.json()["account_id"]
    tenant_id = "hospital-department-test"
    membership = make_tenant_membership(
        account_id=account_id,
        tenant_id=tenant_id,
        department_ids=departments,
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind="hospital_os_tenant_membership",
        payload=access_payload(membership),
        resource_id=membership.membership_id,
    )
    grant = make_role_grant(
        account_id=account_id,
        tenant_id=tenant_id,
        roles=["NURSE", "HOSPITAL_ADMIN"],
        department_scope=departments,
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind="hospital_os_role_grant",
        payload=access_payload(grant),
        resource_id=grant.grant_id,
    )
    return account_id, tenant_id


def test_department_scope_filters_existing_tenant_records_and_blocks_cross_department_writes(
    monkeypatch, tmp_path
):
    client = _client(monkeypatch, tmp_path)
    _, tenant_id = _register_and_provision(client, ["ward-a"])

    census = client.post(
        "/life/api/medical/hospital-os/census/patients",
        json={
            "patient_id": "patient-a",
            "department_id": "ward-a",
            "care_setting": "INPATIENT",
            "location": "A-101",
            "attending_team": "team-a",
        },
    )
    assert census.status_code == 200, census.text
    assert census.json()["department_id"] == "ward-a"

    allowed_action = client.post(
        "/life/api/medical/hospital-os/clinical-actions",
        json={
            "patient_id": "patient-a",
            "department_id": "ward-a",
            "action_type": "review",
            "responsible_role": "NURSE",
        },
    )
    assert allowed_action.status_code == 200, allowed_action.text

    blocked_action = client.post(
        "/life/api/medical/hospital-os/clinical-actions",
        json={
            "patient_id": "patient-b",
            "department_id": "ward-b",
            "action_type": "review",
            "responsible_role": "NURSE",
        },
    )
    assert blocked_action.status_code == 403
    assert blocked_action.json()["detail"]["code"] == "hospital_department_membership_denied"

    tenant_subject = f"hospital_tenant:{tenant_id}"
    hidden_action = ClinicalAction(
        clinical_action_id="act-hidden-ward-b",
        patient_id="patient-hidden",
        action_type="hidden-review",
        responsible_role="NURSE",
        action_state="INCOMPLETE",
        created_at="2026-10-12T00:00:00+00:00",
        department_id="ward-b",
    )
    put_resource(
        domain="medical",
        subject_id=tenant_subject,
        kind="hospital_os_clinical_action",
        payload=to_payload(hidden_action),
        resource_id=hidden_action.clinical_action_id,
    )

    visible_actions = client.get("/life/api/medical/hospital-os/clinical-actions")
    assert visible_actions.status_code == 200, visible_actions.text
    action_ids = {item["clinical_action_id"] for item in visible_actions.json()["actions"]}
    assert allowed_action.json()["clinical_action_id"] in action_ids
    assert hidden_action.clinical_action_id not in action_ids

    allowed_operation = client.post(
        "/life/api/medical/hospital-os/operations",
        json={
            "department_id": "ward-a",
            "operation_type": "BED_WAIT",
            "subject_ref": "bed-a",
            "state": "OPEN",
            "owner_role": "HOSPITAL_ADMIN",
        },
    )
    assert allowed_operation.status_code == 200, allowed_operation.text
    assert allowed_operation.json()["department_id"] == "ward-a"

    blocked_operation = client.post(
        "/life/api/medical/hospital-os/operations",
        json={
            "department_id": "ward-b",
            "operation_type": "BED_WAIT",
            "subject_ref": "bed-b",
            "state": "OPEN",
            "owner_role": "HOSPITAL_ADMIN",
        },
    )
    assert blocked_operation.status_code == 403

    hidden_operation = make_hospital_operation_record(
        operation_type="BED_WAIT",
        subject_ref="hidden-bed-b",
        state="OPEN",
        owner_role="HOSPITAL_ADMIN",
        due_at=None,
        details={},
        operation_record_id="ops-hidden-ward-b",
        department_id="ward-b",
    )
    put_resource(
        domain="medical",
        subject_id=tenant_subject,
        kind="hospital_os_operation_record",
        payload=to_payload(hidden_operation),
        resource_id=hidden_operation.operation_record_id,
    )

    operations = client.get("/life/api/medical/hospital-os/operations")
    assert operations.status_code == 200, operations.text
    refs = {item["subject_ref"] for item in operations.json()["operations"]}
    assert "bed-a" in refs
    assert "hidden-bed-b" not in refs

    today = client.get("/life/api/medical/hospital-os/hospital/today")
    assert today.status_code == 200, today.text
    assert today.json()["bed_waits"] == 1


def test_source_revision_cannot_move_between_departments(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _register_and_provision(client, ["ward-a", "ward-b"])

    first = client.post(
        "/life/api/medical/hospital-os/source-revisions",
        json={
            "patient_id": "patient-lineage",
            "department_id": "ward-a",
            "source_id": "source-lineage",
            "payload": {"value": "first"},
            "source_state": "CURRENT",
            "verification_state": "VERIFIED",
        },
    )
    assert first.status_code == 200, first.text

    moved = client.post(
        "/life/api/medical/hospital-os/source-revisions",
        json={
            "patient_id": "patient-lineage",
            "department_id": "ward-b",
            "source_id": "source-lineage",
            "payload": {"value": "second"},
            "source_state": "CORRECTED",
            "verification_state": "VERIFIED",
            "predecessor_revision_id": first.json()["source_revision_id"],
            "changed_fields": ["value"],
        },
    )
    assert moved.status_code == 409
    assert moved.json()["detail"] == "source_department_mismatch"
