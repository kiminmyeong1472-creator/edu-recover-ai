"""Encrypted, verified backup destinations; no implicit cloud credentials.

The existing EDURBK envelope format is reused. Source material is never uploaded
in plaintext, and credential-bearing sources are never opened by this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.services.encrypted_backup import decrypt_file, encrypt_file
from app.training.trade_secret_backup_runner import StorageWriteReceipt
from app.training.trade_secret_engine import BackupTarget

CHUNK = 8 * 1024 * 1024
DRIVE_API = "https://www.googleapis.com/drive/v3"


class BackupStorageError(RuntimeError):
    """Safe error codes only; provider response bodies must not be logged."""

    def __init__(self, code: str):
        self.safe_code = code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", code) else "BACKUP_STORAGE_FAILED"
        super().__init__(self.safe_code)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def private_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise BackupStorageError("PRIVATE_DIRECTORY_REQUIRED")
    if path.stat().st_mode & 0o077:
        raise BackupStorageError("PRIVATE_DIRECTORY_PERMISSIONS_REQUIRED")


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class PreparedBackup:
    path: Path
    artifact_sha256: str
    plaintext_sha256: str
    metadata_only: bool


class EncryptedPayloads:
    """One temporary ciphertext per asset/revision, shared by both destinations."""

    def __init__(self, *, source_root: Path, staging_root: Path):
        self.source_root = source_root.resolve(strict=True)
        private_directory(staging_root)
        self._tmp = tempfile.TemporaryDirectory(prefix="backup-stage-", dir=staging_root)
        self.root = Path(self._tmp.name)
        self._cache: dict[tuple, PreparedBackup] = {}

    def close(self) -> None:
        self._tmp.cleanup()

    def prepare(self, *, asset, plan, source_ref: str | None) -> PreparedBackup:
        key = (asset.asset_id, asset.content_hash, plan.classification.value,
               plan.raw_content_backup_allowed)
        if key in self._cache:
            return self._cache[key]
        work = self.root / hashlib.sha256(repr(key).encode()).hexdigest()
        work.mkdir(mode=0o700)
        metadata_only = not plan.raw_content_backup_allowed
        if metadata_only:
            # No source path, secret value, credential fingerprint or context copy.
            source = work / "metadata.json"
            source.write_text(json.dumps({
                "asset_id": asset.asset_id, "kind": asset.kind.value,
                "classification": plan.classification.value,
                "metadata_only": True, "source_content_omitted": True,
            }, sort_keys=True), encoding="utf-8")
            source.chmod(0o600)
            expected = sha256_file(source)
        else:
            if not source_ref:
                raise BackupStorageError("SOURCE_REQUIRED")
            source = Path(source_ref)
            if source.is_symlink():
                raise BackupStorageError("SOURCE_SYMLINK_FORBIDDEN")
            source = source.resolve(strict=True)
            if not source.is_relative_to(self.source_root) or not source.is_file():
                raise BackupStorageError("SOURCE_OUTSIDE_APPROVED_ROOT")
            expected = asset.content_hash.lower()
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise BackupStorageError("SOURCE_SHA256_REQUIRED")
        encrypted = work / "payload.edurbk"
        result = encrypt_file(source, encrypted)
        if result["source_sha256"] != expected:
            encrypted.unlink(missing_ok=True)
            raise BackupStorageError("SOURCE_HASH_MISMATCH")
        if sha256_file(encrypted) != result["encrypted_sha256"]:
            raise BackupStorageError("CIPHERTEXT_HASH_MISMATCH")
        restored = work / "restore-check.bin"
        try:
            restored_result = decrypt_file(encrypted, restored)
            if restored_result["decrypted_sha256"] != expected:
                raise BackupStorageError("RESTORE_HASH_MISMATCH")
        finally:
            restored.unlink(missing_ok=True)
            if metadata_only:
                source.unlink(missing_ok=True)
        payload = PreparedBackup(encrypted, result["encrypted_sha256"], expected, metadata_only)
        self._cache[key] = payload
        return payload


def _receipt(asset, target, payload: PreparedBackup, ref: str) -> StorageWriteReceipt:
    return StorageWriteReceipt(
        asset_id=asset.asset_id, target=target, content_hash=asset.content_hash,
        storage_ref=ref, raw_content_written=False, encrypted=True,
        artifact_sha256=payload.artifact_sha256,
        stored_bytes=payload.path.stat().st_size,
        integrity_verified=True, restore_verified=True,
        metadata_only=payload.metadata_only,
    )


class ServerBackupAdapter:
    def __init__(self, root: Path, payloads: EncryptedPayloads):
        self.root, self.payloads = root, payloads

    def preflight(self, *, target: BackupTarget) -> None:
        if not target.value.startswith("SERVER_"):
            raise BackupStorageError("SERVER_TARGET_REQUIRED")
        private_directory(self.root)
        if not os.access(self.root, os.W_OK):
            raise BackupStorageError("SERVER_ROOT_NOT_WRITABLE")
        subdir = self.root / target.value.lower()
        if subdir.exists() or subdir.is_symlink():
            private_directory(subdir)

    def write(self, *, asset, decision, plan, target, source_ref):
        self.preflight(target=target)
        payload = self.payloads.prepare(asset=asset, plan=plan, source_ref=source_ref)
        folder = self.root / target.value.lower()
        folder.mkdir(mode=0o700, exist_ok=True)
        private_directory(folder)
        # Names reveal neither original filenames nor owner-supplied identifiers.
        name = payload.artifact_sha256 + ".edurbk"
        destination = folder / name
        if destination.exists() or destination.is_symlink():
            if destination.is_symlink() or sha256_file(destination) != payload.artifact_sha256:
                raise BackupStorageError("EXISTING_CIPHERTEXT_MISMATCH")
        else:
            used = sum(p.stat().st_size for p in self.root.glob("*/*.edurbk") if p.is_file() and not p.is_symlink())
            budget = 100 * 1024 * 1024
            if used + payload.path.stat().st_size > budget or shutil.disk_usage(self.root).free < 20 * 1024 * 1024 + payload.path.stat().st_size:
                raise BackupStorageError("LOCAL_BACKUP_CAPACITY_REACHED")
            fd, tmp = tempfile.mkstemp(prefix=".pending-", dir=folder)
            try:
                with os.fdopen(fd, "wb") as output, payload.path.open("rb") as source:
                    shutil.copyfileobj(source, output, CHUNK)
                    output.flush()
                    os.fsync(output.fileno())
                if sha256_file(Path(tmp)) != payload.artifact_sha256:
                    raise BackupStorageError("SERVER_COPY_HASH_MISMATCH")
                os.replace(tmp, destination)
                fsync_directory(folder)
            finally:
                Path(tmp).unlink(missing_ok=True)
        if sha256_file(destination) != payload.artifact_sha256:
            raise BackupStorageError("SERVER_READBACK_HASH_MISMATCH")
        return _receipt(asset, target, payload, str(destination))


class DriveBackupAdapter:
    """Explicit user OAuth for My Drive or service account for Shared Drive.

    ChatGPT's Drive connector is not an application credential. My Drive uploads
    use owner consent and app-created owner-only backup folders.
    The injected HTTP session makes failure paths testable without external calls.
    """

    def __init__(self, *, session: Any, folders: dict[BackupTarget, str], payloads: EncryptedPayloads, user_oauth: bool = False):
        self.session, self.folders, self.payloads = session, folders, payloads
        self.user_oauth = user_oauth

    @classmethod
    def from_environment(cls, *, payloads: EncryptedPayloads):
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession

        from app.training.drive_oauth import oauth_configured, authorized_session
        if oauth_configured():
            if os.getenv("EDURECOVER_BACKUP_DRIVE_SERVICE_ACCOUNT_FILE") or os.getenv("EDURECOVER_BACKUP_DRIVE_SERVICE_ACCOUNT_JSON"):
                raise BackupStorageError("AMBIGUOUS_DRIVE_CREDENTIALS")
            session, folders = authorized_session()
            return cls(session=session, folders={BackupTarget(k): v for k, v in folders.items()}, payloads=payloads, user_oauth=True)
        path = os.getenv("EDURECOVER_BACKUP_DRIVE_SERVICE_ACCOUNT_FILE")
        raw = os.getenv("EDURECOVER_BACKUP_DRIVE_SERVICE_ACCOUNT_JSON")
        if bool(path) == bool(raw):
            raise BackupStorageError("EXACTLY_ONE_EXPLICIT_DRIVE_CREDENTIAL_REQUIRED")
        if path:
            credential_path = Path(path)
            if credential_path.is_symlink() or not credential_path.is_file():
                raise BackupStorageError("DRIVE_CREDENTIAL_FILE_INVALID")
            if credential_path.stat().st_mode & 0o077:
                raise BackupStorageError("DRIVE_CREDENTIAL_FILE_PERMISSIONS_REQUIRED")
            raw = credential_path.read_text(encoding="utf-8")
        try:
            info = json.loads(raw)
            if (info.get("type") != "service_account"
                    or info.get("token_uri") != "https://oauth2.googleapis.com/token"):
                raise ValueError("Unsupported credential type or token endpoint")
            credentials = service_account.Credentials.from_service_account_info(
                info, scopes=["https://www.googleapis.com/auth/drive"])
            folders = {BackupTarget(k): v for k, v in json.loads(
                os.environ["EDURECOVER_BACKUP_DRIVE_FOLDERS_JSON"]).items()}
        except Exception:
            raise BackupStorageError("DRIVE_CONFIGURATION_INVALID") from None
        # No domain delegation, default credentials, API-key substitution or sharing edits.
        return cls(session=AuthorizedSession(credentials), folders=folders, payloads=payloads)

    def _request(self, method: str, url: str, **kwargs):
        try:
            response = self.session.request(method, url, timeout=(10, 120),
                                            allow_redirects=False, **kwargs)
        except Exception:
            raise BackupStorageError("DRIVE_TRANSPORT_FAILED") from None
        if response.status_code not in {200, 201, 308}:
            raise BackupStorageError(f"DRIVE_HTTP_{response.status_code}")
        return response

    def preflight(self, *, target: BackupTarget) -> None:
        folder = self.folders.get(target)
        if not target.value.startswith("DRIVE_") or not isinstance(folder, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", folder):
            raise BackupStorageError("APPROVED_DRIVE_FOLDER_REQUIRED")
        info = self._request("GET", f"{DRIVE_API}/files/{folder}", params={
            "supportsAllDrives": "true", "fields": "id,mimeType,trashed,driveId,ownedByMe,capabilities(canAddChildren)"
        }).json()
        if (info.get("id") != folder or info.get("trashed") is not False
                or info.get("mimeType") != "application/vnd.google-apps.folder"
                or not info.get("capabilities", {}).get("canAddChildren")):
            raise BackupStorageError("DRIVE_FOLDER_NOT_WRITABLE")
        if self.user_oauth:
            if not info.get("ownedByMe") or info.get("driveId"):
                raise BackupStorageError("OWNER_MY_DRIVE_FOLDER_REQUIRED")
        elif not info.get("driveId"):
            raise BackupStorageError("SERVICE_ACCOUNT_REQUIRES_SHARED_DRIVE")
        page = None
        while True:
            params = {"supportsAllDrives": "true", "fields": "nextPageToken,permissions(type,role)", "pageSize": 100}
            if page:
                params["pageToken"] = page
            permissions = self._request("GET", f"{DRIVE_API}/files/{folder}/permissions", params=params).json()
            if not permissions.get("permissions"):
                raise BackupStorageError("DRIVE_PERMISSIONS_UNVERIFIED")
            if any(p.get("type") not in {"user", "group"} for p in permissions["permissions"]):
                raise BackupStorageError("PUBLIC_OR_DOMAIN_DRIVE_SHARING_FORBIDDEN")
            if self.user_oauth and any(p.get("type") != "user" or p.get("role") != "owner" for p in permissions["permissions"]):
                raise BackupStorageError("OWNER_ONLY_BACKUP_FOLDER_REQUIRED")
            page = permissions.get("nextPageToken")
            if not page:
                break

    def write(self, *, asset, decision, plan, target, source_ref):
        self.preflight(target=target)
        payload = self.payloads.prepare(asset=asset, plan=plan, source_ref=source_ref)
        size = payload.path.stat().st_size
        folder = self.folders[target]
        started = self._request("POST", "https://www.googleapis.com/upload/drive/v3/files", params={
            "uploadType": "resumable", "supportsAllDrives": "true", "fields": "id"
        }, json={"name": payload.artifact_sha256 + ".edurbk", "parents": [folder],
                 "mimeType": "application/octet-stream"}, headers={
                     "X-Upload-Content-Type": "application/octet-stream",
                     "X-Upload-Content-Length": str(size)})
        upload_url = started.headers.get("Location", "")
        parsed = urlparse(upload_url)
        if parsed.scheme != "https" or parsed.hostname != "www.googleapis.com" or not parsed.path.startswith("/upload/drive/"):
            raise BackupStorageError("UNTRUSTED_UPLOAD_LOCATION")
        offset, result = 0, None
        with payload.path.open("rb") as source:
            while chunk := source.read(CHUNK):
                end = offset + len(chunk) - 1
                response = self._request("PUT", upload_url, data=chunk, headers={
                    "Content-Length": str(len(chunk)), "Content-Type": "application/octet-stream",
                    "Content-Range": f"bytes {offset}-{end}/{size}"})
                if end + 1 < size:
                    if response.status_code != 308 or response.headers.get("Range") != f"bytes=0-{end}":
                        raise BackupStorageError("UPLOAD_ACK_MISMATCH")
                else:
                    if response.status_code not in {200, 201}:
                        raise BackupStorageError("UPLOAD_NOT_COMPLETE")
                    result = response.json()
                offset = end + 1
        file_id = (result or {}).get("id", "")
        if not isinstance(file_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", file_id):
            raise BackupStorageError("UPLOAD_FILE_ID_MISSING")
        metadata = self._request("GET", f"{DRIVE_API}/files/{file_id}", params={
            "supportsAllDrives": "true", "fields": "id,parents,size,trashed"
        }).json()
        if (metadata.get("id") != file_id or metadata.get("trashed") is not False
                or metadata.get("parents") != [folder] or int(metadata.get("size", -1)) != size):
            raise BackupStorageError("DRIVE_METADATA_MISMATCH")
        response = self._request("GET", f"{DRIVE_API}/files/{file_id}", params={
            "alt": "media", "supportsAllDrives": "true"}, stream=True)
        digest, read = hashlib.sha256(), 0
        try:
            for chunk in response.iter_content(CHUNK):
                read += len(chunk)
                digest.update(chunk)
        finally:
            response.close()
        if read != size or digest.hexdigest() != payload.artifact_sha256:
            raise BackupStorageError("DRIVE_READBACK_HASH_MISMATCH")
        return _receipt(asset, target, payload, f"gdrive://{file_id}")
