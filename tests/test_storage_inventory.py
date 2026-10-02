import hashlib
import json
import os

from app.services import storage_inventory as inventory


def _write(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_inventory_preserves_files_and_only_reports_approved_evidence(tmp_path):
    name = "confidential-patent-title.docx"
    document = _write(tmp_path, "private_documents/documents/" + name, b"exact document bytes")
    database = _write(tmp_path, "liferecover/accounts.sqlite3", b"secret account contents")
    _write(tmp_path, "private_documents/metadata/private-title.json", b'{"secret":"DO_NOT_LOG"}')
    _write(tmp_path, "classroom_media/class1/clip.mp4", b"media")
    _write(tmp_path, "oauth_private/drive_oauth.edurbk", b"TOKEN_DO_NOT_LOG")
    _write(tmp_path, "backup_activation.json", b'{"enabled":true,"token":"DO_NOT_LOG"}')
    _write(tmp_path, "backup_manifest.json", json.dumps([
        {"source": "classroom_tutor.sqlite3", "asset_id": "DO_NOT_LOG"},
        {"source": "private_documents/documents/" + name},
        {"source": "/credential/DO_NOT_LOG"},
    ]).encode())
    _write(tmp_path, "backup_state/latest.json", json.dumps({
        "status": "verified", "finished_at": "2026-10-02T00:01:00+00:00",
        "verified_assets": 2, "receipt_persisted": True,
        "assets": [{"storage_ref": "DO_NOT_LOG"}], "access_token": "DO_NOT_LOG",
    }).encode())
    before = {p: (p.read_bytes(), p.stat().st_mode) for p in tmp_path.rglob("*") if p.is_file()}

    result = inventory.build_storage_inventory(tmp_path)

    assert result["root_status"] == "present"
    assert result["databases"]["liferecover/accounts.sqlite3"]["bytes"] == database.stat().st_size
    assert result["classroom_media"]["files"] == 1
    assert result["private_document_metadata"]["files"] == 1
    assert result["private_documents"]["hashed_documents"] == [{
        "filename_sha256": hashlib.sha256(name.encode()).hexdigest(),
        "sha256": hashlib.sha256(document.read_bytes()).hexdigest(), "bytes": document.stat().st_size,
    }]
    assert result["backup"]["manifest_entries"] == 3
    assert result["backup"]["registered_source_counts"]["private_documents"] == 1
    assert result["backup"]["activation_enabled"] is True
    assert result["backup"]["drive_oauth_payload_present"] is True
    assert result["backup"]["backup_completeness_verified"] is False
    assert result["backup"]["last_run_reported"]["verified_assets"] == 2
    serialized = json.dumps(result)
    for forbidden in (name, "DO_NOT_LOG", "secret account contents", "exact document bytes", str(tmp_path)):
        assert forbidden not in serialized
    after = {p: (p.read_bytes(), p.stat().st_mode) for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


def test_missing_root_is_not_created(tmp_path):
    root = tmp_path / "absent"
    assert inventory.build_storage_inventory(root)["root_status"] == "missing"
    assert not root.exists()


def test_symlink_ancestors_files_and_fifo_are_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    _write(outside, "documents/real.docx", b"sensitive outside payload")
    root = tmp_path / "root"
    root.mkdir()
    (root / "private_documents").symlink_to(outside, target_is_directory=True)
    (root / "liferecover").symlink_to(outside, target_is_directory=True)
    media = root / "classroom_media"
    media.mkdir()
    (media / "link.docx").symlink_to(outside / "documents/real.docx")
    (media / "subdirectory").symlink_to(outside, target_is_directory=True)
    os.mkfifo(media / "blocking.pipe")
    (root / "backup_manifest.json").symlink_to(outside / "documents/real.docx")

    result = inventory.build_storage_inventory(root)

    assert result["private_documents"]["status"] == "unsafe_or_unreadable"
    assert result["private_documents"]["hashed_documents"] == []
    assert result["databases"]["liferecover/accounts.sqlite3"]["status"] == "unsafe_or_unreadable"
    assert result["classroom_media"]["files"] == 0
    assert result["classroom_media"]["excluded_entries"] == 3
    assert result["backup"]["manifest"]["status"] == "unsafe_or_unreadable"
    link = tmp_path / "linked-root"
    link.symlink_to(root, target_is_directory=True)
    assert inventory.build_storage_inventory(link)["root_status"] == "unsafe_or_unreadable"
    ancestor = tmp_path / "linked-ancestor"
    ancestor.symlink_to(tmp_path, target_is_directory=True)
    assert inventory.build_storage_inventory(ancestor / "root")["root_status"] == "unsafe_or_unreadable"


def test_document_symlink_does_not_produce_content_hash(tmp_path):
    document = _write(tmp_path, "private_documents/documents/a.docx", b"allowed")
    (document.parent / "linked.docx").symlink_to(document)
    result = inventory.build_storage_inventory(tmp_path)["private_documents"]
    assert result["docx_files"] == 1
    assert result["excluded_entries"] == 1
    assert len(result["hashed_documents"]) == 1


def test_private_images_are_hashed_but_unapproved_payload_types_are_not(tmp_path):
    payload = b"user supplied image bytes"
    _write(tmp_path, "private_documents/documents/photo.JPG", payload)
    _write(tmp_path, "private_documents/documents/credentials.json", b"secret")
    result = inventory.build_storage_inventory(tmp_path)["private_documents"]
    assert result["files"] == 2
    assert result["docx_files"] == 0
    assert result["image_files"] == 1
    assert len(result["hashed_documents"]) == 1
    assert result["hashed_documents"][0]["sha256"] == hashlib.sha256(payload).hexdigest()


def test_scan_and_hash_limits_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, "MAX_ENTRIES", 3)
    monkeypatch.setattr(inventory, "MAX_HASH_FILE_BYTES", 4)
    for index in range(8):
        _write(tmp_path, f"private_documents/documents/{index}.docx", b"larger than cap")
    result = inventory.build_storage_inventory(tmp_path)
    assert result["budget_exhausted"] is True
    assert result["private_documents"]["files"] == 3
    assert result["private_documents"]["truncated"] is True
    assert result["private_documents"]["hash_incomplete"] is True
    assert result["private_documents"]["hashed_documents"] == []


def test_total_hash_byte_budget_does_not_affect_file_census(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, "MAX_HASH_TOTAL_BYTES", 5)
    for index in range(3):
        _write(tmp_path, f"private_documents/documents/{index}.docx", b"1234")
    result = inventory.build_storage_inventory(tmp_path)["private_documents"]
    assert result["files"] == 3
    assert len(result["hashed_documents"]) == 1
    assert result["hash_incomplete"] is True
    assert result["truncated"] is False


def test_untrusted_json_fields_cannot_escape_redaction(tmp_path, monkeypatch):
    _write(tmp_path, "backup_state/latest.json", json.dumps({
        "status": ["DO_NOT_LOG"], "finished_at": "DO_NOT_LOG",
        "verified_assets": "DO_NOT_LOG", "skipped_assets": True,
        "failed_assets": -1, "receipt_persisted": "DO_NOT_LOG",
    }).encode())
    _write(tmp_path, "backup_manifest.json", b" " * 2048)
    monkeypatch.setattr(inventory, "MAX_JSON_BYTES", 1024)
    result = inventory.build_storage_inventory(tmp_path)
    assert result["backup"]["last_run_reported"] == {}
    assert result["backup"]["manifest_parseable"] is False
    assert "DO_NOT_LOG" not in json.dumps(result)


def test_expired_budget_performs_no_hashes(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, "TIME_BUDGET_SECONDS", 0)
    _write(tmp_path, "private_documents/documents/one.docx", b"data")
    result = inventory.build_storage_inventory(tmp_path)
    assert result["budget_exhausted"] is True
    assert result["private_documents"]["hashed_documents"] == []
    assert result["private_documents"]["truncated"] is True


def test_startup_log_is_safe_and_does_not_create_root(tmp_path, monkeypatch, caplog):
    root = tmp_path / "absent"
    monkeypatch.setenv("EDURECOVER_CLASSROOM_STATE_DIR", str(root))
    monkeypatch.setenv("EDURECOVER_OWNER_PASSWORD", "OWNER_SECRET_DO_NOT_LOG")
    with caplog.at_level("INFO", logger="uvicorn.error"):
        result = inventory.log_storage_inventory()
    assert result["root_status"] == "missing"
    assert not root.exists()
    assert "storage_inventory=" in caplog.text
    assert str(root) not in caplog.text
    assert "OWNER_SECRET_DO_NOT_LOG" not in caplog.text
