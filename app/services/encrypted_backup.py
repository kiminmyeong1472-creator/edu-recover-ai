"""Encrypted backup utilities for EduRecover private assets.

Security model
--------------
* EDURECOVER_BACKUP_MASTER_KEY is encryption-only and must never be reused as
  an external provider API key.
* The master key wraps a fresh random data-encryption key (DEK) per file.
* File contents are streamed through AES-256-GCM so large backups do not need
  to be loaded into memory.
* Secret values are never returned by status helpers or metadata results.
"""

from __future__ import annotations

import base64
import hashlib
import os
import struct
import uuid
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MASTER_KEY_ENV = "EDURECOVER_BACKUP_MASTER_KEY"
REQUIRED_ENV = "EDURECOVER_BACKUP_ENCRYPTION_REQUIRED"

_MAGIC = b"EDURBK01"
_VERSION = 1
_NONCE_SIZE = 12
_TAG_SIZE = 16
_DEK_SIZE = 32
_CHUNK_SIZE = 8 * 1024 * 1024
_HEADER = struct.Struct(">8sB12s12sH")
_AAD = _MAGIC + bytes([_VERSION])


class BackupEncryptionError(RuntimeError):
    """Base class for backup-encryption failures."""


class BackupKeyNotConfigured(BackupEncryptionError):
    """Raised when the backup master key is not configured."""


class BackupKeyInvalid(BackupEncryptionError):
    """Raised when the configured key cannot be used safely."""


class BackupCiphertextInvalid(BackupEncryptionError):
    """Raised when an encrypted backup is malformed or fails authentication."""


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _decode_master_key(raw: str) -> bytes:
    value = raw.strip()
    if not value:
        raise BackupKeyNotConfigured(f"{MASTER_KEY_ENV} is empty.")

    try:
        if value.lower().startswith("hex:"):
            key = bytes.fromhex(value[4:].strip())
        else:
            if value.lower().startswith("base64:"):
                value = value[7:].strip()
            padding = "=" * (-len(value) % 4)
            key = base64.urlsafe_b64decode(value + padding)
    except (ValueError, base64.binascii.Error) as exc:
        raise BackupKeyInvalid(
            f"{MASTER_KEY_ENV} must be a 32-byte key encoded as base64url or hex:..."
        ) from exc

    if len(key) != 32:
        raise BackupKeyInvalid(
            f"{MASTER_KEY_ENV} must decode to exactly 32 bytes; decoded length={len(key)}."
        )
    return key


def _master_key() -> bytes:
    raw = os.getenv(MASTER_KEY_ENV)
    if raw is None:
        raise BackupKeyNotConfigured(
            f"{MASTER_KEY_ENV} is not configured. Set it as a sealed deployment secret."
        )
    return _decode_master_key(raw)


def backup_encryption_status() -> dict[str, Any]:
    """Return non-secret readiness information only."""
    required = _bool_env(REQUIRED_ENV, True)
    raw = os.getenv(MASTER_KEY_ENV)
    configured = bool(raw and raw.strip())
    valid_format = False
    if configured:
        try:
            _decode_master_key(raw or "")
            valid_format = True
        except BackupEncryptionError:
            valid_format = False

    return {
        "required": required,
        "master_key_configured": configured,
        "master_key_format_valid": valid_format,
        "ready": (not required) or (configured and valid_format),
        "algorithm": "AES-256-GCM",
        "envelope_encryption": True,
        "per_file_random_dek": True,
        "streaming_large_file_support": True,
        "master_key_source": f"deployment secret environment variable: {MASTER_KEY_ENV}",
        "secret_value_exposed": False,
        "note": (
            "The backup master key is separate from OpenAI/Gemini/Claude API keys. "
            "It wraps a fresh random data-encryption key for each backup file."
        ),
    }


def assert_backup_encryption_ready() -> None:
    status = backup_encryption_status()
    if status["required"] and not status["ready"]:
        if not status["master_key_configured"]:
            raise BackupKeyNotConfigured(
                f"{MASTER_KEY_ENV} is required but not configured."
            )
        raise BackupKeyInvalid(
            f"{MASTER_KEY_ENV} is configured but is not a valid 32-byte key."
        )


