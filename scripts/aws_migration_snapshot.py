"""Offline, encrypted migration of the complete EduRecover state directory.

This is separate from normal business backups: accounts, the secret vault and
OAuth private state are included. Stop every writer before export. The operator
confirmation is not an automatic check of running processes or remote writers.
Deployment environment secrets are NOT captured and must be transferred through
AWS Secrets Manager separately, with their existing values preserved.

Examples (set EDURECOVER_MIGRATION_PASSPHRASE through a secret channel first)::

    python scripts/aws_migration_snapshot.py export --state-root /app/.edurecover_classroom_state --archive /private/state.edurmig --writes-quiesced
    python scripts/aws_migration_snapshot.py restore --archive /private/state.edurmig --staging-root /private/restored-state

Restore only creates a new staging directory. It never replaces a live volume.
An authenticated verification manifest is saved outside that directory as
<staging-root>.edurmig-manifest.json for the deployment preflight.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import struct
import tarfile
import tempfile
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

PASSPHRASE_ENV = "EDURECOVER_MIGRATION_PASSPHRASE"
MAGIC = b"EDURMIG1"
HEADER = struct.Struct(">8s16s12s")
CHUNK_SIZE = 1024 * 1024
MAX_FILES = 100_000
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
ACCOUNTS_PATH = "liferecover/accounts.sqlite3"
FORMAT = "edurecover-offline-migration-v1"
LOGICAL_ID = "DREAMKEEPER-001"


class MigrationError(RuntimeError):
    """An export or staging restore could not be verified."""


def _derive_key(salt: bytes) -> bytes:
    value = os.getenv(PASSPHRASE_ENV)
    if value is None or not value.strip():
        raise MigrationError(f"Configure {PASSPHRASE_ENV} through a secret channel.")
    return Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(value.encode("utf-8"))


def _no_symlink_ancestors(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    for item in (*reversed(path.parents), path):
        if item.is_symlink():
            raise MigrationError("Symlinks in source or destination paths are forbidden.")
    return path


def _relative(name: object) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\0" in name:
        raise MigrationError("Invalid archive path.")
    path = PurePosixPath(name)
    if (path.is_absolute() or str(path) != name or len(name) > 4096
            or any(part in {".", ".."} or ":" in part for part in path.parts)):
        raise MigrationError("Unsafe archive path.")
    return name


def _logical_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
        raise MigrationError("Provide the existing logical ID; migration never generates a replacement ID.")
    return value


def _fingerprint(path: Path) -> tuple[int, ...]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise MigrationError("Only regular files without hard links may be migrated.")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _source_handles(path: Path, expected: tuple[int, ...] | None = None):
    """Hold the directory chain; never follow a parent or leaf symlink."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise MigrationError("Safe source opens require O_NOFOLLOW and O_DIRECTORY.")
    path = Path(os.path.abspath(path))
    descriptors = []
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        parent = os.open(path.anchor, flags)
        descriptors.append(parent)
        for part in path.parts[1:-1]:
            parent = os.open(part, flags, dir_fd=parent)
            descriptors.append(parent)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        descriptors.append(fd)
        actual = os.fstat(fd)
        if (not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1
                or expected is not None and (actual.st_dev, actual.st_ino) != expected[:2]):
            raise MigrationError("Source changed or is not a regular file without hard links.")
        with os.fdopen(os.dup(fd), "rb") as stream:
            yield stream, parent
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


@contextmanager
def _open_source(path: Path, expected: tuple[int, ...] | None = None):
    with _source_handles(path, expected) as (stream, _parent):
        yield stream


