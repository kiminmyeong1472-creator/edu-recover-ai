import base64
import hashlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import private_document_store as store


# Fixed 1x1 images produced by an independent image encoder, not the validators.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNIqZgGAAK2AXPg3BD1AAAAAElFTkSuQmCC"
)
_JPEG = base64.b64decode(
    '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nI'
    'CIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMj'
    'IyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQE'
    'AAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEI'
    'I0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4e'
    'XqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6e'
    'rx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ'
    '3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZH'
    'SElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6w'
    'sPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwCpRRRXoHIf/9k='
)


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


@pytest.mark.parametrize("name,content,mime", [
    ("사진.jpg", _JPEG, "image/jpeg"),
    ("사진.JPEG", _JPEG, "image/jpeg"),
    ("사진.png", _PNG, "image/png"),
])
def test_private_photo_roundtrip_requires_owner_and_preserves_bytes(monkeypatch, tmp_path, name, content, mime):
    client = _client(monkeypatch, tmp_path)
    digest = hashlib.sha256(content).hexdigest()
    upload = dict(
        params={"filename": name, "sha256": digest}, content=content,
        headers={"Content-Type": mime},
    )
    assert client.post("/owner/private-documents", **upload).status_code == 401
    assert client.post("/owner/private-documents", auth=("owner", "wrong"), **upload).status_code == 401
    first = client.post("/owner/private-documents", auth=("owner", "secret"), **upload)
    assert first.status_code == 200
    assert first.json()["idempotent"] is False
    second = client.post("/owner/private-documents", auth=("owner", "secret"), **upload)
    assert second.status_code == 200
    assert second.json()["idempotent"] is True

    path = f"/owner/private-documents/{name}"
    denied = client.get(path, follow_redirects=False)
    assert denied.status_code == 303
    assert denied.headers["location"] == "/owner/login"
    downloaded = client.get(path, auth=("owner", "secret"))
    assert downloaded.content == content
    assert downloaded.headers["content-type"] == mime
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["x-document-sha256"] == digest
    listing = client.get("/owner/private-documents", auth=("owner", "secret")).json()
    assert listing["count"] == 1
    assert listing["documents"][0]["filename"] == name
    assert store.private_document_integrity_snapshot()["documents"] == [
        {"filename": name, "sha256": digest, "size": len(content)}
    ]


@pytest.mark.parametrize("name,content,mime,status", [
    ("payload.exe", _PNG, "application/octet-stream", 400),
    ("payload.svg", b"<svg/>", "application/octet-stream", 400),
    ("fake.jpg", b"\xff\xd8not a JPEG\xff\xd9", "image/jpeg", 422),
    ("fake.png", b"\x89PNG\r\n\x1a\nnot a PNG", "image/png", 422),
    ("wrong.jpg", _PNG, "image/jpeg", 422),
    ("wrong.png", _JPEG, "image/png", 422),
    ("wrong.png", _PNG, "text/html", 415),
    ("broken.png", _PNG[:-1], "image/png", 422),
    ("trailing.png", _PNG + b"<script>alert(1)</script>", "image/png", 422),
    ("broken.jpg", _JPEG[:-1], "image/jpeg", 422),
])
def test_rejects_unsupported_or_malformed_photos(monkeypatch, tmp_path, name, content, mime, status):
    client = _client(monkeypatch, tmp_path)
    response = client.post(
        "/owner/private-documents",
        params={"filename": name, "sha256": hashlib.sha256(content).hexdigest()},
        content=content, headers={"Content-Type": mime}, auth=("owner", "secret"),
    )
    assert response.status_code == status
    assert store.private_document_integrity_snapshot()["count"] == 0


def test_photo_import_is_idempotent_and_cannot_replace_existing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("EDURECOVER_CLASSROOM_STATE_DIR", str(tmp_path))
    manifest = [{
        "url": "https://files.oaiusercontent.com/signed-photo",
        "filename": "Original.jpg", "sha256": hashlib.sha256(_JPEG).hexdigest(), "size": len(_JPEG),
    }]
    monkeypatch.setenv("EDURECOVER_DOCUMENT_IMPORT_JSON", json.dumps(manifest))
    calls = []

    def fake_download(url, temporary, expected_size):
        calls.append(url)
        temporary.write_bytes(_JPEG)

    monkeypatch.setattr(store, "_download_import", fake_download)
    assert store._import_from_env_sync() == {"requested": 1, "stored": 1, "idempotent": 0, "failed": 0}
    assert store._import_from_env_sync() == {"requested": 1, "stored": 0, "idempotent": 1, "failed": 0}
    assert len(calls) == 1
    manifest[0]["sha256"] = "0" * 64
    monkeypatch.setenv("EDURECOVER_DOCUMENT_IMPORT_JSON", json.dumps(manifest))
    assert store._import_from_env_sync() == {"requested": 1, "stored": 0, "idempotent": 0, "failed": 1}
    assert (tmp_path / "private_documents" / "documents" / "Original.jpg").read_bytes() == _JPEG
    assert len(calls) == 1


def test_photo_upload_respects_byte_limit_and_digest(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("EDURECOVER_PRIVATE_DOCX_MAX_BYTES", str(len(_PNG) - 1))
    upload = dict(params={"filename": "photo.png", "sha256": hashlib.sha256(_PNG).hexdigest()},
                  content=_PNG, headers={"Content-Type": "image/png"}, auth=("owner", "secret"))
    assert client.post("/owner/private-documents", **upload).status_code == 413
    monkeypatch.setenv("EDURECOVER_PRIVATE_DOCX_MAX_BYTES", str(len(_PNG)))
    upload["params"]["sha256"] = "0" * 64
    assert client.post("/owner/private-documents", **upload).status_code == 422
    assert store.private_document_integrity_snapshot()["count"] == 0


def test_photo_upload_cannot_overwrite_a_different_original(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    different = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"
    )
    for content, expected_status in [(_PNG, 200), (different, 409)]:
        response = client.post(
            "/owner/private-documents",
            params={"filename": "Original.png", "sha256": hashlib.sha256(content).hexdigest()},
            content=content, headers={"Content-Type": "image/png"}, auth=("owner", "secret"),
        )
        assert response.status_code == expected_status
    assert (tmp_path / "private_documents" / "documents" / "Original.png").read_bytes() == _PNG
