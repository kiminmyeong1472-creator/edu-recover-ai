import hashlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import private_document_store as store


def _docx_bytes(text: str = "exact bytes") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("_rels/.rels", "<Relationships/>")
        archive.writestr("word/document.xml", f"<document>{text}</document>")
    return output.getvalue()


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("EDURECOVER_CLASSROOM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("EDURECOVER_OWNER_USER", "owner")
    monkeypatch.setenv("EDURECOVER_OWNER_PASSWORD", "secret")
    return TestClient(app)


def test_owner_upload_list_download_preserves_exact_bytes(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    content = _docx_bytes("원본 그대로")
    digest = hashlib.sha256(content).hexdigest()
    response = client.post(
        "/owner/private-documents",
        params={"filename": "원본 명세서.docx", "sha256": digest},
        content=content,
        headers={"Content-Type": "application/octet-stream"},
        auth=("owner", "secret"),
    )
    assert response.status_code == 200
    assert response.json()["sha256"] == digest
    stored = tmp_path / "private_documents" / "documents" / "원본 명세서.docx"
    assert stored.read_bytes() == content
    assert stored.stat().st_mode & 0o777 == 0o600

    listing = client.get("/owner/private-documents", auth=("owner", "secret"))
    assert listing.status_code == 200
    assert listing.json()["documents"][0]["sha256"] == digest

    downloaded = client.get("/owner/private-documents/원본 명세서.docx", auth=("owner", "secret"))
    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert downloaded.headers["x-document-sha256"] == digest


def test_upload_rejects_bad_hash_and_traversal(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    content = _docx_bytes()
    response = client.post(
        "/owner/private-documents",
        params={"filename": "safe.docx", "sha256": "0" * 64},
        content=content,
        headers={"Content-Type": "application/octet-stream"},
        auth=("owner", "secret"),
    )
    assert response.status_code == 422
    assert not (tmp_path / "private_documents" / "documents" / "safe.docx").exists()
    with pytest.raises(store.PrivateDocumentError, match="INVALID_FILENAME"):
        store.sanitize_filename("../escape.docx")


def test_startup_import_manifest_is_exact_and_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("EDURECOVER_CLASSROOM_STATE_DIR", str(tmp_path))
    content = _docx_bytes("startup import")
    digest = hashlib.sha256(content).hexdigest()
    manifest = [{
        "url": "https://files.oaiusercontent.com/signed-document",
        "filename": "Imported.docx",
        "sha256": digest,
        "size": len(content),
    }]
    monkeypatch.setenv("EDURECOVER_DOCUMENT_IMPORT_JSON", json.dumps(manifest))

    def fake_download(url, temporary, expected_size):
        assert "signed-document" in url
        assert expected_size == len(content)
        temporary.write_bytes(content)

    monkeypatch.setattr(store, "_download_import", fake_download)
    first = store._import_from_env_sync()
    second = store._import_from_env_sync()
    assert first == {"requested": 1, "stored": 1, "idempotent": 0, "failed": 0}
    assert second == {"requested": 1, "stored": 0, "idempotent": 1, "failed": 0}
    assert (tmp_path / "private_documents" / "documents" / "Imported.docx").read_bytes() == content