def _scan(root: Path) -> tuple[dict[str, tuple[int, ...]], list[str]]:
    files, directories = {}, []
    def fail(error: OSError) -> None:
        raise error
    for directory, dirnames, filenames in os.walk(root, followlinks=False, onerror=fail):
        dirnames.sort()
        filenames.sort()
        for name in dirnames:
            path = Path(directory) / name
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise MigrationError("Symlinks and special directories are forbidden.")
            directories.append(_relative(path.relative_to(root).as_posix()))
        for name in filenames:
            path = Path(directory) / name
            relative = _relative(path.relative_to(root).as_posix())
            if (name == ".env" or name.startswith(".env.")
                    or name.lower() in {"service_account.json", "google_credentials.json", "aws_credentials"}
                    or path.suffix.lower() in {".pem", ".key"} or ".aws" in path.relative_to(root).parts):
                raise MigrationError("Deployment credential files require separate secret transfer.")
            files[relative] = _fingerprint(path)
            if len(files) > MAX_FILES:
                raise MigrationError("State contains too many files.")
    return files, sorted(directories)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _open_source(path) as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sqlite(path: Path, expected: tuple[int, ...] | None = None) -> bool:
    with _open_source(path, expected) as source:
        header = source.read(16)
    # An untouched SQLite file can legitimately be zero bytes. SQLite's backup
    # API produces a standalone database and quick_check still verifies it.
    found = header == b"SQLite format 3\0" or not header and path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}
    if path.suffix.lower() in {".sqlite", ".sqlite3", ".db"} and not found:
        raise MigrationError("A database file is not a valid SQLite database.")
    return found


def _check_sqlite(path: Path) -> None:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise MigrationError("SQLite quick_check failed.")


