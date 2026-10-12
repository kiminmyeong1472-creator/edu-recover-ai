"""Real local encryption/restore and fake HTTP destination boundary tests."""
import fcntl
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.encrypted_backup import decrypt_file
from app.training.backup_adapters import (
    BackupStorageError, DriveBackupAdapter, EncryptedPayloads, ServerBackupAdapter,
)
from app.training.backup_service import (
    RegisteredAsset, _run_assets, _safe_asset, get_backup_service_status, run_configured_backup_once,
)
from app.training.backup_inventory import ACCOUNT_DATABASE, discover_runtime_assets
from app.training.trade_secret_backup_runner import BackupCycleError, execute_backup_cycle
from app.training.trade_secret_engine import AssetKind, BackupTarget, SecretAsset, classify_secret_asset


class Response:
    def __init__(self, status=200, data=None, headers=None, body=b""):
        self.status_code, self.data = status, data
        self.headers, self.body = headers or {}, body

    def json(self):
        return self.data

    def iter_content(self, _):
        yield self.body

    def close(self):
        pass


class FakeDrive:
    def __init__(self):
        self.uploaded = b""
        self.calls = []
        self.public = self.corrupt = self.my_drive = self.bad_location = False
        self.fail_upload = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        if url.endswith("/permissions"):
            return Response(data={"permissions": [{"type": "anyone" if self.public else "user"}]})
        if url.endswith("/folder"):
            return Response(data={"id": "folder", "mimeType": "application/vnd.google-apps.folder",
                                  "trashed": False, "driveId": None if self.my_drive else "shared",
                                  "capabilities": {"canAddChildren": True}})
        if method == "POST":
            location = "https://attacker.invalid/upload" if self.bad_location else "https://www.googleapis.com/upload/drive/v3/files?upload_id=test"
            return Response(headers={"Location": location})
        if method == "PUT":
            if self.fail_upload:
                raise RuntimeError("SECRET_MUST_NOT_APPEAR_IN_RECEIPT")
            self.uploaded += kwargs["data"]
            return Response(data={"id": "ciphertext-file"})
        if kwargs.get("params", {}).get("alt") == "media":
            return Response(body=self.uploaded + (b"corrupt" if self.corrupt else b""))
        return Response(data={"id": "ciphertext-file", "parents": ["folder"],
                              "trashed": False, "size": str(len(self.uploaded))})


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.delenv("EDURECOVER_BACKUP_OWNER_PASSPHRASE", raising=False)
    # Synthetic test-only key, never a deployment credential.
    monkeypatch.setenv("EDURECOVER_BACKUP_MASTER_KEY", "hex:" + "11" * 32)
    for folder in ("source", "server", "state"):
        (tmp_path / folder).mkdir(mode=0o700)
    source = tmp_path / "source" / "lesson.txt"
    source.write_bytes(b"private teaching material")
    payloads = EncryptedPayloads(source_root=source.parent, staging_root=tmp_path / "state")
    server = ServerBackupAdapter(tmp_path / "server", payloads)
    http = FakeDrive()
    drive = DriveBackupAdapter(session=http, folders={
        BackupTarget.DRIVE_TRADE_SECRET: "folder", BackupTarget.DRIVE_QUARANTINE: "folder"}, payloads=payloads)
    item = SecretAsset(asset_id="lesson", kind=AssetKind.UPLOAD,
                       content_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
                       provenance_complete=True, integrity_verified=True, owner_marked_trade_secret=True)
    yield tmp_path, source, item, payloads, server, drive, http
    payloads.close()


def cycle(storage, item=None):
    root, source, default, _, server, drive, _ = storage
    item = item or default
    decision = classify_secret_asset(item)
    return execute_backup_cycle(asset=item, decision=decision,
                                adapters={t: server if t.value.startswith("SERVER") else drive
                                          for t in decision.backup_targets},
                                source_ref=str(source), last_completed_at=None)


