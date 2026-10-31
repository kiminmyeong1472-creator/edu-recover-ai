import base64
import io
import sqlite3

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from PIL import Image

from app.liferecover_runtime import router as runtime_router
from app.platform_core.life_auth import router as auth_router
import app.liferevolve_screen as screens

HEADERS = {"X-LifeRevolve-Action": "screen-import", "Origin": "https://testserver"}


def client_for(monkeypatch, tmp_path, username="screen-user"):
    monkeypatch.setenv("EDURECOVER_CLASSROOM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LIFERECOVER_SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("LIFERECOVER_REGISTRATION_INVITE", "invite-test")
    monkeypatch.setenv("LIFERECOVER_DATA_KEY", "11" * 32)
    monkeypatch.delenv("LIFEREVOLVE_PUBLIC_ORIGIN", raising=False)
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(runtime_router)
    client = TestClient(app, base_url="https://testserver")
    if username:
        response = client.post("/life/auth/register", json={"username": username, "password": "very-secure-password", "invite_code": "invite-test"})
        assert response.status_code == 200, response.text
    return client


def image_bytes(fmt="PNG", size=(60, 120)):
    out = io.BytesIO()
    Image.new("RGB", size, "#ad4f32").save(out, format=fmt)
    return out.getvalue()


def payload(raw=None, name="휴대폰 화면.png"):
    return {"name": name, "base64": base64.b64encode(raw or image_bytes()).decode("ascii")}


def test_original_bytes_encrypted_store_list_download_and_delete(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    raw = image_bytes()
    result = client.post("/life/api/screens", json=payload(raw), headers=HEADERS)
    assert result.status_code == 201, result.text
    item = result.json()["screen"]
    assert item["status"] == "STORED"
    assert item["analysis_status"] == "NOT_RUN"
    assert item["coverage_percent"] is None
    assert item["current_screen"] is False
    assert (item["width"], item["height"], item["byte_size"]) == (60, 120, len(raw))
    rows = client.get("/life/api/screens").json()["screens"]
    assert len(rows) == 1 and "base64" not in rows[0]
    assert rows[0]["name"] == "휴대폰 화면.png"
    assert client.get("/life/api/screens").headers["cache-control"] == "no-store"
    assert client.get("/life/api/resources/life").json()["resources"] == []
    assert client.get(f'/life/api/resources/life/{item["id"]}').status_code == 404
    displayed = client.get(item["image_url"])
    assert displayed.status_code == 200 and displayed.content == raw
    assert displayed.headers["content-type"] == "image/png"
    download = client.get(item["download_url"])
    assert download.content == raw and download.headers["content-disposition"].startswith("attachment;")
    assert base64.b64encode(raw) not in (tmp_path / "liferecover" / "life.sqlite3").read_bytes()
    assert "휴대폰 화면.png".encode() not in (tmp_path / "liferecover" / "life.sqlite3").read_bytes()
    deleted = client.delete(f'/life/api/screens/{item["id"]}', headers=HEADERS)
    assert deleted.status_code == 204
    assert client.get(item["image_url"]).status_code == 404
    assert client.get("/life/api/screens").json()["screens"] == []


def test_other_account_cannot_read_delete_or_list_image(monkeypatch, tmp_path):
    a = client_for(monkeypatch, tmp_path, "owner-one")
    item = a.post("/life/api/screens", json=payload(), headers=HEADERS).json()["screen"]
    b = client_for(monkeypatch, tmp_path, "owner-two")
    assert b.get("/life/api/screens").json()["screens"] == []
    assert b.get(item["image_url"]).status_code == 404
    assert b.get(item["download_url"]).status_code == 404
    assert b.delete(f'/life/api/screens/{item["id"]}', headers=HEADERS).status_code == 404
    assert a.get(item["image_url"]).status_code == 200


def test_auth_and_mutation_origin_header_are_required(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    assert client.post("/life/api/screens", json=payload()).status_code == 403
    assert client.post("/life/api/screens", json=payload(), headers={**HEADERS, "Origin": "https://attacker.invalid"}).status_code == 403
    assert client.post("/life/api/screens", json=payload(), headers={**HEADERS, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    anonymous = client_for(monkeypatch, tmp_path, None)
    assert anonymous.get("/life/api/screens").status_code == 401
    assert anonymous.post("/life/api/screens", json=payload(), headers=HEADERS).status_code == 401
    response = anonymous.get("/life/screen", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/life/auth/login"


@pytest.mark.parametrize("body", [
    {"name": "bad.png", "base64": "not-base64!"},
    {"name": "bad.png", "base64": base64.b64encode(b"<svg><script>alert(1)</script></svg>").decode()},
    {"name": "bad.png", "base64": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"garbage").decode()},
    {"name": "bad.png", "base64": "", "unexpected": 1},
    [],
])
def test_invalid_content_does_not_create_record(monkeypatch, tmp_path, body):
    client = client_for(monkeypatch, tmp_path)
    assert client.post("/life/api/screens", json=body, headers=HEADERS).status_code == 422
    assert client.get("/life/api/screens").json()["screens"] == []


def test_actual_content_controls_type_and_name_cannot_add_headers(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    raw = image_bytes("JPEG")
    result = client.post("/life/api/screens", json=payload(raw, "../../bad\r\nInjected: yes.png"), headers=HEADERS)
    assert result.status_code == 201
    item = result.json()["screen"]
    assert "\r" not in item["name"] and "/" not in item["name"]
    response = client.get(item["download_url"])
    assert response.content == raw and response.headers["content-type"] == "image/jpeg"
    assert response.headers["content-disposition"].endswith('.jpg"')
    assert "injected" not in response.headers


def test_dimensions_size_stream_and_account_quota_limits(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    monkeypatch.setattr(screens, "MAX_DIMENSION", 100)
    assert client.post("/life/api/screens", json=payload(image_bytes(size=(101, 1))), headers=HEADERS).status_code == 413
    monkeypatch.setattr(screens, "MAX_DIMENSION", 8192)
    monkeypatch.setattr(screens, "MAX_IMAGE_BYTES", 16)
    assert client.post("/life/api/screens", json=payload(), headers=HEADERS).status_code == 413
    monkeypatch.setattr(screens, "MAX_IMAGE_BYTES", 8 * 1024 * 1024)
    monkeypatch.setattr(screens, "MAX_BODY_BYTES", 32)
    response = client.post("/life/api/screens", content=iter([b"x" * 20, b"x" * 20]), headers={**HEADERS, "Content-Type": "application/json"})
    assert response.status_code == 413
    monkeypatch.setattr(screens, "MAX_BODY_BYTES", 12 * 1024 * 1024)
    monkeypatch.setattr(screens, "MAX_ACCOUNT_IMAGES", 1)
    assert client.post("/life/api/screens", json=payload(), headers=HEADERS).status_code == 201
    assert client.post("/life/api/screens", json=payload(), headers=HEADERS).status_code == 429
    assert len(client.get("/life/api/screens").json()["screens"]) == 1


def test_failed_encryption_or_global_capacity_rolls_back_metadata(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    monkeypatch.delenv("LIFERECOVER_DATA_KEY")
    assert client.post("/life/api/screens", json=payload(), headers=HEADERS).status_code == 503
    assert client.get("/life/api/screens").json()["screens"] == []
    monkeypatch.setenv("LIFERECOVER_DATA_KEY", "11" * 32)
    monkeypatch.setattr(screens, "MAX_GLOBAL_STORED_BYTES", 1)
    assert client.post("/life/api/screens", json=payload(), headers=HEADERS).status_code == 507
    assert client.get("/life/api/screens").json()["screens"] == []


def test_generic_resource_api_cannot_bypass_screen_store(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    result = client.post("/life/api/resources", json={"domain": "life", "kind": screens.KIND, "payload": payload()})
    assert result.status_code == 403


def test_corrupted_ciphertext_is_not_returned_as_an_image(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    item = client.post("/life/api/screens", json=payload(), headers=HEADERS).json()["screen"]
    with sqlite3.connect(tmp_path / "liferecover" / "life.sqlite3") as conn:
        conn.execute("UPDATE resource SET ciphertext=? WHERE resource_id=?", (b"invalid-ciphertext", item["id"]))
        conn.commit()
    response = client.get(item["image_url"])
    assert response.status_code == 500
    assert response.json()["detail"] == "screen_integrity_failure"


def test_home_and_assets_preserve_existing_destinations(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    response = client.get("/life")
    assert response.status_code == 200
    assert "생활의 혁명" in response.text and "LifeRevolve" in response.text
    for path in ("/life/daily", "/life/medical", "/life/legal", "/life/privacy", "/life/screen"):
        assert path in response.text
    assert "{{AUTH_TEXT}}" not in response.text
    asset = client.get("/life/assets/companion.png")
    assert asset.status_code == 200 and asset.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert client.get("/life/assets/companion.png.b64").status_code == 404
    assert client.get("/life/screen").status_code == 200


def test_public_origin_handles_tls_proxy_and_rejects_malformed_origin(monkeypatch):
    monkeypatch.setenv("LIFEREVOLVE_PUBLIC_ORIGIN", "https://edurecover-prod-production.up.railway.app")

    def request(origin):
        return Request({"type": "http", "scheme": "http", "server": ("internal", 8000), "path": "/", "query_string": b"", "root_path": "", "headers": [(b"host", b"internal:8000"), (b"x-liferevolve-action", b"screen-import"), (b"origin", origin.encode())]})

    screens._mutation_allowed(request("https://edurecover-prod-production.up.railway.app"))
    for origin in ("https://attacker.invalid", "null", "https://[invalid", "https://trusted@edurecover-prod-production.up.railway.app"):
        with pytest.raises(HTTPException) as error:
            screens._mutation_allowed(request(origin))
        assert error.value.status_code == 403
    monkeypatch.setenv("LIFEREVOLVE_PUBLIC_ORIGIN", "https://edurecover-prod-production.up.railway.app/not-an-origin")
    with pytest.raises(HTTPException) as error:
        screens._mutation_allowed(request("https://edurecover-prod-production.up.railway.app"))
    assert error.value.status_code == 503