def _safe_chmod_600(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def encrypt_file(source: str | Path, destination: str | Path) -> dict[str, Any]:
    """Encrypt one file with a random DEK wrapped by the deployment master key."""
    assert_backup_encryption_ready()
    master_key = _master_key()

    src = Path(source)
    dst = Path(destination)
    if not src.is_file():
        raise FileNotFoundError(src)
    dst.parent.mkdir(parents=True, exist_ok=True)

    dek = os.urandom(_DEK_SIZE)
    wrap_nonce = os.urandom(_NONCE_SIZE)
    data_nonce = os.urandom(_NONCE_SIZE)
    wrapped_dek = AESGCM(master_key).encrypt(wrap_nonce, dek, _AAD)

    header = _HEADER.pack(
        _MAGIC,
        _VERSION,
        wrap_nonce,
        data_nonce,
        len(wrapped_dek),
    )

    tmp = dst.with_name(dst.name + f".tmp-{uuid.uuid4().hex}")
    source_hash = hashlib.sha256()
    encrypted_hash = hashlib.sha256()
    bytes_read = 0

    encryptor = Cipher(algorithms.AES(dek), modes.GCM(data_nonce)).encryptor()
    encryptor.authenticate_additional_data(_AAD)

    try:
        with src.open("rb") as in_f, tmp.open("wb") as out_f:
            out_f.write(header)
            out_f.write(wrapped_dek)
            encrypted_hash.update(header)
            encrypted_hash.update(wrapped_dek)

            while True:
                chunk = in_f.read(_CHUNK_SIZE)
                if not chunk:
                    break
                bytes_read += len(chunk)
                source_hash.update(chunk)
                encrypted = encryptor.update(chunk)
                if encrypted:
                    out_f.write(encrypted)
                    encrypted_hash.update(encrypted)

            tail = encryptor.finalize()
            if tail:
                out_f.write(tail)
                encrypted_hash.update(tail)

            tag = encryptor.tag
            out_f.write(tag)
            encrypted_hash.update(tag)
            out_f.flush()
            os.fsync(out_f.fileno())

        _safe_chmod_600(tmp)
        os.replace(tmp, dst)
        _safe_chmod_600(dst)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    return {
        "source_name": src.name,
        "destination_name": dst.name,
        "source_size_bytes": bytes_read,
        "source_sha256": source_hash.hexdigest(),
        "encrypted_sha256": encrypted_hash.hexdigest(),
        "algorithm": "AES-256-GCM",
        "master_key_value_exposed": False,
        "per_file_random_dek": True,
    }


def decrypt_file(source: str | Path, destination: str | Path) -> dict[str, Any]:
    """Decrypt an EduRecover encrypted backup after GCM authentication succeeds."""
    assert_backup_encryption_ready()
    master_key = _master_key()

    src = Path(source)
    dst = Path(destination)
    if not src.is_file():
        raise FileNotFoundError(src)

    total_size = src.stat().st_size
    minimum_size = _HEADER.size + (_DEK_SIZE + _TAG_SIZE) + _TAG_SIZE
    if total_size < minimum_size:
        raise BackupCiphertextInvalid("Encrypted backup is too short.")

    with src.open("rb") as in_f:
        raw_header = in_f.read(_HEADER.size)
        try:
            magic, version, wrap_nonce, data_nonce, wrapped_len = _HEADER.unpack(raw_header)
        except struct.error as exc:
            raise BackupCiphertextInvalid("Encrypted backup header is invalid.") from exc

        if magic != _MAGIC or version != _VERSION:
            raise BackupCiphertextInvalid("Unsupported encrypted backup format.")

        wrapped_dek = in_f.read(wrapped_len)
        if len(wrapped_dek) != wrapped_len:
            raise BackupCiphertextInvalid("Wrapped data key is truncated.")

        try:
            dek = AESGCM(master_key).decrypt(wrap_nonce, wrapped_dek, _AAD)
        except Exception as exc:
            raise BackupCiphertextInvalid(
                "Master key mismatch or wrapped data key authentication failure."
            ) from exc

        ciphertext_start = in_f.tell()
        ciphertext_len = total_size - ciphertext_start - _TAG_SIZE
        if ciphertext_len < 0:
            raise BackupCiphertextInvalid("Encrypted backup payload is truncated.")

        in_f.seek(total_size - _TAG_SIZE)
        tag = in_f.read(_TAG_SIZE)
        if len(tag) != _TAG_SIZE:
            raise BackupCiphertextInvalid("Encrypted backup authentication tag is missing.")
        in_f.seek(ciphertext_start)

        decryptor = Cipher(algorithms.AES(dek), modes.GCM(data_nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(_AAD)

        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + f".tmp-{uuid.uuid4().hex}")
        plain_hash = hashlib.sha256()
        remaining = ciphertext_len
        bytes_written = 0

        try:
            with tmp.open("wb") as out_f:
                while remaining:
                    chunk = in_f.read(min(_CHUNK_SIZE, remaining))
                    if not chunk:
                        raise BackupCiphertextInvalid("Encrypted backup payload is truncated.")
                    remaining -= len(chunk)
                    plain = decryptor.update(chunk)
                    if plain:
                        out_f.write(plain)
                        plain_hash.update(plain)
                        bytes_written += len(plain)

                try:
                    tail = decryptor.finalize()
                except Exception as exc:
                    raise BackupCiphertextInvalid(
                        "Encrypted backup authentication failed; file may be altered or key may be wrong."
                    ) from exc
                if tail:
                    out_f.write(tail)
                    plain_hash.update(tail)
                    bytes_written += len(tail)

                out_f.flush()
                os.fsync(out_f.fileno())

            _safe_chmod_600(tmp)
            os.replace(tmp, dst)
            _safe_chmod_600(dst)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    return {
        "source_name": src.name,
        "destination_name": dst.name,
        "decrypted_size_bytes": bytes_written,
        "decrypted_sha256": plain_hash.hexdigest(),
        "algorithm": "AES-256-GCM",
        "master_key_value_exposed": False,
    }
