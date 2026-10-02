"""Explicitly configured 30-minute backup worker and owner-safe status.

Usage: python -m app.training.backup_service --once
       python -m app.training.backup_service --status
No scheduler, credentials, folders or infrastructure are created automatically.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.encrypted_backup import backup_encryption_status
from app.training.backup_adapters import (
    BackupStorageError, DriveBackupAdapter, EncryptedPayloads,
    ServerBackupAdapter, fsync_directory, private_directory, sha256_file,
)
from app.training.trade_secret_backup_runner import BackupCycleError, execute_backup_cycle
from app.training.trade_secret_engine import AssetKind, SecretAsset, classify_secret_asset
from app.training.backup_inventory import (
    MAX_RUNTIME_FILES, MAX_RUNTIME_FILE_BYTES, MAX_RUNTIME_TOTAL_BYTES,
    checked_source_path, credential_source_reason, discover_runtime_assets, merge_runtime_assets,
)

ENV = {
    "manifest": "EDURECOVER_BACKUP_MANIFEST",
    "source_root": "EDURECOVER_BACKUP_SOURCE_ROOT",
    "server_root": "EDURECOVER_BACKUP_SERVER_ROOT",
    "state_dir": "EDURECOVER_BACKUP_STATE_DIR",
}


class RegisteredAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1)
    kind: AssetKind
    contains_credentials: bool = False
    contains_personal_data: bool = False
    deidentified: bool = False
    owner_marked_trade_secret: bool = False
    unfiled_patent_material: bool = False
    product_strategy_or_internal_policy: bool = False
    training_policy_secret: bool = False
    chat_contains_secret_context: bool = False
    explicit_public_release: bool = False
    provenance_complete: bool = False
    sqlite_snapshot: bool = False
    expected_sha256: str | None = None


def atomic_json(path: Path, value: Any) -> None:
    private_directory(path.parent)
    if path.is_symlink():
        raise BackupStorageError("STATE_SYMLINK_FORBIDDEN")
    fd, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _is_enabled() -> bool:
    return os.getenv("EDURECOVER_BACKUP_ENABLED", "").lower() in {"true", "1", "yes"}


def get_backup_service_status() -> dict[str, Any]:
    """No cloud call and no secret values: configuration is not a successful backup."""
    blockers = []
    if not _is_enabled():
        blockers.append("BACKUP_DISABLED")
    for env in ENV.values():
        if not os.getenv(env):
            blockers.append("MISSING_" + env)
    encryption_ready = backup_encryption_status()["ready"]
    if not encryption_ready:
        blockers.append("BACKUP_ENCRYPTION_NOT_READY")
    credential_sources = sum(bool(os.getenv(name)) for name in (
        "EDURECOVER_BACKUP_DRIVE_SERVICE_ACCOUNT_FILE",
        "EDURECOVER_BACKUP_DRIVE_SERVICE_ACCOUNT_JSON"))
    from app.training.drive_oauth import oauth_configured, client_configured
    oauth = oauth_configured()
    if credential_sources + int(oauth) != 1:
        blockers.append("EXPLICIT_DRIVE_AUTHORIZATION_REQUIRED")
    if oauth and not client_configured():
        blockers.append("DRIVE_OAUTH_CLIENT_NOT_CONFIGURED")
    if not oauth and not os.getenv("EDURECOVER_BACKUP_DRIVE_FOLDERS_JSON"):
        blockers.append("DRIVE_DESTINATIONS_NOT_CONFIGURED")
    # A /tmp or container-layer backup is never advertised as durable production storage.
    production = bool(os.getenv("RAILWAY_SERVICE_ID")) or os.getenv("ENVIRONMENT", "").lower() == "production"
    durable = False
    mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if mount:
        mount_path = Path(mount)
        durable = mount_path.is_mount() and mount_path.resolve() != Path("/")
        if durable:
            durable = all(os.getenv(ENV[k]) and Path(os.environ[ENV[k]]).resolve().is_relative_to(mount_path.resolve())
                          for k in ("server_root", "state_dir", "source_root"))
    if production and not durable:
        blockers.append("PERSISTENT_SOURCE_SERVER_AND_STATE_VOLUME_REQUIRED")
    for key in ("source_root", "server_root", "state_dir"):
        raw = os.getenv(ENV[key])
        if raw:
            path = Path(raw)
            try:
                if key == "source_root":
                    if not path.is_dir() or path.is_symlink():
                        raise ValueError()
                else:
                    private_directory(path)
            except (OSError, ValueError, BackupStorageError):
                blockers.append(key.upper() + "_DIRECTORY_NOT_READY")
    manifest = os.getenv(ENV["manifest"])
    if manifest:
        path = Path(manifest)
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
                blockers.append("MANIFEST_FILE_NOT_READY")
        except OSError:
            blockers.append("MANIFEST_FILE_NOT_READY")
    latest = None
    raw_state = os.getenv(ENV["state_dir"])
    if raw_state:
        try:
            entry = Path(raw_state) / "latest.json"
            if entry.is_symlink():
                raise ValueError()
            record = json.loads(entry.read_text(encoding="utf-8"))
            latest = {key: record.get(key) for key in (
                "status", "finished_at", "verified_assets", "skipped_assets", "failed_assets", "receipt_persisted", "coverage")}
        except (OSError, ValueError):
            pass
    return {
        "enabled": _is_enabled(), "configuration_ready": not blockers,
        "interval_minutes": 30, "persistent_volume_verified": durable,
        "cloud_access_verified": False, "blockers": blockers,
        "last_run": latest, "scheduler_installed_by_this_status_call": False,
        "note": "Configuration is not a completed backup. Inspect verified receipts; metadata-only backups do not restore credential source files.",
    }


@dataclass(frozen=True)
class _OpenedSource:
    root: Path
    relative: Path
    parent_fd: int
    file_fd: int
    directory_ids: tuple[tuple[int, int], ...]


@contextmanager
def _open_source_without_links(root: Path, relative: Path):
    """Hold the entire directory chain and open the leaf relative to its parent."""
    if (relative.is_absolute() or ".." in relative.parts or not relative.parts
            or "\\" in str(relative) or ".." in root.parts):
        raise BackupStorageError("REGISTERED_SOURCE_MUST_BE_RELATIVE")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise BackupStorageError("SAFE_SNAPSHOT_OPEN_UNSUPPORTED")
    root = root.absolute()
    descriptors = []
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        parent = os.open(root.anchor, flags)
        descriptors.append(parent)
        identities = [(os.fstat(parent).st_dev, os.fstat(parent).st_ino)]
        for part in root.parts[1:] + relative.parts[:-1]:
            parent = os.open(part, flags, dir_fd=parent)
            descriptors.append(parent)
            info = os.fstat(parent)
            identities.append((info.st_dev, info.st_ino))
        source = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=parent)
        descriptors.append(source)
        if not stat.S_ISREG(os.fstat(source).st_mode):
            raise BackupStorageError("SOURCE_REGULAR_FILE_REQUIRED")
        yield _OpenedSource(root, relative, parent, source, tuple(identities))
    except OSError as exc:
        raise BackupStorageError("SOURCE_UNAVAILABLE_OR_UNSAFE") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _assert_source_still_bound(opened: _OpenedSource) -> None:
    """Reject renamed/replaced parents or source paths before publishing bytes."""
    with _open_source_without_links(opened.root, opened.relative) as current:
        held, fresh = os.fstat(opened.file_fd), os.fstat(current.file_fd)
        if (opened.directory_ids != current.directory_ids
                or (held.st_dev, held.st_ino) != (fresh.st_dev, fresh.st_ino)):
            raise BackupStorageError("SOURCE_CHANGED_DURING_SNAPSHOT")


def _sqlite_sidecars(opened: _OpenedSource) -> tuple[int, dict[str, tuple[int, int]]]:
    size, identities = 0, {}
    for suffix in ("-wal", "-shm"):
        try:
            descriptor = os.open(opened.relative.name + suffix,
                                 os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=opened.parent_fd)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BackupStorageError("SQLITE_SIDECAR_UNSAFE") from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise BackupStorageError("SQLITE_SIDECAR_UNSAFE")
            identities[suffix] = (info.st_dev, info.st_ino)
            if suffix == "-wal":
                size += info.st_size
        finally:
            os.close(descriptor)
    return size, identities


def _snapshot(item: RegisteredAsset, source_root: Path, destination: Path) -> str:
    relative = Path(item.source)
    reason = credential_source_reason(relative)
    if reason:
        raise BackupStorageError(reason)
    source = checked_source_path(source_root, relative)
    sqlite_snapshot = item.sqlite_snapshot or source.suffix.lower() in {".sqlite3", ".sqlite", ".db"}
    with _open_source_without_links(source_root, relative) as opened:
        before = os.fstat(opened.file_fd)
        estimate = before.st_size
        sidecar_ids = {}
        if sqlite_snapshot:
            sidecar_size, sidecar_ids = _sqlite_sidecars(opened)
            estimate += sidecar_size
        if estimate > MAX_RUNTIME_FILE_BYTES:
            raise BackupStorageError("SNAPSHOT_FILE_TOO_LARGE")
        if shutil.disk_usage(destination.parent).free < 3 * estimate + 20 * 1024 * 1024:
            raise BackupStorageError("SNAPSHOT_FREE_SPACE_REQUIRED")
        _assert_source_still_bound(opened)
        if sqlite_snapshot:
            # Linux /proc anchors the parent while allowing SQLite to locate WAL.
            # SQLite opens its own descriptors, so pre/post inode checks supplement
            # (not replace) the requirement that runtime directories are app-owned.
            # This is not an adversarial filesystem or whole-service snapshot API.
            anchored = Path(f"/proc/self/fd/{opened.parent_fd}") / relative.name
            if not Path("/proc/self/fd").is_dir():
                raise BackupStorageError("SAFE_SQLITE_SNAPSHOT_OPEN_UNSUPPORTED")
            source_db = sqlite3.connect(anchored.as_uri() + "?mode=ro", uri=True, timeout=20)
            dest_db = sqlite3.connect(destination)
            try:
                _assert_source_still_bound(opened)
                page_size = source_db.execute("PRAGMA page_size").fetchone()[0]
                deadline = time.monotonic() + 60
                def progress(_status, _remaining, total):
                    if total * page_size > MAX_RUNTIME_FILE_BYTES:
                        raise BackupStorageError("SNAPSHOT_FILE_TOO_LARGE")
                    if time.monotonic() > deadline:
                        raise BackupStorageError("SQLITE_SNAPSHOT_TIMEOUT")
                source_db.backup(dest_db, pages=256, progress=progress)
                if dest_db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise BackupStorageError("SQLITE_SNAPSHOT_INTEGRITY_FAILED")
                _, final_sidecars = _sqlite_sidecars(opened)
                if any(final_sidecars.get(suffix) != identity for suffix, identity in sidecar_ids.items()):
                    raise BackupStorageError("SQLITE_SIDECAR_CHANGED_DURING_SNAPSHOT")
            finally:
                dest_db.close()
                source_db.close()
            destination.chmod(0o600)
        else:
            # Copy the exact opened regular file; never reopen its path for data.
            with os.fdopen(os.dup(opened.file_fd), "rb") as src, destination.open("xb") as dst:
                destination.chmod(0o600)
                copied = 0
                while chunk := src.read(8 * 1024 * 1024):
                    copied += len(chunk)
                    if copied > MAX_RUNTIME_FILE_BYTES:
                        raise BackupStorageError("SNAPSHOT_FILE_TOO_LARGE")
                    dst.write(chunk)
            after = os.fstat(opened.file_fd)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise BackupStorageError("SOURCE_CHANGED_DURING_SNAPSHOT")
        _assert_source_still_bound(opened)
    digest = sha256_file(destination)
    if item.expected_sha256 and item.expected_sha256.lower() != digest:
        raise BackupStorageError("REGISTERED_SOURCE_HASH_MISMATCH")
    return digest


def _safe_asset(item: RegisteredAsset, digest: str) -> SecretAsset:
    values = item.model_dump(exclude={"source", "sqlite_snapshot", "expected_sha256"})
    return SecretAsset(**values, content_hash=digest, integrity_verified=not item.contains_credentials)


def _retain_verified_local_copies(previous, receipt, server_root):
    """Rotate only this asset's app-generated copies after both destinations verify."""
    prior = previous.get("retained_local_copies", [])
    added = [{"path": w.storage_ref, "sha256": w.artifact_sha256}
             for w in receipt.writes if w.target.value.startswith("SERVER_")]
    retained = prior + added
    while len(retained) > 3:
        old = retained.pop(0)
        path = Path(old["path"])
        if (not path.is_symlink() and path.resolve().is_relative_to(server_root.resolve())
                and path.name == old["sha256"] + ".edurbk" and path.is_file()
                and sha256_file(path) == old["sha256"]):
            path.unlink()
            fsync_directory(path.parent)
    return retained