def test_server_and_drive_ciphertext_identical_and_restore_verified(storage):
    root, source, item, _, _, _, http = storage
    receipt = cycle(storage)
    assert receipt.complete and len(receipt.writes) == 2
    assert all(w.encrypted and w.restore_verified and w.integrity_verified for w in receipt.writes)
    assert not any(w.raw_content_written for w in receipt.writes)
    stored = Path(receipt.writes[0].storage_ref)
    assert stored.read_bytes() == http.uploaded
    assert source.read_bytes() not in http.uploaded
    assert not stored.stat().st_mode & 0o077
    result = decrypt_file(stored, root / "restored.txt")
    assert result["decrypted_sha256"] == item.content_hash
    assert (root / "restored.txt").read_bytes() == source.read_bytes()


@pytest.mark.parametrize("owner_password", [False, True])
def test_account_backup_restores_login_and_original_resource_ownership(storage, monkeypatch, owner_password):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.platform_core.life_auth import router as auth_router
    from app.liferecover_runtime import router as runtime_router

    root = storage[0]
    source_root = root / "source"
    monkeypatch.setenv("EDURECOVER_CLASSROOM_STATE_DIR", str(source_root))
    monkeypatch.setenv("LIFERECOVER_SESSION_SECRET", "synthetic-session-secret-" * 3)
    monkeypatch.setenv("LIFERECOVER_REGISTRATION_INVITE", "synthetic-invite")
    monkeypatch.setenv("LIFERECOVER_DATA_KEY", "22" * 32)
    if owner_password:
        monkeypatch.setenv("EDURECOVER_BACKUP_OWNER_PASSPHRASE", "synthetic recovery password")
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(runtime_router)
    account_ids, resource_ids = {}, {}
    for name in ("original-user", "other-user"):
        with TestClient(app, base_url="https://testserver") as client:
            registered = client.post("/life/auth/register", json={
                "username": name, "password": "synthetic-long-password", "invite_code": "synthetic-invite"})
            assert registered.status_code == 200
            account_ids[name] = registered.json()["account_id"]
            saved = client.post("/life/api/resources", json={
                "domain": "life", "kind": "note", "payload": {"owner_marker": name}})
            assert saved.status_code == 200
            resource_ids[name] = saved.json()["resource_id"]

    inventory = discover_runtime_assets(source_root)
    assert inventory.summary()["account_database_discovered"]
    assert not inventory.issues
    restored_root = root / "restored"
    (restored_root / "liferecover").mkdir(mode=0o700, parents=True)
    for entry in inventory.assets:
        http = FakeDrive()
        def factory(*, payloads):
            return DriveBackupAdapter(session=http, folders={
                BackupTarget.DRIVE_RESTRICTED_PERSONAL: "folder"}, payloads=payloads)
        result = _run_assets(manifest=[RegisteredAsset.model_validate(entry)],
                             source_root=source_root, server_root=root / "server",
                             state_dir=root / "state", now=datetime.now(timezone.utc),
                             drive_factory=factory)
        assert result["status"] == "verified", result
        writes = result["assets"][0]["writes"]
        assert {w["target"] for w in writes} == {
            "SERVER_RESTRICTED_PERSONAL", "DRIVE_RESTRICTED_PERSONAL"}
        assert all(w["encrypted"] and w["restore_verified"] and not w["metadata_only"] for w in writes)
        ciphertext = Path(writes[0]["storage_ref"])
        assert ciphertext.read_bytes() == http.uploaded
        assert b"original-user" not in http.uploaded
        assert b"SQLite format 3" not in http.uploaded
        decrypt_file(ciphertext, restored_root / entry["source"])

    # New sessions are issued after restoration; no deployment key is copied.
    monkeypatch.setenv("EDURECOVER_CLASSROOM_STATE_DIR", str(restored_root))
    monkeypatch.setenv("LIFERECOVER_SESSION_SECRET", "rotated-synthetic-secret-" * 3)
    with TestClient(app, base_url="https://testserver") as client:
        login = client.post("/life/auth/login", json={
            "username": "original-user", "password": "synthetic-long-password"})
        assert login.status_code == 200
        assert login.json()["account_id"] == account_ids["original-user"]
        own = client.get("/life/api/resources/life/" + resource_ids["original-user"])
        assert own.status_code == 200
        assert own.json()["payload"]["owner_marker"] == "original-user"
        assert client.get("/life/api/resources/life/" + resource_ids["other-user"]).status_code == 404
        assert client.post("/life/auth/login", json={
            "username": "original-user", "password": "wrong-password"}).status_code == 401