def _snapshot_sqlite(source: Path, destination: Path, expected: tuple[int, ...]) -> None:
    deadline = time.monotonic() + 120
    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() > deadline:
            raise MigrationError("SQLite backup timed out; confirm that every writer is stopped.")
    # immutable=1 would ignore a live WAL, so intentionally use mode=ro instead.
    with _source_handles(source, expected) as (_stream, parent):
        if not Path("/proc/self/fd").is_dir():
            raise MigrationError("SQLite snapshots require Linux /proc directory handles.")
        # Anchor the parent so that SQLite also finds the correct WAL file. This
        # supplements, rather than replaces, the stopped-writers requirement.
        anchored = Path(f"/proc/self/fd/{parent}") / source.name
        with closing(sqlite3.connect(anchored.as_uri() + "?mode=ro", uri=True, timeout=5)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.execute("PRAGMA query_only=ON")
                src.backup(dst, pages=256, progress=progress, sleep=0.05)
                dst.execute("PRAGMA journal_mode=DELETE")
    destination.chmod(0o600)
    _check_sqlite(destination)


def _encrypt(source: Path, destination: Path) -> None:
    salt, nonce = os.urandom(16), os.urandom(12)
    header = HEADER.pack(MAGIC, salt, nonce)
    encryptor = Cipher(algorithms.AES(_derive_key(salt)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    with source.open("rb") as plain, destination.open("xb") as encrypted:
        destination.chmod(0o600)
        encrypted.write(header)
        while chunk := plain.read(CHUNK_SIZE):
            encrypted.write(encryptor.update(chunk))
        encrypted.write(encryptor.finalize())
        encrypted.write(encryptor.tag)
        encrypted.flush()
        os.fsync(encrypted.fileno())


def _decrypt(source: Path, destination: Path) -> None:
    with _open_source(source) as encrypted:
        size = os.fstat(encrypted.fileno()).st_size
        header = encrypted.read(HEADER.size)
        if size <= HEADER.size + 16 or len(header) != HEADER.size:
            raise MigrationError("Migration archive is truncated.")
        magic, salt, nonce = HEADER.unpack(header)
        if magic != MAGIC:
            raise MigrationError("Unsupported migration archive format.")
        encrypted.seek(-16, os.SEEK_END)
        tag = encrypted.read(16)
        encrypted.seek(HEADER.size)
        decryptor = Cipher(algorithms.AES(_derive_key(salt)), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(header)
        remaining = size - HEADER.size - 16
        try:
            with destination.open("xb") as plain:
                destination.chmod(0o600)
                while remaining:
                    chunk = encrypted.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise MigrationError("Migration archive is truncated.")
                    remaining -= len(chunk)
                    plain.write(decryptor.update(chunk))
                plain.write(decryptor.finalize())
        except InvalidTag as exc:
            raise MigrationError("Wrong passphrase or damaged migration archive.") from exc


def _receipt(manifest: dict, *, operation: str) -> dict:
    return {
        "operation": operation,
        "archive_format": FORMAT,
        "logical_id": manifest["logical_id"],
        "file_count": len(manifest["files"]),
        "sqlite_database_count": sum(item["sqlite"] for item in manifest["files"]),
        "accounts_state": manifest["accounts_state"],
        "operator_confirmed_writes_quiesced": True,
        "cross_database_atomicity_automatically_verified": False,
        "environment_secrets_included": False,
        "preserve_existing_environment_secrets_in_aws_secrets_manager": True,
        "full_service_restore_verified": False,
        "application_and_key_restore_verification_required": True,
    }


def export_snapshot(state_root: str | Path, archive: str | Path, *,
                    writes_quiesced: bool = False,
                    accounts_not_initialized: bool = False,
                    logical_id: str = LOGICAL_ID) -> dict:
    if not writes_quiesced:
        raise MigrationError("Stop every service writer and explicitly confirm writes_quiesced.")
    logical_id = _logical_id(logical_id)
    root, output = _no_symlink_ancestors(Path(state_root)), _no_symlink_ancestors(Path(archive))
    if not root.is_dir() or output.is_relative_to(root):
        raise MigrationError("Use an existing state directory and an archive outside it.")
    if output.suffix != ".edurmig" or output.exists():
        raise MigrationError("Choose a new .edurmig archive path; overwriting is forbidden.")
    before, directories = _scan(root)
    if ACCOUNTS_PATH not in before and not accounts_not_initialized:
        raise MigrationError("Account database is missing; explicitly confirm accounts were never initialized.")
    with tempfile.TemporaryDirectory(prefix="edurmig-") as temporary:
        private = Path(temporary)
        private.chmod(0o700)
        snapshot = private / "state"
        snapshot.mkdir(mode=0o700)
        databases = {name for name in before if _is_sqlite(root / name, before[name])
                     and not name.endswith(("-wal", "-shm", "-journal"))}
        sidecars = {name + suffix for name in databases for suffix in ("-wal", "-shm", "-journal")}
        wal_hashes = {name: _sha256(root / name) for name in before
                      if name in {database + "-wal" for database in databases}}
        files = []
        for name in directories:
            (snapshot / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in sorted(before):
            if name in sidecars:
                continue
            source, target = root / name, snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if name in databases:
                _snapshot_sqlite(source, target, before[name])
            else:
                with _open_source(source, before[name]) as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst, CHUNK_SIZE)
                target.chmod(0o600)
            files.append({"path": name, "size": target.stat().st_size,
                          "sha256": _sha256(target), "sqlite": name in databases})
        after, after_directories = _scan(root)
        # SQLite's read-only WAL coordination can modify/create an SHM file.
        ignored = {name + "-shm" for name in databases}
        # SQLite may also reset WAL file permissions (ctime only). Verify its
        # content as well as identity/size/mtime, rather than treating chmod as a
        # committed transaction or ignoring actual WAL changes.
        before_compare = {k: v[:4] if k in wal_hashes else v for k, v in before.items() if k not in ignored}
        after_compare = {k: v[:4] if k in wal_hashes else v for k, v in after.items() if k not in ignored}
        if (before_compare != after_compare or directories != after_directories
                or any(_sha256(root / name) != digest for name, digest in wal_hashes.items())):
            raise MigrationError("State changed during export; stop all writers and retry.")
        manifest = {
            "format": FORMAT, "created_at": datetime.now(timezone.utc).isoformat(),
            "logical_id": logical_id,
            "operator_confirmed_writes_quiesced": True,
            "environment_secrets_included": False,
            "accounts_state": "included" if ACCOUNTS_PATH in before else "operator_confirmed_not_initialized",
            "directories": directories, "files": files,
        }
        manifest_path = private / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        manifest_path.chmod(0o600)
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise MigrationError("Migration manifest is too large.")
        plain = private / "snapshot.tar"
        with tarfile.open(plain, "w", format=tarfile.PAX_FORMAT) as tar:
            tar.add(manifest_path, arcname="manifest.json", recursive=False)
            for item in files:
                tar.add(snapshot / item["path"], arcname="state/" + item["path"], recursive=False)
        plain.chmod(0o600)
        encrypted = private / "snapshot.edurmig"
        _encrypt(plain, encrypted)
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Exclusive destination creation avoids an overwrite race.
        created = False
        try:
            with output.open("xb") as dst, encrypted.open("rb") as src:
                created = True
                output.chmod(0o600)
                shutil.copyfileobj(src, dst, CHUNK_SIZE)
                dst.flush()
                os.fsync(dst.fileno())
        except BaseException:
            if created:
                output.unlink(missing_ok=True)
            raise
    return _receipt(manifest, operation="encrypted_export")


def _validate_manifest(value: object) -> tuple[dict, dict[str, dict]]:
    if not isinstance(value, dict) or value.get("format") != FORMAT:
        raise MigrationError("Invalid migration manifest.")
    _logical_id(value.get("logical_id"))
    if value.get("operator_confirmed_writes_quiesced") is not True or value.get("environment_secrets_included") is not False:
        raise MigrationError("Invalid migration safety declarations.")
    items, directories = value.get("files"), value.get("directories")
    if not isinstance(items, list) or len(items) > MAX_FILES or not isinstance(directories, list):
        raise MigrationError("Invalid migration inventory.")
    expected = {}
    for item in items:
        if not isinstance(item, dict):
            raise MigrationError("Invalid migration file entry.")
        name = _relative(item.get("path"))
        digest = item.get("sha256")
        if (name in expected or type(item.get("size")) is not int or item["size"] < 0
                or type(item.get("sqlite")) is not bool or not isinstance(digest, str)
                or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
            raise MigrationError("Invalid migration file entry.")
        expected[name] = item
    directory_set = {_relative(name) for name in directories}
    if len(directory_set) != len(directories) or directory_set.intersection(expected):
        raise MigrationError("Invalid migration directories.")
    if any(any(parent.as_posix() in expected for parent in PurePosixPath(name).parents if parent.as_posix() != ".")
           for name in list(expected) + directories):
        raise MigrationError("File and directory paths conflict.")
    accounts_state = value.get("accounts_state")
    if (not isinstance(accounts_state, str)
            or (accounts_state == "included" and (ACCOUNTS_PATH not in expected or not expected[ACCOUNTS_PATH]["sqlite"]))
            or (accounts_state == "operator_confirmed_not_initialized" and ACCOUNTS_PATH in expected)
            or accounts_state not in {"included", "operator_confirmed_not_initialized"}):
        raise MigrationError("Invalid account recovery declaration.")
    return value, expected


def restore_snapshot(archive: str | Path, staging_root: str | Path, *,
                     logical_id: str = LOGICAL_ID) -> dict:
    logical_id = _logical_id(logical_id)
    source, target = _no_symlink_ancestors(Path(archive)), _no_symlink_ancestors(Path(staging_root))
    manifest_output = _no_symlink_ancestors(target.with_name(target.name + ".edurmig-manifest.json"))
    if not source.is_file() or target.exists() or manifest_output.exists() or not target.parent.is_dir():
        raise MigrationError("Restore requires an archive and a new staging directory under an existing parent.")
    created, manifest_created = False, False
    try:
        with tempfile.TemporaryDirectory(prefix="edurmig-restore-", dir=target.parent) as temporary:
            private = Path(temporary)
            private.chmod(0o700)
            plain = private / "authenticated.tar"
            _decrypt(source, plain)
            target.mkdir(mode=0o700)
            created = True
            with tarfile.open(plain, "r:") as tar:
                first = tar.next()
                if (first is None or first.name != "manifest.json" or not first.isreg()
                        or first.sparse is not None or first.size > MAX_MANIFEST_BYTES):
                    raise MigrationError("Migration manifest must be the first regular archive member.")
                stream = tar.extractfile(first)
                if stream is None:
                    raise MigrationError("Migration manifest is missing.")
                manifest, expected = _validate_manifest(json.loads(stream.read().decode("utf-8")))
                if manifest["logical_id"] != logical_id:
                    raise MigrationError("Archive logical ID does not match the existing project's logical ID.")
                for name in manifest["directories"]:
                    (target / name).mkdir(parents=True, exist_ok=True, mode=0o700)
                seen = set()
                while member := tar.next():
                    if not member.isreg() or member.sparse is not None or not member.name.startswith("state/"):
                        raise MigrationError("Only regular state files are allowed in the archive.")
                    name = _relative(member.name[6:])
                    if name not in expected or name in seen or member.size != expected[name]["size"]:
                        raise MigrationError("Archive member does not match its manifest.")
                    seen.add(name)
                    destination = target / name
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    stream = tar.extractfile(member)
                    if stream is None:
                        raise MigrationError("Archive member is unreadable.")
                    with destination.open("xb") as dst:
                        destination.chmod(0o600)
                        shutil.copyfileobj(stream, dst, CHUNK_SIZE)
                    if _sha256(destination) != expected[name]["sha256"]:
                        raise MigrationError("Restored file hash does not match its manifest.")
                    if expected[name]["sqlite"]:
                        _check_sqlite(destination)
                if seen != set(expected):
                    raise MigrationError("Archive is missing required state files.")
            receipt = _receipt(manifest, operation="verified_staging_restore")
            report = {**manifest, "restore_receipt": receipt}
            temporary_manifest = private / "verified-manifest.json"
            with temporary_manifest.open("xb") as output:
                temporary_manifest.chmod(0o600)
                output.write(json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8"))
                output.flush()
                os.fsync(output.fileno())
            # Both paths are on the same filesystem. link() publishes atomically
            # and refuses a destination created since the initial collision check.
            os.link(temporary_manifest, manifest_output, follow_symlinks=False)
            manifest_created = True
        return {**receipt, "verification_manifest_path": str(manifest_output)}
    except BaseException:
        if manifest_created:
            manifest_output.unlink(missing_ok=True)
        if created:
            shutil.rmtree(target)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--state-root", required=True)
    export.add_argument("--archive", required=True)
    export.add_argument("--writes-quiesced", action="store_true")
    export.add_argument("--accounts-not-initialized", action="store_true")
    export.add_argument("--logical-id", default=LOGICAL_ID)
    restore = commands.add_parser("restore")
    restore.add_argument("--archive", required=True)
    restore.add_argument("--staging-root", required=True)
    restore.add_argument("--logical-id", default=LOGICAL_ID)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            result = export_snapshot(args.state_root, args.archive, writes_quiesced=args.writes_quiesced,
                                     accounts_not_initialized=args.accounts_not_initialized, logical_id=args.logical_id)
        else:
            result = restore_snapshot(args.archive, args.staging_root, logical_id=args.logical_id)
    except (MigrationError, OSError, sqlite3.Error, tarfile.TarError, ValueError) as exc:
        # Do not print exception details from libraries: paths or secret contents
        # can be included in their messages. Known operator errors are safe.
        message = str(exc) if isinstance(exc, MigrationError) else "Migration failed; inspect source integrity and private storage."
        print(json.dumps({"ok": False, "error": message, "full_service_restore_verified": False}))
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
