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
            "username": "hospital-med-user",
            "password": "very-secure-password",
            "invite_code": "invite-test",
        },
    )
    assert response.status_code == 200, response.text
    account_id = response.json()["account_id"]
    grant = make_role_grant(
        account_id=account_id,
        roles=["NURSE", "PHARMACIST", "DOCTOR"],
        patient_scope=["patient-8"],
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind="hospital_os_role_grant",
        payload=access_payload(grant),
        resource_id=grant.grant_id,
    )
    return account_id


def test_carried_medication_requires_pharmacist_before_physician_decision(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _register_with_roles(client)

    captured = client.post(
        "/life/api/medical/hospital-os/medications/carried",
        json={
            "patient_id": "patient-8",
            "captured_description": "white round tablet",
            "imprint": "AB12",
            "packaging": "patient supplied blister",
            "ingredient_candidates": ["ingredient-a", "ingredient-b"],
            "formulary_matches": ["formulary-a"],
            "external_record_refs": ["external-rx-1"],
        },
    )
    assert captured.status_code == 200, captured.text
    carried_id = captured.json()["carried_medication_id"]
    assert captured.json()["state"] == "CANDIDATE"

    premature = client.post(
        f"/life/api/medical/hospital-os/medications/carried/{carried_id}/decision",
        json={"decision": "CONTINUE"},
    )
    assert premature.status_code == 409
    assert premature.json()["detail"] == "pharmacist_verification_required"

    verified = client.post(
        f"/life/api/medical/hospital-os/medications/carried/{carried_id}/verify",
        json={"verified_ingredient": "ingredient-a", "needs_more_info": False},
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["state"] == "PHARMACIST_VERIFIED"
    assert verified.json()["verified_ingredient"] == "ingredient-a"

    decided = client.post(
        f"/life/api/medical/hospital-os/medications/carried/{carried_id}/decision",
        json={"decision": "CONTINUE"},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["state"] == "CONTINUE"
    assert decided.json()["physician_decision"] == "CONTINUE"

    listed = client.get(
        "/life/api/medical/hospital-os/patients/patient-8/carried-medications"
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["carried_medications"]) == 1

    audits = client.get("/life/api/medical/hospital-os/audit-events")
    assert audits.status_code == 200, audits.text
    event_types = {x["event_type"] for x in audits.json()["audit_events"]}
    assert "carried_medication.capture" in event_types
    assert "carried_medication.pharmacist_verify" in event_types
    assert "carried_medication.physician_decision" in event_types


def test_integration_ingest_creates_lineage_source_and_preserves_uncertainty(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _register_with_roles(client)

    ingested = client.post(
        "/life/api/medical/hospital-os/integrations/ingest",
        json={
            "adapter_type": "EMR",
            "standard": "FHIR_R4",
            "source_system": "sample-emr",
            "external_object_id": "MedicationRequest/123",
            "patient_id": "patient-8",
            "payload": {"resourceType": "MedicationRequest", "id": "123"},
            "source_state": "CURRENT",
            "verification_state": "VERIFIED",
            "changed_fields": ["status"],
        },
    )
    assert ingested.status_code == 200, ingested.text
    assert ingested.json()["verification_state"] == "VERIFIED"
    assert ingested.json()["source_revision_id"].startswith("src_")

    uncertain = client.post(
        "/life/api/medical/hospital-os/integrations/ingest",
        json={
            "adapter_type": "PUBLIC_DATA",
            "standard": "VENDOR_JSON",
            "source_system": "external-source",
            "external_object_id": "record-2",
            "patient_id": "patient-8",
            "payload": {"value": "unconfirmed"},
            "source_state": "LATEST_UNCONFIRMED",
            "verification_state": "VERIFIED",
        },
    )
    assert uncertain.status_code == 409
    assert uncertain.json()["detail"] == "uncertain_source_cannot_be_auto_verified"

    integrations = client.get("/life/api/medical/hospital-os/integrations")
    assert integrations.status_code == 200, integrations.text
    assert len(integrations.json()["integrations"]) == 1
    assert integrations.json()["integrations"][0]["standard"] == "FHIR_R4"