def test_manual_account_entry_cannot_select_public_or_general_backup():
    asset = _safe_asset(RegisteredAsset(asset_id="account", source=ACCOUNT_DATABASE, kind="OTHER",
                                       explicit_public_release=True, deidentified=True), "a" * 64)
    decision = classify_secret_asset(asset)
    assert decision.raw_content_backup_allowed
    assert not decision.external_provider_delivery_allowed
    assert set(decision.backup_targets) == {
        BackupTarget.SERVER_RESTRICTED_PERSONAL, BackupTarget.DRIVE_RESTRICTED_PERSONAL}


def test_metadata_only_account_entry_is_failure_not_success(storage):
    root = storage[0]
    http = FakeDrive()
    def factory(*, payloads):
        return DriveBackupAdapter(session=http, folders={BackupTarget.DRIVE_QUARANTINE: "folder"}, payloads=payloads)
    result = _run_assets(manifest=[RegisteredAsset(asset_id="account", source=ACCOUNT_DATABASE,
                                                  kind="CONFIGURATION", contains_credentials=True)],
                         source_root=root / "source", server_root=root / "server", state_dir=root / "state",
                         now=datetime.now(timezone.utc), drive_factory=factory)
    assert result["status"] == "failed" and result["verified_assets"] == 0
    assert result["assets"][0]["failure_code"] == "ACCOUNT_DATABASE_CONTENT_BACKUP_REQUIRED"
    assert not http.calls
    assert not list((root / "server").rglob("*.edurbk"))


def test_account_backup_without_encryption_key_never_uploads(storage, monkeypatch):
    root = storage[0]
    path = root / "source" / ACCOUNT_DATABASE
    path.parent.mkdir(mode=0o700)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE account (account_id TEXT)")
        db.execute("INSERT INTO account VALUES ('synthetic-account')")
    monkeypatch.delenv("EDURECOVER_BACKUP_MASTER_KEY")
    http = FakeDrive()
    def factory(*, payloads):
        return DriveBackupAdapter(session=http, folders={
            BackupTarget.DRIVE_RESTRICTED_PERSONAL: "folder"}, payloads=payloads)
    result = _run_assets(manifest=[RegisteredAsset(asset_id="account", source=ACCOUNT_DATABASE,
                                                  kind="CONFIGURATION")],
                         source_root=root / "source", server_root=root / "server", state_dir=root / "state",
                         now=datetime.now(timezone.utc), drive_factory=factory)
    assert result["status"] == "failed" and result["verified_assets"] == 0
    assert not any(method in {"POST", "PUT"} for method, _ in http.calls)
    assert not list((root / "server").rglob("*.edurbk"))


@pytest.mark.parametrize("flag", ["public", "my_drive"])
def test_invalid_drive_destination_blocks_all_writes(storage, flag):
    setattr(storage[-1], flag, True)
    with pytest.raises(BackupCycleError) as failure:
        cycle(storage)
    assert failure.value.receipt.failure_stage == "preflight"
    assert failure.value.receipt.writes == ()
    assert list((storage[0] / "server").iterdir()) == []
    assert not any(method == "POST" for method, _ in storage[-1].calls)


def test_corrupt_drive_readback_is_partial_failure(storage):
    storage[-1].corrupt = True
    with pytest.raises(BackupCycleError) as failure:
        cycle(storage)
    receipt = failure.value.receipt
    assert not receipt.complete and len(receipt.writes) == 1
    assert receipt.writes[0].target == BackupTarget.SERVER_PRIVATE