def _run_assets(*, manifest: list[RegisteredAsset], source_root: Path, server_root: Path,
                state_dir: Path, now: datetime, drive_factory=None) -> dict:
    records, verified, skipped, failed = [], 0, 0, 0
    if len(manifest) > MAX_RUNTIME_FILES:
        raise BackupStorageError("BACKUP_ASSET_COUNT_LIMIT_REACHED")
    snapshot_bytes = 0
    # The manifest includes only explicitly registered or allowlisted sources.
    with tempfile.TemporaryDirectory(prefix="backup-run-", dir=state_dir) as temporary:
        work = Path(temporary)
        payloads = EncryptedPayloads(source_root=work, staging_root=work)
        try:
            server = ServerBackupAdapter(server_root, payloads)
            drive = (drive_factory or DriveBackupAdapter.from_environment)(payloads=payloads)
            destination_revision = hashlib.sha256(json.dumps({
                "server": str(server_root.resolve()),
                "drive": {str(k): v for k, v in drive.folders.items()},
            }, sort_keys=True).encode()).hexdigest()
            from app.training.trade_secret_engine import BackupTarget
            adapters = {target: server if target.value.startswith("SERVER_") else drive for target in BackupTarget}
            for item in manifest:
                token = hashlib.sha256(item.asset_id.encode()).hexdigest()
                success_path = state_dir / ("success-" + token + ".json")
                try:
                    # A credential asset is NEVER opened, hashed or copied. Preserve only metadata.
                    digest = "credential-source-omitted" if item.contains_credentials else _snapshot(item, source_root, work / token)
                    if not item.contains_credentials:
                        snapshot_bytes += (work / token).stat().st_size
                        if snapshot_bytes > MAX_RUNTIME_TOTAL_BYTES:
                            raise BackupStorageError("BACKUP_TOTAL_BYTES_LIMIT_REACHED")
                    asset = _safe_asset(item, digest)
                    decision = classify_secret_asset(asset)
                    last = None
                    previous = {}
                    if success_path.exists():
                        if success_path.is_symlink():
                            raise BackupStorageError("STATE_SYMLINK_FORBIDDEN")
                        previous = json.loads(success_path.read_text(encoding="utf-8"))
                        # Classification/targets changes invalidate the old completion too.
                        if (previous.get("content_hash") == digest
                                and previous.get("classification") == decision.classification.value
                                and previous.get("destination_revision") == destination_revision):
                            last = datetime.fromisoformat(previous["completed_at"])
                            if last > now:
                                raise BackupStorageError("COMPLETION_TIMESTAMP_IN_FUTURE")
                    receipt = execute_backup_cycle(
                        asset=asset, decision=decision, adapters=adapters,
                        source_ref=None if item.contains_credentials else str(work / token),
                        last_completed_at=last, now=now)
                    if receipt.due:
                        if not receipt.complete or not all(w.encrypted and w.integrity_verified and w.restore_verified for w in receipt.writes):
                            raise BackupStorageError("VERIFIED_ENCRYPTED_RECEIPTS_REQUIRED")
                        retained = _retain_verified_local_copies(previous, receipt, server_root)
                        atomic_json(success_path, {
                            "content_hash": digest, "classification": decision.classification.value,
                            "destination_revision": destination_revision, "retained_local_copies": retained,
                            "completed_at": now.isoformat(), "receipt": receipt.model_dump(mode="json")})
                        verified += 1
                    else:
                        skipped += 1
                    records.append(receipt.model_dump(mode="json"))
                except BackupCycleError as exc:
                    failed += 1
                    records.append(exc.receipt.model_dump(mode="json"))
                except Exception as exc:
                    failed += 1
                    records.append({"asset_id": item.asset_id, "complete": False,
                                    "error_type": type(exc).__name__, "failure_stage": "snapshot_or_state",
                                    "failure_code": exc.safe_code if isinstance(exc, BackupStorageError) else None})
        finally:
            payloads.close()
            if "drive" in locals() and hasattr(drive.session, "close"):
                drive.session.close()
    return {"status": "failed" if failed else "verified" if verified else "not_due",
            "finished_at": now.isoformat(), "verified_assets": verified,
            "skipped_assets": skipped, "failed_assets": failed, "assets": records}


