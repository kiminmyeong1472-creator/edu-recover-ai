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
            "username": "hospital-today-user",
            "password": "very-secure-password",
            "invite_code": "invite-test",
        },
    )
    assert response.status_code == 200, response.text
    account_id = response.json()["account_id"]
    grant = make_role_grant(
        account_id=account_id,
        roles=["DOCTOR", "HOSPITAL_ADMIN"],
    )
    put_resource(
        domain="medical",
        subject_id=account_id,
        kind="hospital_os_role_grant",
        payload=access_payload(grant),
        resource_id=grant.grant_id,
    )
    return account_id


def test_hospital_today_expanded_census_and_operational_counters(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _register_with_roles(client)

    for payload in (
        {
            "patient_id": "patient-10",
            "care_setting": "OUTPATIENT",
            "location": "clinic-a",
            "attending_team": "team-a",
        },
        {
            "patient_id": "patient-11",
            "care_setting": "INPATIENT",
            "location": "ward-b",
            "attending_team": "team-b",
            "discharge_planned": True,
        },
        {
            "patient_id": "patient-12",
            "care_setting": "ER",
            "location": "er-1",
            "attending_team": "er-team",
            "surgery_planned": True,
        },
    ):
        response = client.post(
            "/life/api/medical/hospital-os/census/patients",
            json=payload,
        )
        assert response.status_code == 200, response.text

    for operation_type in (
        "BED_WAIT",
        "SIGNATURE_PENDING",
        "BILLING_ERROR",
        "RECEIVABLE",
    ):
        response = client.post(
            "/life/api/medical/hospital-os/operations",
            json={
                "operation_type": operation_type,
                "subject_ref": operation_type.lower(),
                "state": "OPEN",
                "owner_role": "HOSPITAL_ADMIN",
            },
        )
        assert response.status_code == 200, response.text

    event = client.post(
        "/life/api/medical/hospital-os/events",
        json={
            "event_type": "test.resulted",
            "patient_id": "patient-10",
            "payload": {"test": "sample"},
        },
    )
    assert event.status_code == 200, event.text

    today = client.get("/life/api/medical/hospital-os/hospital/today")
    assert today.status_code == 200, today.text
    data = today.json()
    assert data["outpatient_patients"] == 1
    assert data["inpatient_patients"] == 1
    assert data["er_patients"] == 1
    assert data["discharge_planned"] == 1
    assert data["surgery_planned"] == 1
    assert data["bed_waits"] == 1
    assert data["signature_pending"] == 1
    assert data["billing_errors"] == 1
    assert data["receivables"] == 1
    assert len(data["last_hour_changes"]) == 1
    assert "now_review" in data


def test_preop_passport_tracks_ingredient_purpose_and_steroid_route(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _register_with_roles(client)

    exposure = client.post(
        "/life/api/medical/hospital-os/preop/exposures",
        json={
            "patient_id": "patient-10",
            "drug_name": "sample-steroid",
            "ingredient": "sample-ingredient",
            "purpose": "recorded purpose",
            "route": "epidural",
            "dose_intensity": "recorded intensity",
            "last_exposure_at": "2026-10-01T00:00:00+00:00",
            "verification_state": "UNVERIFIED",
            "review_categories": ["ADRENAL_REVIEW"],
            "is_steroid_exposure": True,
        },
    )
    assert exposure.status_code == 200, exposure.text
    data = exposure.json()
    assert data["ingredient"] == "sample-ingredient"
    assert data["purpose"] == "recorded purpose"
    assert data["steroid_route_category"] == "EPIDURAL"
    assert "EXPOSURE_UNKNOWN" not in data["review_categories"]

    unknown = client.post(
        "/life/api/medical/hospital-os/preop/exposures",
        json={
            "patient_id": "patient-10",
            "drug_name": "unknown-steroid-exposure",
            "ingredient": None,
            "purpose": None,
            "route": None,
            "dose_intensity": None,
            "last_exposure_at": None,
            "verification_state": "UNRESOLVED",
            "review_categories": [],
            "is_steroid_exposure": True,
        },
    )
    assert unknown.status_code == 200, unknown.text
    unknown_data = unknown.json()
    assert "EXPOSURE_UNKNOWN" in unknown_data["review_categories"]
    assert "ingredient" in unknown_data["missing_fields"]
    assert "purpose" in unknown_data["missing_fields"]
    assert "route" in unknown_data["missing_fields"]