def test_http_exception_secret_not_copied_to_failure(storage):
    storage[-1].fail_upload = True
    with pytest.raises(BackupCycleError) as failure:
        cycle(storage)
    assert "SECRET_MUST_NOT_APPEAR" not in str(failure.value)
    assert "SECRET_MUST_NOT_APPEAR" not in failure.value.receipt.model_dump_json()


def test_upload_location_cannot_redirect_authenticated_session(storage):
    storage[-1].bad_location = True
    with pytest.raises(BackupCycleError):
        cycle(storage)
    assert not any("attacker.invalid" in url for _, url in storage[-1].calls)


def test_hash_mismatch_never_publishes_ciphertext(storage):
    item = storage[2].model_copy(update={"content_hash": "f" * 64})
    with pytest.raises(BackupCycleError):
        cycle(storage, item)
    assert not list((storage[0] / "server").rglob("*.edurbk"))
    assert not any(method == "POST" for method, _ in storage[-1].calls)


def test_credential_asset_never_reads_source_and_preserves_metadata_only(storage):
    storage[1].unlink()
    item = storage[2].model_copy(update={"contains_credentials": True})
    receipt = cycle(storage, item)
    assert receipt.complete and all(w.metadata_only for w in receipt.writes)
    restored = storage[0] / "metadata-restored.json"
    decrypt_file(receipt.writes[0].storage_ref, restored)
    metadata = json.loads(restored.read_text())
    assert metadata["source_content_omitted"] is True
    assert "content_hash" not in metadata and "source_ref" not in metadata


def test_missing_drive_adapter_prevents_partial_server_write(storage):
    item = storage[2]
    with pytest.raises(BackupCycleError):
        execute_backup_cycle(asset=item, decision=classify_secret_asset(item),
                             adapters={BackupTarget.SERVER_PRIVATE: storage[4]},
                             source_ref=str(storage[1]), last_completed_at=None)
    assert list((storage[0] / "server").iterdir()) == []


def test_server_symlink_directory_rejected(storage):
    root = storage[0]
    (root / "server" / "server_private").symlink_to(root / "source", target_is_directory=True)
    with pytest.raises(BackupCycleError) as failure:
        cycle(storage)
    assert failure.value.receipt.failure_stage == "preflight"
    assert list((root / "source").glob("*.edurbk")) == []


def test_snapshot_scheduler_interval_and_revision_change(storage):
    root = storage[0]
    def drive_factory(*, payloads):
        return DriveBackupAdapter(session=FakeDrive(), folders={BackupTarget.DRIVE_TRADE_SECRET: "folder"}, payloads=payloads)
    now = datetime.now(timezone.utc)
    kwargs = dict(manifest=[RegisteredAsset(asset_id="lesson", source="lesson.txt", kind=AssetKind.PRODUCT_DESIGN,
                                          provenance_complete=True)],
                  source_root=root / "source", server_root=root / "server", state_dir=root / "state",
                  drive_factory=drive_factory)
    first = _run_assets(now=now, **kwargs)
    assert first["status"] == "verified"
    second = _run_assets(now=now + timedelta(minutes=5), **kwargs)
    assert second["status"] == "not_due" and second["skipped_assets"] == 1
    storage[1].write_text("changed content")
    third = _run_assets(now=now + timedelta(minutes=6), **kwargs)
    assert third["status"] == "verified"