def run_configured_backup_once() -> dict[str, Any]:
    """Run one cycle; safe to schedule every 30 minutes on the SAME durable mount.

    No enablement or infrastructure changes are performed. All failures return
    non-success and attempt a durable failure receipt; exception messages omitted.
    """
    now = datetime.now(timezone.utc)
    status = get_backup_service_status()
    result = {"status": "blocked", "finished_at": now.isoformat(),
              "verified_assets": 0, "skipped_assets": 0, "failed_assets": 0,
              "blockers": status["blockers"], "receipt_persisted": False}
    state = Path(os.environ[ENV["state_dir"]]) if os.getenv(ENV["state_dir"]) else None
    lock = None
    owns_lock = False
    try:
        if state is not None:
            private_directory(state)
            fd = os.open(state / ".backup.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            lock = os.fdopen(fd, "a+")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                owns_lock = True
            except BlockingIOError:
                # Do not overwrite the active worker's durable last receipt.
                result = {**result, "status": "busy", "blockers": ["BACKUP_ALREADY_RUNNING"]}
                return result
        if status["configuration_ready"]:
            manifest_path = Path(os.environ[ENV["manifest"]])
            if manifest_path.is_symlink() or manifest_path.stat().st_size > 1024 * 1024:
                raise BackupStorageError("MANIFEST_INVALID")
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list) or not raw:
                raise BackupStorageError("NONEMPTY_ASSET_MANIFEST_REQUIRED")
            manifest = [RegisteredAsset.model_validate(item) for item in raw]
            if len({item.asset_id for item in manifest}) != len(manifest):
                raise BackupStorageError("DUPLICATE_REGISTERED_ASSET")
            source_root = Path(os.environ[ENV["source_root"]])
            # Refresh after acquiring the cycle lock: new media/documents are
            # included without restarting the service or rewriting owner entries.
            inventory = discover_runtime_assets(source_root)
            merged = merge_runtime_assets([item.model_dump(mode="json") for item in manifest], inventory)
            manifest = [RegisteredAsset.model_validate(item) for item in merged]
            result = _run_assets(manifest=manifest, source_root=Path(os.environ[ENV["source_root"]]),
                                 server_root=Path(os.environ[ENV["server_root"]]),
                                 state_dir=state, now=now)
            result["coverage"] = inventory.summary()
            result["coverage"]["registered_assets"] = len(raw)
            result["coverage"]["effective_assets"] = len(manifest)
            if inventory.issues:
                result["status"] = "failed"
                result["failure_code"] = "RUNTIME_INVENTORY_INCOMPLETE"
            result["receipt_persisted"] = False
    except Exception as exc:
        result.update(status="failed", error_type=type(exc).__name__)
        if isinstance(exc, BackupStorageError):
            result["failure_code"] = exc.safe_code
    finally:
        # Persist before releasing the lock so a second invocation cannot race receipts.
        if state is not None and owns_lock:
            try:
                result["receipt_persisted"] = True
                history = state / "receipts"
                history.mkdir(mode=0o700, exist_ok=True)
                atomic_json(history / (now.strftime("%Y%m%dT%H%M%S%fZ") + ".json"), result)
                atomic_json(state / "latest.json", result)
            except Exception:
                result["receipt_persisted"] = False
                result["status"] = "failed"
                result["receipt_error"] = "RECEIPT_PERSISTENCE_FAILED"
        if lock is not None:
            lock.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--once", action="store_true")
    action.add_argument("--status", action="store_true")
    args = parser.parse_args()
    result = get_backup_service_status() if args.status else run_configured_backup_once()
    # CLI summary deliberately excludes asset/source paths, content and credentials.
    print(json.dumps({k: v for k, v in result.items() if k != "assets"}, ensure_ascii=False))
    return 0 if args.status or result["status"] in {"verified", "not_due"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
