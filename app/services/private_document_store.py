"""Exact-byte private DOCX and photo storage on the persistent classroom volume.

Documents are never converted, recompressed, or rewritten.  A file is accepted
only after its caller-supplied digest (or import-manifest digest) matches the
bytes that were streamed to an atomic temporary file.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import struct
import unicodedata
import urllib.request
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.services.owner_basic_auth import require_owner
from app.services.secret_vault import state_root

logger = logging.getLogger(__name__)

private_document_router = APIRouter(
    prefix="/owner/private-documents",
    dependencies=[Depends(require_owner)],
    tags=["owner"],
)

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_DOCX_MEMBERS = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
_IMPORT_ENV = "EDURECOVER_DOCUMENT_IMPORT_JSON"
_DEFAULT_MAX_BYTES = 20 * 1024 * 1024
_HARD_MAX_BYTES = 64 * 1024 * 1024
_MEDIA_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


class PrivateDocumentError(ValueError):
    pass


def _max_bytes() -> int:
    try:
        value = int(os.getenv("EDURECOVER_PRIVATE_DOCX_MAX_BYTES", str(_DEFAULT_MAX_BYTES)))
    except ValueError:
        return _DEFAULT_MAX_BYTES
    return min(max(value, 1), _HARD_MAX_BYTES)


def _root() -> Path:
    root = state_root() / "private_documents"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


def _document_dir() -> Path:
    directory = _root() / "documents"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return directory


def _metadata_dir() -> Path:
    directory = _root() / "metadata"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return directory


def sanitize_filename(value: str) -> str:
    """Return one safe NFC-normalized DOCX/JPEG/PNG filename or raise."""
    name = unicodedata.normalize("NFC", value.strip())
    if not name or len(name) > 180:
        raise PrivateDocumentError("INVALID_FILENAME")
    if name in {".", ".."} or Path(name).name != name:
        raise PrivateDocumentError("INVALID_FILENAME")
    if any(ord(char) < 32 or char in {'/', '\\', '\x00'} for char in name):
        raise PrivateDocumentError("INVALID_FILENAME")
    if Path(name).suffix.lower() not in _MEDIA_TYPES:
        raise PrivateDocumentError("DOCUMENT_TYPE_NOT_ALLOWED")
    return name


def _metadata_path(name: str) -> Path:
    key = hashlib.sha256(name.encode("utf-8")).hexdigest()
    return _metadata_dir() / f"{key}.json"


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _validate_docx_container(path: Path) -> None:
    """Read-only OOXML sanity check; never writes or re-packs the document."""
    try:
        with zipfile.ZipFile(path, "r") as archive:
            members = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise PrivateDocumentError("INVALID_DOCX_CONTAINER") from exc
    if not _REQUIRED_DOCX_MEMBERS.issubset(members):
        raise PrivateDocumentError("INVALID_DOCX_CONTAINER")


def _validate_image_container(path: Path, suffix: str) -> None:
    """Read-only structural sanity check, not an image decoder or malware scan."""
    data = path.read_bytes()
    if suffix == ".png":
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise PrivateDocumentError("INVALID_IMAGE_CONTAINER")
        offset, saw_header, saw_data = 8, False, False
        while offset + 12 <= len(data):
            size = int.from_bytes(data[offset:offset + 4], "big")
            end = offset + 12 + size
            if end > len(data):
                break
            kind = data[offset + 4:offset + 8]
            payload = data[offset + 8:end - 4]
            crc = int.from_bytes(data[end - 4:end], "big")
            if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
                break
            if not saw_header:
                if kind != b"IHDR" or size != 13:
                    break
                width, height, depth, color, compression, filtering, interlace = struct.unpack(
                    ">IIBBBBB", payload
                )
                depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
                if (not width or not height or depth not in depths.get(color, set())
                        or compression or filtering or interlace not in {0, 1}):
                    break
                saw_header = True
            elif kind == b"IHDR":
                break
            if kind == b"IDAT" and size:
                saw_data = True
            if kind == b"IEND":
                if size == 0 and saw_data and end == len(data):
                    return
                break
            offset = end
    elif data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9"):
        offset, saw_frame = 2, False
        frame_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        while offset + 4 <= len(data):
            if data[offset] != 0xFF:
                break
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset + 3 > len(data):
                break
            marker = data[offset]
            size = int.from_bytes(data[offset + 1:offset + 3], "big")
            end = offset + 1 + size
            if size < 2 or end > len(data) - 2:
                break
            payload = data[offset + 3:end]
            if marker in frame_markers:
                if (len(payload) < 6 or not int.from_bytes(payload[1:3], "big")
                        or not int.from_bytes(payload[3:5], "big") or not payload[5]
                        or len(payload) != 6 + 3 * payload[5]):
                    break
                saw_frame = True
            if marker == 0xDA:  # Start of scan; entropy data is preserved without decoding.
                if (saw_frame and payload and payload[0]
                        and len(payload) == 4 + 2 * payload[0] and end < len(data) - 2):
                    return
                break
            offset = end
    raise PrivateDocumentError("INVALID_IMAGE_CONTAINER")


def _validate_document_container(path: Path, name: str) -> None:
    suffix = Path(name).suffix.lower()
    if suffix == ".docx":
        _validate_docx_container(path)
    elif suffix in {".jpg", ".jpeg", ".png"}:
        _validate_image_container(path, suffix)
    else:
        raise PrivateDocumentError("DOCUMENT_TYPE_NOT_ALLOWED")


def _write_metadata(name: str, sha256: str, size: int, source: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "filename": name,
        "sha256": sha256,
        "size": size,
        "stored_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
    }
    target = _metadata_path(name)
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, target)
        target.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return data


def _read_metadata(name: str) -> dict[str, Any]:
    path = _metadata_path(name)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("filename") == name:
                return data
        except (OSError, ValueError, TypeError):
            pass
    document = _document_dir() / name
    sha256, size = _sha256_file(document)
    return _write_metadata(name, sha256, size, "recovered")


def _existing_exact(name: str, expected_sha256: str, expected_size: int) -> dict[str, Any] | None:
    target = _document_dir() / name
    if not target.exists():
        return None
    if target.is_symlink() or not target.is_file():
        raise PrivateDocumentError("UNSAFE_DOCUMENT_PATH")
    actual_sha256, actual_size = _sha256_file(target)
    if actual_sha256 != expected_sha256 or actual_size != expected_size:
        raise PrivateDocumentError("FILENAME_CONFLICT")
    _validate_document_container(target, name)
    target.chmod(0o600)
    metadata = _read_metadata(name)
    metadata["idempotent"] = True
    return metadata


def _commit_temporary(
    temporary: Path,
    *,
    name: str,
    expected_sha256: str,
    expected_size: int,
    source: str,
) -> dict[str, Any]:
    actual_sha256, actual_size = _sha256_file(temporary)
    if actual_size != expected_size or actual_sha256 != expected_sha256:
        raise PrivateDocumentError("DOCUMENT_INTEGRITY_MISMATCH")
    _validate_document_container(temporary, name)
    existing = _existing_exact(name, expected_sha256, expected_size)
    if existing is not None:
        return existing
    target = _document_dir() / name
    temporary.chmod(0o600)
    try:
        # Hard-link publication is atomic and refuses to overwrite a competing file.
        os.link(temporary, target)
    except FileExistsError:
        existing = _existing_exact(name, expected_sha256, expected_size)
        if existing is None:  # pragma: no cover - defensive race guard
            raise PrivateDocumentError("FILENAME_CONFLICT")
        return existing
    target.chmod(0o600)
    metadata = _write_metadata(name, expected_sha256, expected_size, source)
    metadata["idempotent"] = False
    return metadata


def _validate_digest(value: str) -> str:
    value = value.lower().strip()
    if not _DIGEST_RE.fullmatch(value):
        raise PrivateDocumentError("INVALID_SHA256")
    return value


@private_document_router.get("")
async def list_private_documents() -> dict[str, Any]:
    documents: list[dict[str, Any]] = []
    for path in sorted(_document_dir().iterdir(), key=lambda item: item.name):
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in _MEDIA_TYPES:
            documents.append(_read_metadata(path.name))
    return {"count": len(documents), "documents": documents}


@private_document_router.post("")
async def upload_private_document(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=180),
    sha256: str = Query(..., min_length=64, max_length=64),
) -> dict[str, Any]:
    try:
        name = sanitize_filename(filename)
        expected_sha256 = _validate_digest(sha256)
    except PrivateDocumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    suffix = Path(name).suffix.lower()
    if content_type not in {_MEDIA_TYPES[suffix], "application/octet-stream"}:
        detail = "DOCX_CONTENT_TYPE_REQUIRED" if suffix == ".docx" else "IMAGE_CONTENT_TYPE_REQUIRED"
        raise HTTPException(status_code=415, detail=detail)

    temporary = _document_dir() / f".upload.{secrets.token_hex(16)}.tmp"
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("x+b") as handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > _max_bytes():
                    raise HTTPException(status_code=413, detail="DOCUMENT_TOO_LARGE")
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if size == 0:
            raise HTTPException(status_code=400, detail="EMPTY_DOCUMENT")
        if digest.hexdigest() != expected_sha256:
            raise HTTPException(status_code=422, detail="DOCUMENT_INTEGRITY_MISMATCH")
        try:
            metadata = _commit_temporary(
                temporary,
                name=name,
                expected_sha256=expected_sha256,
                expected_size=size,
                source="owner-upload",
            )
        except PrivateDocumentError as exc:
            status_code = 409 if str(exc) == "FILENAME_CONFLICT" else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return {"status": "stored", **metadata}
    finally:
        temporary.unlink(missing_ok=True)


@private_document_router.get("/{filename}")
async def download_private_document(filename: str):
    try:
        name = sanitize_filename(filename)
    except PrivateDocumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    path = _document_dir() / name
    if not path.is_file() or path.is_symlink():
        raise HTTPException(status_code=404, detail="DOCUMENT_NOT_FOUND")
    return FileResponse(
        path,
        filename=name,
        media_type=_MEDIA_TYPES[Path(name).suffix.lower()],
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Document-SHA256": _sha256_file(path)[0],
        },
    )


def _allowed_import_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        hostname = parsed.hostname.rstrip(".").lower()
        allowed = tuple(
            item.strip().lower()
            for item in os.getenv(
                "EDURECOVER_DOCUMENT_IMPORT_HOSTS",
                ".oaiusercontent.com,.blob.core.windows.net,.amazonaws.com",
            ).split(",")
            if item.strip()
        )
        if not any(hostname == suffix.lstrip(".") or hostname.endswith(suffix) for suffix in allowed):
            return False
        try:
            literal = ipaddress.ip_address(hostname)
            return not (literal.is_private or literal.is_loopback or literal.is_link_local)
        except ValueError:
            return True
    except ValueError:
        return False


def _download_import(url: str, temporary: Path, expected_size: int) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "EduRecover-private-document-import/1"})
    with urllib.request.urlopen(request, timeout=60) as response, temporary.open("x+b") as handle:
        size = 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > _max_bytes() or size > expected_size:
                raise PrivateDocumentError("DOCUMENT_TOO_LARGE")
            handle.write(chunk)
        handle.flush()
        os.fsync(handle.fileno())


def _parse_import_manifest(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise PrivateDocumentError("INVALID_IMPORT_MANIFEST") from exc
    if not isinstance(data, list) or not 1 <= len(data) <= 20:
        raise PrivateDocumentError("INVALID_IMPORT_MANIFEST")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict) or set(item) != {"url", "filename", "sha256", "size"}:
            raise PrivateDocumentError("INVALID_IMPORT_MANIFEST")
        name = sanitize_filename(str(item["filename"]))
        sha256 = _validate_digest(str(item["sha256"]))
        if isinstance(item["size"], bool):
            raise PrivateDocumentError("INVALID_IMPORT_MANIFEST")
        try:
            size = int(item["size"])
        except (TypeError, ValueError) as exc:
            raise PrivateDocumentError("INVALID_IMPORT_MANIFEST") from exc
        url = str(item["url"])
        if not 1 <= size <= _max_bytes() or not _allowed_import_url(url) or name in seen:
            raise PrivateDocumentError("INVALID_IMPORT_MANIFEST")
        seen.add(name)
        result.append({"url": url, "filename": name, "sha256": sha256, "size": size})
    return result


def _import_from_env_sync() -> dict[str, int]:
    raw = os.getenv(_IMPORT_ENV, "").strip()
    if not raw:
        return {"requested": 0, "stored": 0, "idempotent": 0, "failed": 0}
    try:
        manifest = _parse_import_manifest(raw)
    except PrivateDocumentError as exc:
        logger.error("private_document_import=FAILED stage=manifest error=%s", str(exc))
        return {"requested": 0, "stored": 0, "idempotent": 0, "failed": 1}

    summary = {"requested": len(manifest), "stored": 0, "idempotent": 0, "failed": 0}
    for index, item in enumerate(manifest):
        name = item["filename"]
        temporary = _document_dir() / f".import.{secrets.token_hex(16)}.tmp"
        try:
            existing = _existing_exact(name, item["sha256"], item["size"])
            if existing is not None:
                summary["idempotent"] += 1
                continue
            _download_import(item["url"], temporary, item["size"])
            metadata = _commit_temporary(
                temporary,
                name=name,
                expected_sha256=item["sha256"],
                expected_size=item["size"],
                source="startup-import",
            )
            summary["idempotent" if metadata["idempotent"] else "stored"] += 1
        except Exception as exc:  # keep the service up; logs never contain the signed URL
            summary["failed"] += 1
            logger.error(
                "private_document_import=FAILED item=%s filename_sha256=%s error_type=%s",
                index,
                hashlib.sha256(name.encode("utf-8")).hexdigest(),
                type(exc).__name__,
            )
        finally:
            temporary.unlink(missing_ok=True)
    # Keep the integrity result visible in Railway's default production log level.
    logger.warning(
        "private_document_import=COMPLETE requested=%s stored=%s idempotent=%s failed=%s",
        summary["requested"], summary["stored"], summary["idempotent"], summary["failed"],
    )
    return summary


async def import_private_documents_from_env() -> None:
    """Startup hook for a short-lived, environment-supplied signed-URL manifest."""
    await asyncio.to_thread(_import_from_env_sync)


def private_document_integrity_snapshot() -> dict[str, Any]:
    """Server-side exact-byte inventory for diagnostics and test verification."""
    documents = []
    for path in sorted(_document_dir().iterdir(), key=lambda item: item.name):
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in _MEDIA_TYPES:
            sha256, size = _sha256_file(path)
            documents.append({"filename": path.name, "sha256": sha256, "size": size})
    return {"count": len(documents), "documents": documents}
