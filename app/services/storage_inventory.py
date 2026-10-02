"""Bounded, read-only storage evidence for owner diagnostics and startup logs.

No database content is read, no credential payload is read, and document names are
represented only by SHA-256. Presence and a last-run record are not proof that a
complete, restorable backup exists. All directory traversal uses no-follow FDs.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import stat
import time


MAX_ENTRIES = 5000
MAX_HASH_FILES = 32
MAX_HASH_FILE_BYTES = 20 * 1024 * 1024
MAX_HASH_TOTAL_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
TIME_BUDGET_SECONDS = 3.0
DOCUMENT_SUFFIXES = {".docx", ".jpg", ".jpeg", ".png"}
DATABASES = (
    "classroom_tutor.sqlite3", "ai_tutor_training.sqlite3", "secret_vault.sqlite3",
    "liferecover/life.sqlite3", "liferecover/medical.sqlite3",
    "liferecover/legal.sqlite3", "liferecover/accounts.sqlite3",
)
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


@contextmanager
def _directory(path, *, parent_fd=None):
    parts = Path(path).parts
    if ".." in parts:
        raise ValueError("UNSAFE_PATH")
    if parent_fd is None:
        absolute = Path(os.path.abspath(path))
        fd = os.open(absolute.anchor, _DIRECTORY_FLAGS)
        parts = absolute.parts[1:]
    else:
        if Path(path).is_absolute():
            raise ValueError("UNSAFE_PATH")
        fd = os.dup(parent_fd)
    try:
        for part in parts:
            if part == ".":
                continue
            next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _file(root_fd, relative):
    path = Path(relative)
    with _directory(path.parent, parent_fd=root_fd) as parent:
        fd = os.open(path.name, _FILE_FLAGS, dir_fd=parent)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("NONREGULAR_FILE")
            yield fd
        finally:
            os.close(fd)


def _presence(root_fd, relative):
    try:
        with _file(root_fd, relative) as fd:
            return {"status": "present", "bytes": os.fstat(fd).st_size}
    except FileNotFoundError:
        return {"status": "missing"}
    except (OSError, ValueError):
        return {"status": "unsafe_or_unreadable"}


def _json_value(root_fd, relative, deadline):
    if time.monotonic() >= deadline:
        return None
    try:
        with _file(root_fd, relative) as fd:
            before = os.fstat(fd)
            if before.st_size > MAX_JSON_BYTES:
                return None
            data = bytearray()
            while len(data) <= MAX_JSON_BYTES:
                if time.monotonic() >= deadline:
                    return None
                chunk = os.read(fd, min(65536, MAX_JSON_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            after = os.fstat(fd)
            if (len(data) > MAX_JSON_BYTES or before.st_size != len(data)
                    or before.st_mtime_ns != after.st_mtime_ns
                    or before.st_ctime_ns != after.st_ctime_ns):
                return None
            return json.loads(data)
    except (OSError, ValueError, RecursionError):
        return None


def _document_hash(fd, name, expected, budget):
    if (expected.st_size > MAX_HASH_FILE_BYTES
            or budget["hash_files"] >= MAX_HASH_FILES
            or budget["hash_bytes"] + expected.st_size > MAX_HASH_TOTAL_BYTES
            or time.monotonic() >= budget["deadline"]):
        return None
    budget["hash_files"] += 1
    try:
        with _file(fd, name) as document:
            before = os.fstat(document)
            if (before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns) != (
                    expected.st_ino, expected.st_dev, expected.st_size, expected.st_mtime_ns):
                return None
            digest = hashlib.sha256()
            size = 0
            while size <= before.st_size:
                if time.monotonic() >= budget["deadline"]:
                    return None
                chunk = os.read(document, min(65536, before.st_size + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                budget["hash_bytes"] += len(chunk)
                if size > before.st_size or budget["hash_bytes"] > MAX_HASH_TOTAL_BYTES:
                    return None
                digest.update(chunk)
            after = os.fstat(document)
            if (size != before.st_size or before.st_mtime_ns != after.st_mtime_ns
                    or before.st_ctime_ns != after.st_ctime_ns):
                return None
            return {"filename_sha256": hashlib.sha256(os.fsencode(name)).hexdigest(),
                    "sha256": digest.hexdigest(), "bytes": size}
    except (OSError, ValueError):
        return None


def _census(root_fd, relative, budget, *, hash_documents=False, depth=0):
    result = {"status": "present", "files": 0, "bytes": 0,
              "excluded_entries": 0, "truncated": False}
    if hash_documents:
        result.update(docx_files=0, image_files=0, hashed_documents=[], hash_incomplete=False)

    def walk(fd, level):
        with os.scandir(fd) as entries:
            for entry in entries:
                if budget["entries"] >= MAX_ENTRIES or time.monotonic() >= budget["deadline"]:
                    result["truncated"] = True
                    return
                budget["entries"] += 1
                try:
                    metadata = entry.stat(follow_symlinks=False)
                    if stat.S_ISREG(metadata.st_mode):
                        result["files"] += 1
                        result["bytes"] += metadata.st_size
                        suffix = Path(entry.name).suffix.lower()
                        if hash_documents and suffix in DOCUMENT_SUFFIXES:
                            result["docx_files" if suffix == ".docx" else "image_files"] += 1
                            hashed = _document_hash(fd, entry.name, metadata, budget)
                            if hashed is None:
                                result["hash_incomplete"] = True
                            else:
                                result["hashed_documents"].append(hashed)
                    elif stat.S_ISDIR(metadata.st_mode):
                        if level >= depth:
                            result["truncated"] = True
                        else:
                            with _directory(entry.name, parent_fd=fd) as child:
                                walk(child, level + 1)
                    else:
                        result["excluded_entries"] += 1
                except (OSError, ValueError):
                    result["excluded_entries"] += 1

    try:
        with _directory(relative, parent_fd=root_fd) as folder:
            walk(folder, 0)
    except FileNotFoundError:
        result["status"] = "missing"
    except (OSError, ValueError):
        result["status"] = "unsafe_or_unreadable"
        result["truncated"] = True
    if hash_documents:
        result["hashed_documents"].sort(key=lambda item: item["filename_sha256"])
        result["hash_incomplete"] |= result["truncated"]
    return result


def _backup_metadata(root_fd, deadline):
    result = {"manifest": _presence(root_fd, "backup_manifest.json"),
              "activation": _presence(root_fd, "backup_activation.json"),
              "latest_record": _presence(root_fd, "backup_state/latest.json"),
              "drive_oauth_payload_present": _presence(
                  root_fd, "oauth_private/drive_oauth.edurbk")["status"] == "present"}
    activation = _json_value(root_fd, "backup_activation.json", deadline)
    result["activation_enabled"] = activation.get("enabled") is True if isinstance(activation, dict) else None
    manifest = _json_value(root_fd, "backup_manifest.json", deadline)
    result["manifest_parseable"] = isinstance(manifest, list)
    if isinstance(manifest, list):
        counts = {"known_databases": 0, "classroom_media": 0,
                  "private_documents": 0, "other": 0}
        known = set()
        for item in manifest:
            source = item.get("source") if isinstance(item, dict) else None
            path = PurePosixPath(source) if isinstance(source, str) else None
            if path is None or path.is_absolute() or ".." in path.parts:
                counts["other"] += 1
            elif str(path) in DATABASES:
                counts["known_databases"] += 1
                known.add(str(path))
            elif path.parts and path.parts[0] in {"classroom_media", "private_documents"}:
                counts[path.parts[0]] += 1
            else:
                counts["other"] += 1
        result.update(manifest_entries=len(manifest), registered_source_counts=counts,
                      registered_known_databases=sorted(known))
    record = _json_value(root_fd, "backup_state/latest.json", deadline)
    if isinstance(record, dict):
        latest = {}
        if isinstance(record.get("status"), str) and record["status"] in {
                "blocked", "busy", "failed", "verified", "not_due"}:
            latest["status"] = record["status"]
        for key in ("verified_assets", "skipped_assets", "failed_assets"):
            value = record.get(key)
            if type(value) is int and 0 <= value <= 1_000_000_000:
                latest[key] = value
        if type(record.get("receipt_persisted")) is bool:
            latest["receipt_persisted"] = record["receipt_persisted"]
        value = record.get("finished_at")
        if isinstance(value, str) and len(value) <= 40:
            try:
                latest["finished_at"] = datetime.fromisoformat(value).isoformat()
            except ValueError:
                pass
        result["last_run_reported"] = latest
    result["backup_completeness_verified"] = False
    return result


def build_storage_inventory(root):
    """Return evidence without reading DBs, credentials, or unbounded files.

    Counts are lower bounds whenever ``truncated`` is true. Hashes prove the
    observed bytes only, not backup/restore success. JSON fields are allowlisted.
    No storage helper that creates files or modifies permissions is called.
    """
    budget = {"entries": 0, "hash_files": 0, "hash_bytes": 0,
              "deadline": time.monotonic() + TIME_BUDGET_SECONDS}
    result = {"version": 1, "root_status": "present", "read_only": True,
              "limits": {"entries": MAX_ENTRIES, "hash_files": MAX_HASH_FILES,
                         "hash_file_bytes": MAX_HASH_FILE_BYTES,
                         "hash_total_bytes": MAX_HASH_TOTAL_BYTES}}
    try:
        with _directory(root) as root_fd:
            result["databases"] = {name: _presence(root_fd, name) for name in DATABASES}
            result["backup"] = _backup_metadata(root_fd, budget["deadline"])
            result["private_documents"] = _census(
                root_fd, "private_documents/documents", budget, hash_documents=True)
            result["private_document_metadata"] = _census(root_fd, "private_documents/metadata", budget)
            result["classroom_media"] = _census(root_fd, "classroom_media", budget, depth=3)
            result["backup_receipts"] = _census(root_fd, "backup_state/receipts", budget)
            result["backup_artifacts"] = _census(root_fd, "backups", budget, depth=3)
    except FileNotFoundError:
        result["root_status"] = "missing"
    except (OSError, ValueError):
        result["root_status"] = "unsafe_or_unreadable"
    result["budget_exhausted"] = (budget["entries"] >= MAX_ENTRIES
                                  or time.monotonic() >= budget["deadline"])
    return result


def log_storage_inventory():
    """Startup hook; errors are redacted and never prevent application startup."""
    logger = logging.getLogger("uvicorn.error")
    try:
        root = Path(os.getenv("EDURECOVER_CLASSROOM_STATE_DIR", ".edurecover_classroom_state"))
        result = build_storage_inventory(root)
        logger.info("storage_inventory=%s", json.dumps(result, sort_keys=True, separators=(",", ":")))
        return result
    except Exception:
        logger.warning("storage_inventory=UNAVAILABLE")
        return {"root_status": "unavailable", "read_only": True}
