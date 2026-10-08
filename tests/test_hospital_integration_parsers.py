from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domains.medical.hospital_access import make_role_grant, to_payload as access_payload
from app.domains.medical.hospital_access_api import router as hospital_access_router
from app.domains.medical.hospital_integration import (
    normalize_fhir_r4,
    normalize_hl7_v2,
)
from app.domains.medical.hospital_os_api import router as hospital_os_router
from app.platform_core.life_auth import router as life_auth_router
from app.platform_core.persistent_domain_store import list_resources, put_resource


def test_fhir_and_hl7_normalizers_extract_stable_identity():
    fhir = normalize_fhir_r4({
        "resourceType": "MedicationRequest",
        "id": "rx-123",
        "subject": {"reference": "Patient/p-7"},
        "status": "active",
    })
    assert fhir.external_object_id == "MedicationRequest/rx-123"
    assert fhir.message_type == "MedicationRequest"
    assert fhir.patient_reference == "Patient/p-7"

    hl7 = normalize_hl7_v2(
        "MSH|^~\\&|APP|FAC|DEST|DESTFAC|202610090100||ADT^A01|MSG00001|P|2.5\r"
        "PID|1||12345^^^HOSP^MR||DOE^JOHN"
    )
    assert hl7.external_object_id == "HL7/MSG00001"
    assert hl7.message_type == "ADT^A01"
    assert hl7.patient_reference == "12345"
    assert hl7.payload["segments"][0]["name"] == "MSH"


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


def _register_doctor(client):
    response = client.post(
        "/life/auth/register",
        json={
            "username": "integration-parser-user",
            "password": "very-secure-password",
            "invite_code": "invite-test",
        },
    )
    assert response.status_code == 200, response.text
    account_id = response.json()["account_id"]
    grant = make_role_grant(
        account_id=account_id,
        roles=["DOCTOR"],
        patient_scope=["patient-13"],
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind="hospital_os_role_grant",
        payload=access_payload(grant),
        resource_id=grant.grant_id,
    )
    return account_id


def test_integration_message_ingest_links_revision_predecessor_and_successor(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    account_id = _register_doctor(client)

    first = client.post(
        "/life/api/medical/hospital-os/integrations/messages",
        json={
            "adapter_type": "EMR",
            "standard": "FHIR_R4",
            "source_system": "sample-emr",
            "patient_id": "patient-13",
            "raw_message": {
                "resourceType": "MedicationRequest",
                "id": "rx-13",
                "subject": {"reference": "Patient/external-13"},
                "status": "active",
            },
            "verification_state": "VERIFIED",
            "changed_fields": ["status"],
        },
    )
    assert first.status_code == 200, first.text
    first_id = first.json()["source_revision_id"]
    assert first.json()["predecessor_revision_id"] is None
    assert first.json()["external_patient_reference"] == "Patient/external-13"

    second = client.post(
        "/life/api/medical/hospital-os/integrations/messages",
        json={
            "adapter_type": "EMR",
            "standard": "FHIR_R4",
            "source_system": "sample-emr",
            "patient_id": "patient-13",
            "raw_message": {
                "resourceType": "MedicationRequest",
                "id": "rx-13",
                "subject": {"reference": "Patient/external-13"},
                "status": "stopped",
            },
            "verification_state": "VERIFIED",
            "changed_fields": ["status"],
        },
    )
    assert second.status_code == 200, second.text
    second_id = second.json()["source_revision_id"]
    assert second.json()["predecessor_revision_id"] == first_id

    sources = list_resources(
        domain="medical",
        subject_id=account_id,
        kind="hospital_os_source_revision",
    )
    by_id = {row["payload"]["source_revision_id"]: row["payload"] for row in sources}
    assert by_id[first_id]["successor_revision_id"] == second_id
    assert by_id[second_id]["predecessor_revision_id"] == first_id


def test_hl7_message_ingest_preserves_external_patient_reference(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _register_doctor(client)
    message = (
        "MSH|^~\\&|LAB|FAC|EMR|FAC|202610090101||ORU^R01|LAB00001|P|2.5\r"
        "PID|1||EXT-13^^^HOSP^MR||DOE^JANE\r"
        "OBR|1|||SAMPLE"
    )
    response = client.post(
        "/life/api/medical/hospital-os/integrations/messages",
        json={
            "adapter_type": "LAB_IMAGING",
            "standard": "HL7_V2",
            "source_system": "sample-lis",
            "patient_id": "patient-13",
            "raw_message": message,
            "verification_state": "UNVERIFIED",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["message_type"] == "ORU^R01"
    assert response.json()["external_patient_reference"] == "EXT-13"
    assert response.json()["verification_state"] == "UNVERIFIED"