def test_sqlite_online_snapshot_and_source_traversal(storage):
    root = storage[0]
    with sqlite3.connect(root / "source" / "tutor.sqlite3") as db:
        db.execute("CREATE TABLE records (id INTEGER)")
        db.execute("INSERT INTO records VALUES (42)")
    def drive_factory(*, payloads):
        return DriveBackupAdapter(session=FakeDrive(), folders={BackupTarget.DRIVE_TRADE_SECRET: "folder"}, payloads=payloads)
    result = _run_assets(manifest=[RegisteredAsset(asset_id="db", source="tutor.sqlite3", kind=AssetKind.PRODUCT_DESIGN,
                                                 sqlite_snapshot=True, provenance_complete=True),
                                   RegisteredAsset(asset_id="escape", source="../elsewhere", kind=AssetKind.UPLOAD)],
                         source_root=root / "source", server_root=root / "server", state_dir=root / "state",
                         now=datetime.now(timezone.utc), drive_factory=drive_factory)
    assert result["status"] == "failed" and result["verified_assets"] == 1 and result["failed_assets"] == 1
    first = result["assets"][0]["writes"][0]
    restored = root / "restored.sqlite3"
    decrypt_file(first["storage_ref"], restored)
    with sqlite3.connect(restored) as db:
        assert db.execute("SELECT id FROM records").fetchone() == (42,)


def test_unconfigured_service_blocks_and_records_failure_without_credentials(storage, monkeypatch):
    monkeypatch.setenv("EDURECOVER_BACKUP_STATE_DIR", str(storage[0] / "state"))
    monkeypatch.delenv("EDURECOVER_BACKUP_ENABLED", raising=False)
    result = run_configured_backup_once()
    assert result["status"] == "blocked" and result["receipt_persisted"]
    assert "BACKUP_DISABLED" in result["blockers"]
    assert get_backup_service_status()["last_run"]["status"] == "blocked"


def test_busy_worker_does_not_overwrite_receipt(storage, monkeypatch):
    state = storage[0] / "state"
    monkeypatch.setenv("EDURECOVER_BACKUP_STATE_DIR", str(state))
    latest = state / "latest.json"
    latest.write_text('{"status":"previous"}')
    with (state / ".backup.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = run_configured_backup_once()
    assert result["status"] == "busy" and not result["receipt_persisted"]
    assert json.loads(latest.read_text())["status"] == "previous"


def test_production_container_storage_cannot_claim_durability(storage, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    status = get_backup_service_status()
    assert not status["configuration_ready"]
    assert "PERSISTENT_SOURCE_SERVER_AND_STATE_VOLUME_REQUIRED" in status["blockers"]


def test_configured_entrypoint_persists_real_verified_receipts(storage, monkeypatch):
    root = storage[0]
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps([{"asset_id": "lesson", "source": "lesson.txt",
                                     "kind": "PRODUCT_DESIGN", "provenance_complete": True}]))
    settings = {
        "ENVIRONMENT": "test", "EDURECOVER_BACKUP_ENABLED": "true",
        "EDURECOVER_BACKUP_MANIFEST": str(manifest),
        "EDURECOVER_BACKUP_SOURCE_ROOT": str(root / "source"),
        "EDURECOVER_BACKUP_SERVER_ROOT": str(root / "server"),
        "EDURECOVER_BACKUP_STATE_DIR": str(root / "state"),
        "EDURECOVER_BACKUP_DRIVE_SERVICE_ACCOUNT_JSON": "test-fixture-not-a-credential",
        "EDURECOVER_BACKUP_DRIVE_FOLDERS_JSON": '{"DRIVE_TRADE_SECRET":"folder"}',
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("RAILWAY_SERVICE_ID", raising=False)
    monkeypatch.delenv("EDURECOVER_BACKUP_DRIVE_SERVICE_ACCOUNT_FILE", raising=False)
    monkeypatch.setattr(DriveBackupAdapter, "from_environment", lambda *, payloads:
                        DriveBackupAdapter(session=FakeDrive(), folders={BackupTarget.DRIVE_TRADE_SECRET: "folder"}, payloads=payloads))
    result = run_configured_backup_once()
    assert result["status"] == "verified" and result["receipt_persisted"]
    assert result["verified_assets"] == 1
    latest = json.loads((root / "state" / "latest.json").read_text())
    assert len(latest["assets"][0]["writes"]) == 2
    assert len(list((root / "state" / "receipts").glob("*.json"))) == 1
    assert run_configured_backup_once()["status"] == "not_due"
