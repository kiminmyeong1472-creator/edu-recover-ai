import base64
import hashlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import liferevolve_release as release


def _client(monkeypatch, tmp_path, data=b"PKverified-test-fixture"):
    monkeypatch.setattr(release, "RELEASE_DIR", tmp_path)
    release._verified_release.cache_clear()
    if data is not None:
        (tmp_path / "LifeRevolve.apk.b64").write_bytes(base64.b64encode(data))
        (tmp_path / "manifest.json").write_text(json.dumps({
            "version_name": "0.3.0", "version_code": 1,
            "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        }), encoding="utf-8")
    app = FastAPI()
    app.include_router(release.router)
    return TestClient(app)


def test_exact_release_download_and_version(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.get("/life/android.apk")
    assert response.status_code == 200
    assert response.content == b"PKverified-test-fixture"
    assert response.headers["content-type"] == "application/vnd.android.package-archive"
    assert "attachment" in response.headers["content-disposition"]
    manifest = client.get("/life/app-version").json()
    assert manifest["sha256"] == hashlib.sha256(response.content).hexdigest()
    assert manifest["download_url"] == "/life/android.apk"


def test_missing_release_is_not_a_download_success(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, None)
    response = client.get("/life/android.apk")
    assert response.status_code == 503
    assert response.json()["detail"] == "android_release_not_ready"


def test_corrupted_release_fails_closed(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "LifeRevolve.apk.b64").write_bytes(base64.b64encode(b"PKverified-test-Fixture"))
    response = client.get("/life/android.apk")
    assert response.status_code == 503
    assert response.json()["detail"] == "android_release_integrity_failure"
