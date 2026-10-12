"""Verify restored originals before granting the container access or starting it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path, PurePosixPath
from urllib.parse import quote

MAX_SIDECAR_BYTES = 16 * 1024 * 1024 + 64 * 1024

def contained_path(root: Path, relative: str) -> Path:
    value = PurePosixPath(relative)
    if not relative or value.is_absolute() or ".." in value.parts or "\\" in relative:
        raise ValueError("RESTORE_PATH_INVALID")
    candidate = root.joinpath(*value.parts)
    if candidate == root or not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("RESTORE_PATH_ESCAPE")
    current = candidate
    while current != root:
        if current.is_symlink():
            raise ValueError("RESTORE_SYMLINK_FORBIDDEN")
        current = current.parent
    return candidate


def verify(root: Path, *, fix_ownership: bool = False) -> dict:
    root = root.absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("RESTORED_STATE_ROOT_REQUIRED")
    sidecar = root.with_name(root.name + ".edurmig-manifest.json")
    if sidecar.is_symlink() or not sidecar.is_file():
        raise ValueError("SIBLING_RESTORE_MANIFEST_REQUIRED")
    # Source archive permits a 16-MiB manifest; restore adds a bounded receipt.
    if sidecar.stat().st_size > MAX_SIDECAR_BYTES:
        raise ValueError("RESTORE_MANIFEST_TOO_LARGE")
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    if manifest.get("format") != "edurecover-offline-migration-v1" or manifest.get("logical_id") != "DREAMKEEPER-001":
        raise ValueError("RESTORE_MANIFEST_IDENTITY_MISMATCH")
    receipt = manifest.get("restore_receipt", {})
    if receipt.get("operation") != "verified_staging_restore" or receipt.get("logical_id") != "DREAMKEEPER-001":
        raise ValueError("VERIFIED_STAGING_RESTORE_RECEIPT_REQUIRED")
    files, directories = manifest.get("files"), manifest.get("directories")
    if not isinstance(files, list) or not files or not isinstance(directories, list):
        raise ValueError("RESTORE_MANIFEST_FILE_LIST_REQUIRED")
    if manifest.get("accounts_state") not in {"included", "operator_confirmed_not_initialized"}:
        raise ValueError("ACCOUNT_STATE_EVIDENCE_REQUIRED")
    account_rows = [row for row in files if isinstance(row, dict) and row.get("path") == "liferecover/accounts.sqlite3"]
    if ((manifest["accounts_state"] == "included" and
         (len(account_rows) != 1 or account_rows[0].get("sqlite") is not True)) or
        (manifest["accounts_state"] == "operator_confirmed_not_initialized" and account_rows)):
        raise ValueError("ACCOUNT_STATE_MANIFEST_MISMATCH")
    validated: list[Path] = []
    expected: set[str] = set()
    databases = 0
    for row in files:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("RESTORE_FILE_ENTRY_INVALID")
        path = contained_path(root, row["path"])
        if row["path"] in expected or not path.is_file():
            raise ValueError("RESTORE_FILE_DUPLICATE_OR_MISSING")
        expected.add(row["path"])
        if type(row.get("size")) is not int or row["size"] < 0 or path.stat().st_size != row["size"]:
            raise ValueError("RESTORE_FILE_SIZE_MISMATCH")
        digest = row.get("sha256", "")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("RESTORE_FILE_DIGEST_INVALID")
        hashed = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hashed.update(chunk)
        if hashed.hexdigest() != digest:
            raise ValueError("RESTORE_FILE_DIGEST_MISMATCH")
        if row.get("sqlite") is True:
            with sqlite3.connect("file:" + quote(str(path)) + "?mode=ro&immutable=1", uri=True) as connection:
                if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("RESTORE_SQLITE_INTEGRITY_FAILED")
            databases += 1
        validated.append(path)
    checked_directories = []
    expected_directories = set()
    for relative in directories:
        if not isinstance(relative, str):
            raise ValueError("RESTORE_DIRECTORY_ENTRY_INVALID")
        path = contained_path(root, relative)
        if relative in expected_directories or not path.is_dir():
            raise ValueError("RESTORE_DIRECTORY_MISSING")
        expected_directories.add(relative)
        checked_directories.append(path)
    actual = set()
    actual_directories = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("RESTORE_SYMLINK_FORBIDDEN")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
        elif path.is_dir():
            actual_directories.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise ValueError("RESTORE_SPECIAL_FILE_FORBIDDEN")
    if actual != expected or actual_directories != expected_directories:
        raise ValueError("RESTORE_FILE_SET_MISMATCH")
    if not databases:
        raise ValueError("RESTORE_SQLITE_DATABASE_REQUIRED")
    # All hashes/paths/SQLite checks finish before any metadata change.
    if fix_ownership:
        if os.geteuid() != 0:
            raise ValueError("ROOT_REQUIRED_FOR_CONTAINER_OWNERSHIP")
        for path in [root, *checked_directories, *validated]:
            os.chown(path, 10001, 10001, follow_symlinks=False)
            path.chmod(0o700 if path.is_dir() else 0o600)
    return {"status": "ORIGINAL_RECORDS_VERIFIED", "logical_id": "DREAMKEEPER-001",
            "file_count": len(validated), "sqlite_database_count": databases,
            "ownership_prepared": fix_ownership, "full_service_restore_verified": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state_root", type=Path)
    parser.add_argument("--fix-ownership", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.state_root, fix_ownership=args.fix_ownership)))
    except Exception as exc:
        raise SystemExit("RESTORE_START_BLOCKED error_type=" + type(exc).__name__) from None


if __name__ == "__main__":
    main()
