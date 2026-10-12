"""Bounded inventory of app-owned runtime data, refreshed for each backup run.

This is an allowlist, not a recursive scan of a volume. Discovery never changes
the owner's manifest or enables backups. The application's account database is
included as restricted personal data for encrypted recovery. Raw deployment
credentials and OAuth tokens stay outside this scope.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.training.backup_adapters import BackupStorageError

MAX_RUNTIME_FILES = 4096
MAX_SCAN_ENTRIES = 8192
MAX_RUNTIME_FILE_BYTES = 256 * 1024 * 1024
MAX_RUNTIME_TOTAL_BYTES = 512 * 1024 * 1024

ACCOUNT_DATABASE = "liferecover/accounts.sqlite3"

# These are the paths actually written by the runtime at the reviewed revision.
RUNTIME_DATABASES = (
    "classroom_tutor.sqlite3", "ai_tutor_training.sqlite3", "secret_vault.sqlite3",
    ACCOUNT_DATABASE,
    "liferecover/life.sqlite3", "liferecover/medical.sqlite3", "liferecover/legal.sqlite3",
)
_METADATA_NAME = re.compile(r"[0-9a-f]{64}\.json\Z")
_MEDIA_NAME = re.compile(r"[0-9a-f]{32}\.(?:png|jpg|mp3|wav|mp4|webm)\Z")


def _private_document_name(name: str) -> bool:
    return (0 < len(name) <= 180 and not name.startswith(".")
            and not any(ord(char) < 32 or char in {"/", "\\"} for char in name)
            and Path(name).suffix.lower() in {".docx", ".jpg", ".jpeg", ".png"})


def credential_source_reason(relative: Path) -> str | None:
    """Permit only the canonical application account DB, not arbitrary credentials."""
    if relative.as_posix() == ACCOUNT_DATABASE:
        return None
    parts = [part.lower() for part in relative.parts]
    name = relative.name.lower()
    if any(part in {"oauth_private", ".aws", ".ssh", ".git"} for part in parts):
        return "CREDENTIAL_SOURCE_REQUIRES_METADATA_ONLY"
    if (name == "accounts.sqlite3" or name.startswith("accounts.sqlite3-")
            or name.startswith(".env")
            or relative.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".edurbk"}
            or any(token in name for token in (
                "service_account", "credential", "oauth", "access_token", "refresh_token",
                "client_secret", "api_key", "secret_key", "master_key", "private_key", "passphrase"))
            or name in {"token.json", "tokens.json", "secrets.json", "passwords.json"}):
        return "CREDENTIAL_SOURCE_REQUIRES_METADATA_ONLY"
    return None


def checked_source_path(source_root: Path, relative: str | Path) -> Path:
    """Reject traversal, non-regular sources, and symlinks in every component."""
    relative = Path(relative)
    if (relative.is_absolute() or ".." in relative.parts or not relative.parts
            or "\\" in str(relative)):
        raise BackupStorageError("REGISTERED_SOURCE_MUST_BE_RELATIVE")
    root = source_root.absolute()
    cursor = Path(root.anchor)
    for part in root.parts[1:] + relative.parts:
        cursor /= part
        mode = cursor.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise BackupStorageError("SOURCE_SYMLINK_FORBIDDEN")
        if cursor != root / relative and not stat.S_ISDIR(mode):
            raise BackupStorageError("SOURCE_PARENT_NOT_DIRECTORY")
    if not stat.S_ISREG(cursor.lstat().st_mode):
        raise BackupStorageError("SOURCE_REGULAR_FILE_REQUIRED")
    return cursor


def _checked_directory(root: Path, relative: str = "") -> Path:
    path = root.absolute()
    cursor = Path(path.anchor)
    parts = path.parts[1:] + (Path(relative).parts if relative else ())
    for part in parts:
        cursor /= part
        mode = cursor.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise BackupStorageError("SOURCE_SYMLINK_FORBIDDEN")
        if not stat.S_ISDIR(mode):
            raise BackupStorageError("SOURCE_PARENT_NOT_DIRECTORY")
    return cursor


@dataclass
class RuntimeInventory:
    assets: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, str]] = field(default_factory=list)
    absent_databases: list[str] = field(default_factory=list)
    total_bytes: int = 0
    scanned_entries: int = 0

    def issue(self, code: str, source: str) -> None:
        # Receipts/status do not expose private filenames or credential locations.
        self.issues.append({"code": code, "source_sha256": hashlib.sha256(source.encode()).hexdigest()})

    def summary(self) -> dict[str, Any]:
        return {
            "scope": "allowlisted_runtime_data",
            "inventory_complete_for_scope": not self.issues,
            "discovered_assets": len(self.assets), "discovered_bytes": self.total_bytes,
            "absent_databases": self.absent_databases, "issues": self.issues,
            "oauth_tokens_environment_and_encryption_keys": "excluded",
            "classroom_database": "existing_encrypted_policy_may_include_authentication_data",
            "account_database": "included_in_encrypted_backup_scope",
            "account_database_discovered": any(
                item["source"] == ACCOUNT_DATABASE for item in self.assets),
            "snapshot_consistency": "per_asset_not_atomic_across_service",
            "full_service_restore_verified": False,
        }


def discover_runtime_assets(source_root: Path, *, max_files: int = MAX_RUNTIME_FILES,
                            max_entries: int = MAX_SCAN_ENTRIES,
                            max_file_bytes: int = MAX_RUNTIME_FILE_BYTES,
                            max_total_bytes: int = MAX_RUNTIME_TOTAL_BYTES) -> RuntimeInventory:
    """Inventory only known stores; missing lazy-created stores are reported."""
    limits = (max_files, max_entries, max_file_bytes, max_total_bytes)
    if any(isinstance(value, bool) or value <= 0 for value in limits):
        raise ValueError("POSITIVE_INVENTORY_LIMITS_REQUIRED")
    result = RuntimeInventory()
    try:
        _checked_directory(source_root)
    except (OSError, BackupStorageError):
        result.issue("SOURCE_ROOT_UNAVAILABLE_OR_UNSAFE", "root")
        return result

    def add(relative: str, *, database: bool = False) -> bool:
        if len(result.assets) >= max_files:
            result.issue("INVENTORY_FILE_LIMIT_REACHED", relative)
            return False
        reason = credential_source_reason(Path(relative))
        if reason:
            result.issue(reason, relative)
            return True
        try:
            path = checked_source_path(source_root, relative)
            size = path.stat().st_size
            if database:
                # SQLite backup includes committed WAL pages; never copy WAL raw.
                for suffix in ("-wal", "-shm"):
                    try:
                        sidecar = checked_source_path(source_root, relative + suffix)
                    except FileNotFoundError:
                        continue
                    if suffix == "-wal":
                        size += sidecar.stat().st_size
            if size > max_file_bytes:
                result.issue("INVENTORY_FILE_TOO_LARGE", relative)
                return True
            if result.total_bytes + size > max_total_bytes:
                result.issue("INVENTORY_TOTAL_BYTES_LIMIT_REACHED", relative)
                return False
            result.assets.append({
                "asset_id": "runtime-" + hashlib.sha256(relative.encode()).hexdigest(),
                "source": relative,
                "kind": ("CONFIGURATION" if relative == ACCOUNT_DATABASE
                         else "TRAINING_RECORD" if database else "UPLOAD"),
                # No public provenance or deidentification is assumed for uploads.
                "contains_personal_data": True, "owner_marked_trade_secret": True,
                "provenance_complete": True, "sqlite_snapshot": database,
            })
            result.total_bytes += size
        except FileNotFoundError:
            if database:
                result.absent_databases.append(relative)
            else:
                result.issue("SOURCE_DISAPPEARED_DURING_INVENTORY", relative)
        except (OSError, BackupStorageError):
            result.issue("SOURCE_UNAVAILABLE_OR_UNSAFE", relative)
        return True

    for relative in RUNTIME_DATABASES:
        if not add(relative, database=True):
            return result

    expected_metadata: set[str] = set()
    found_metadata: set[str] = set()
    for directory, accepted in (
        ("private_documents/documents", _private_document_name),
        ("private_documents/metadata", lambda name: bool(_METADATA_NAME.fullmatch(name))),
        ("classroom_media", lambda name: bool(_MEDIA_NAME.fullmatch(name))),
    ):
        try:
            path = _checked_directory(source_root, directory)
            # Scan lazily and stop at a hard bound; no recursive glob or sorting
            # of an unbounded attacker-controlled directory.
            with os.scandir(path) as entries:
                for entry in entries:
                    result.scanned_entries += 1
                    if result.scanned_entries > max_entries:
                        result.issue("INVENTORY_SCAN_LIMIT_REACHED", directory)
                        return result
                    relative = directory + "/" + entry.name
                    # In-progress imports/uploads are unpublished temporary files.
                    if entry.name.endswith(".tmp"):
                        continue
                    if not accepted(entry.name):
                        result.issue("UNRECOGNIZED_RUNTIME_FILE", relative)
                        continue
                    if directory == "private_documents/documents":
                        expected_metadata.add(hashlib.sha256(entry.name.encode()).hexdigest() + ".json")
                    elif directory == "private_documents/metadata":
                        found_metadata.add(entry.name)
                    if not add(relative):
                        return result
        except FileNotFoundError:
            continue
        except (OSError, BackupStorageError):
            result.issue("SOURCE_DIRECTORY_UNAVAILABLE_OR_UNSAFE", directory)
    for name in sorted(expected_metadata - found_metadata):
        result.issue("PRIVATE_DOCUMENT_METADATA_MISSING", name)
    for name in sorted(found_metadata - expected_metadata):
        result.issue("PRIVATE_DOCUMENT_ORIGINAL_MISSING", name)
    result.assets.sort(key=lambda item: item["source"])
    return result


def merge_runtime_assets(registered: list[dict[str, Any]], inventory: RuntimeInventory) -> list[dict[str, Any]]:
    """Add discovered sources in memory; owner entries always retain their values."""
    merged = list(registered)
    existing_sources = {Path(item["source"]).as_posix() for item in registered}
    existing_ids = {item["asset_id"] for item in registered}
    for item in inventory.assets:
        if item["source"] in existing_sources:
            continue
        if item["asset_id"] in existing_ids:
            raise BackupStorageError("RUNTIME_ASSET_ID_CONFLICT")
        merged.append(item)
        existing_sources.add(item["source"])
        existing_ids.add(item["asset_id"])
    if len(merged) > MAX_RUNTIME_FILES:
        raise BackupStorageError("BACKUP_ASSET_COUNT_LIMIT_REACHED")
    return merged
