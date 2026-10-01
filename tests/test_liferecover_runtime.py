import os

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.platform_core.life_auth import router as auth_router
from app.liferecover_runtime import router as runtime_router


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("EDURECOVER_CLASSROOM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LIFERECOVER_SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("LIFERECOVER_REGISTRATION_INVITE", "invite-test")
    monkeypatch.setenv("LIFERECOVER_DATA_KEY", "11" * 32)
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(runtime_router)
    return TestClient(app)


def test_register_login_and_encrypted_domain_storage(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    registered = client.post(
        "/life/auth/register",
        json={"username":"tester","password":"very-secure-password","invite_code":"invite-test"},
    )
    assert registered.status_code == 200, registered.text

    saved = client.post(
        "/life/api/resources",
        json={"domain":"medical","kind":"medication","payload":{"name":"sample","purpose":"test"}},
    )
    assert saved.status_code == 200, saved.text

    listed = client.get("/life/api/resources/medical")
    assert listed.status_code == 200, listed.text
    assert listed.json()["resources"][0]["payload"]["purpose"] == "test"

    db_bytes = (tmp_path / "liferecover" / "medical.sqlite3").read_bytes()
    assert b'"purpose":"test"' not in db_bytes


def test_domain_isolation_by_account(monkeypatch, tmp_path):
    client_a = _client(monkeypatch, tmp_path)
    assert client_a.post(
        "/life/auth/register",
        json={"username":"alpha","password":"very-secure-password","invite_code":"invite-test"},
    ).status_code == 200
    saved = client_a.post(
        "/life/api/resources",
        json={"domain":"legal","kind":"case","payload":{"title":"private"}},
    )
    rid = saved.json()["resource_id"]

    client_b = _client(monkeypatch, tmp_path)
    assert client_b.post(
        "/life/auth/register",
        json={"username":"beta","password":"another-secure-password","invite_code":"invite-test"},
    ).status_code == 200
    hidden = client_b.get(f"/life/api/resources/legal/{rid}")
    assert hidden.status_code == 404


def test_missing_data_key_fails_closed(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post(
        "/life/auth/register",
        json={"username":"gamma","password":"very-secure-password","invite_code":"invite-test"},
    ).status_code == 200
    monkeypatch.delenv("LIFERECOVER_DATA_KEY")
    result = client.post(
        "/life/api/resources",
        json={"domain":"medical","kind":"medication","payload":{"name":"sample"}},
    )
    assert result.status_code == 503
