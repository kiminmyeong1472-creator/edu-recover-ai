"""Streaming authenticated backups with separate owner-managed credentials.

EDURECOVER_BACKUP_OWNER_PASSPHRASE accepts the owner's exact password. Version 2
uses per-file random scrypt salt to derive a wrapping key, then AES-256-GCM to
wrap a fresh DEK. It takes priority for NEW backups, without changing any secret.

Version 1 backups still use EDURECOVER_BACKUP_MASTER_KEY and remain readable.
Keep that legacy key until old backups are safely migrated and restore-tested.
Changing either credential never silently migrates existing encrypted files.
"""

from __future__ import annotations

import base64
import hashlib
import os
import struct
import tempfile
from pathlib import Path
from typing import Any, BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


MASTER_KEY_ENV = "EDURECOVER_BACKUP_MASTER_KEY"
PASSPHRASE_ENV = "EDURECOVER_BACKUP_OWNER_PASSPHRASE"
REQUIRED_ENV = "EDURECOVER_BACKUP_ENCRYPTION_REQUIRED"

_MAGIC = b"EDURBK01"
_VERSION = 1  # Existing raw-key format; never reinterpret its ciphertext.
_PASSPHRASE_VERSION = 2
_NONCE_SIZE = 12
_TAG_SIZE = 16
_DEK_SIZE = 32
_SALT_SIZE = 16
_CHUNK_SIZE = 8 * 1024 * 1024
_HEADER = struct.Struct(">8sB12s12sH")
_AAD = _MAGIC + bytes([_VERSION])
# Parameters are fixed by format version, not attacker-controlled file fields.
_SCRYPT_N = 2**17
_SCRYPT_R = 8
_SCRYPT_P = 1


class BackupEncryptionError(RuntimeError):
    """Base class for backup-encryption failures."""


class BackupKeyNotConfigured(BackupEncryptionError):
    """The credential required by a backup format is missing."""


class BackupKeyInvalid(BackupEncryptionError):
    """The configured credential cannot be used."""


class BackupCiphertextInvalid(BackupEncryptionError):
    """A backup is malformed or fails authentication."""


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _decode_master_key(raw: str) -> bytes:
    """Decode the legacy key, preserving existing supported encodings."""
    value = raw.strip()
    if not value:
        raise BackupKeyNotConfigured(f"{MASTER_KEY_ENV} is empty.")
    try:
        if value.lower().startswith("hex:"):
            key = bytes.fromhex(value[4:].strip())
        elif len(value) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in value):
            key = bytes.fromhex(value)
        else:
            if value.lower().startswith("base64:"):
                value = value[7:].strip()
            key = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, base64.binascii.Error) as exc:
        raise BackupKeyInvalid("Legacy backup key encoding is invalid.") from exc
    if len(key) != _DEK_SIZE:
        raise BackupKeyInvalid("Legacy backup key must decode to 32 bytes.")
    return key


def _master_key() -> bytes:
    """Read only the legacy raw key; never substitute a provider API key."""
    raw = os.getenv(MASTER_KEY_ENV)
    if raw is None:
        raise BackupKeyNotConfigured(f"{MASTER_KEY_ENV} is required for legacy backups.")
    return _decode_master_key(raw)


def _passphrase_bytes() -> bytes:
    raw = os.getenv(PASSPHRASE_ENV)
    if raw is None:
        raise BackupKeyNotConfigured(f"{PASSPHRASE_ENV} is not configured.")
    if not raw.strip():
        raise BackupKeyInvalid(f"{PASSPHRASE_ENV} must not be empty.")
    try:
        # Preserve case, spaces and Unicode exactly. No hex/base64 interpretation.
        return raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BackupKeyInvalid("Owner backup password must be valid UTF-8.") from exc


def _derive_wrapping_key(salt: bytes) -> bytes:
    if len(salt) != _SALT_SIZE:
        raise BackupCiphertextInvalid("Backup salt is invalid.")
    return Scrypt(
        salt=salt, length=_DEK_SIZE, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    ).derive(_passphrase_bytes())


def backup_encryption_status() -> dict[str, Any]:
    """Report configuration only; never expose or fingerprint a credential."""
    use_password = os.getenv(PASSPHRASE_ENV) is not None
    source = PASSPHRASE_ENV if use_password else MASTER_KEY_ENV
    raw = os.getenv(source)
    configured = bool(raw and raw.strip())
    valid_format = False
    if configured:
        try:
            _passphrase_bytes() if use_password else _master_key()
            valid_format = True
        except BackupEncryptionError:
            pass
    return {
        "required": _bool_env(REQUIRED_ENV, True),
        "master_key_configured": configured,
        "master_key_format_valid": valid_format,
        "ready": configured and valid_format,
        "algorithm": "AES-256-GCM",
        "envelope_encryption": True,
        "per_file_random_dek": True,
        "streaming_large_file_support": True,
        "master_key_source": f"deployment secret environment variable: {source}",
        "key_input_mode": "owner_password" if use_password else "legacy_raw_key",
        "new_backup_format_version": _PASSPHRASE_VERSION if use_password else _VERSION,
        "owner_passphrase_supported": True,
        "owner_passphrase_variable": PASSPHRASE_ENV,
        "legacy_backup_read_supported": True,
        "secret_value_exposed": False,
        "note": (
            "The owner password takes priority for new backups. Keep the old raw key "
            "for version 1 restores. Replacing a password does not migrate existing "
            "backups. Readiness describes configuration, not a verified restore."
        ),
    }


def assert_backup_encryption_ready() -> None:
    # Explicit encryption always needs a credential, even if optional by policy.
    if os.getenv(PASSPHRASE_ENV) is not None:
        _passphrase_bytes()
    else:
        _master_key()


def _safe_chmod_600(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _paths(source: str | Path, destination: str | Path) -> tuple[Path, Path]:
    src, dst = Path(source), Path(destination)
    if not src.is_file():
        raise FileNotFoundError(src)
    if src.resolve() == dst.resolve():
        raise ValueError("Backup source and destination must be different files.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    return src, dst


def _private_output(dst: Path) -> tuple[Path, BinaryIO]:
    # mkstemp creates an owner-only file before any unauthenticated plaintext is written.
    fd, name = tempfile.mkstemp(prefix=dst.name + ".tmp-", dir=dst.parent)
    return Path(name), os.fdopen(fd, "wb")


def encrypt_file(source: str | Path, destination: str | Path) -> dict[str, Any]:
    """Encrypt one file with a new DEK; existing inputs and secrets are untouched."""
    assert_backup_encryption_ready()
    src, dst = _paths(source, destination)
    use_password = os.getenv(PASSPHRASE_ENV) is not None
    version = _PASSPHRASE_VERSION if use_password else _VERSION
    salt = os.urandom(_SALT_SIZE) if use_password else b""
    wrapping_key = _derive_wrapping_key(salt) if use_password else _master_key()
    dek = os.urandom(_DEK_SIZE)
    wrap_nonce, data_nonce = os.urandom(_NONCE_SIZE), os.urandom(_NONCE_SIZE)
    header = _HEADER.pack(_MAGIC, version, wrap_nonce, data_nonce, _DEK_SIZE + _TAG_SIZE)
    aad = header + salt if use_password else _AAD
    wrapped_dek = AESGCM(wrapping_key).encrypt(wrap_nonce, dek, aad)
    prefix = header + salt + wrapped_dek
    encryptor = Cipher(algorithms.AES(dek), modes.GCM(data_nonce)).encryptor()
    encryptor.authenticate_additional_data(aad)
    source_hash, encrypted_hash = hashlib.sha256(), hashlib.sha256()
    bytes_read = 0
    tmp, output = _private_output(dst)
    try:
        with output as out_f, src.open("rb") as in_f:
            out_f.write(prefix)
            encrypted_hash.update(prefix)
            while chunk := in_f.read(_CHUNK_SIZE):
                bytes_read += len(chunk)
                source_hash.update(chunk)
                encrypted = encryptor.update(chunk)
                out_f.write(encrypted)
                encrypted_hash.update(encrypted)
            tail = encryptor.finalize() + encryptor.tag
            out_f.write(tail)
            encrypted_hash.update(tail)
            out_f.flush()
            os.fsync(out_f.fileno())
        os.replace(tmp, dst)
        _safe_chmod_600(dst)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return {
        "source_name": src.name,
        "destination_name": dst.name,
        "source_size_bytes": bytes_read,
        "source_sha256": source_hash.hexdigest(),
        "encrypted_sha256": encrypted_hash.hexdigest(),
        "algorithm": "AES-256-GCM",
        "format_version": version,
        "master_key_value_exposed": False,
        "per_file_random_dek": True,
    }


def decrypt_file(source: str | Path, destination: str | Path) -> dict[str, Any]:
    """Restore using the format's credential; publish only authenticated plaintext."""
    src, dst = _paths(source, destination)
    with src.open("rb") as in_f:
        total_size = os.fstat(in_f.fileno()).st_size
        raw_header = in_f.read(_HEADER.size)
        try:
            magic, version, wrap_nonce, data_nonce, wrapped_len = _HEADER.unpack(raw_header)
        except struct.error as exc:
            raise BackupCiphertextInvalid("Encrypted backup header is invalid.") from exc
        if magic != _MAGIC or version not in {_VERSION, _PASSPHRASE_VERSION}:
            raise BackupCiphertextInvalid("Unsupported encrypted backup format.")
        if wrapped_len != _DEK_SIZE + _TAG_SIZE:
            raise BackupCiphertextInvalid("Wrapped data key length is invalid.")
        salt_len = _SALT_SIZE if version == _PASSPHRASE_VERSION else 0
        if total_size < _HEADER.size + salt_len + wrapped_len + _TAG_SIZE:
            raise BackupCiphertextInvalid("Encrypted backup is truncated.")
        salt = in_f.read(salt_len)
        wrapped_dek = in_f.read(wrapped_len)
        if len(salt) != salt_len or len(wrapped_dek) != wrapped_len:
            raise BackupCiphertextInvalid("Encrypted backup is truncated.")
        if version == _PASSPHRASE_VERSION:
            wrapping_key, aad = _derive_wrapping_key(salt), raw_header + salt
        else:
            # Old backups still use the OLD key, even when a new password is set.
            wrapping_key, aad = _master_key(), _AAD
        try:
            dek = AESGCM(wrapping_key).decrypt(wrap_nonce, wrapped_dek, aad)
        except InvalidTag as exc:
            raise BackupCiphertextInvalid("Backup credential mismatch or altered header.") from exc
        ciphertext_start = in_f.tell()
        remaining = total_size - ciphertext_start - _TAG_SIZE
        in_f.seek(total_size - _TAG_SIZE)
        tag = in_f.read(_TAG_SIZE)
        if len(tag) != _TAG_SIZE:
            raise BackupCiphertextInvalid("Backup authentication tag is missing.")
        in_f.seek(ciphertext_start)
        decryptor = Cipher(algorithms.AES(dek), modes.GCM(data_nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(aad)
        plain_hash, bytes_written = hashlib.sha256(), 0
        tmp, output = _private_output(dst)
        try:
            with output as out_f:
                while remaining:
                    chunk = in_f.read(min(_CHUNK_SIZE, remaining))
                    if not chunk:
                        raise BackupCiphertextInvalid("Backup payload is truncated.")
                    remaining -= len(chunk)
                    plain = decryptor.update(chunk)
                    out_f.write(plain)
                    plain_hash.update(plain)
                    bytes_written += len(plain)
                try:
                    tail = decryptor.finalize()
                except InvalidTag as exc:
                    raise BackupCiphertextInvalid("Backup payload authentication failed.") from exc
                out_f.write(tail)
                plain_hash.update(tail)
                bytes_written += len(tail)
                out_f.flush()
                os.fsync(out_f.fileno())
            os.replace(tmp, dst)
            _safe_chmod_600(dst)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
    return {
        "source_name": src.name,
        "destination_name": dst.name,
        "decrypted_size_bytes": bytes_written,
        "decrypted_sha256": plain_hash.hexdigest(),
        "algorithm": "AES-256-GCM",
        "format_version": version,
        "master_key_value_exposed": False,
    }


def run_backup_encryption_self_test() -> dict[str, Any]:
    """Test random non-user data only; never return secret values or file contents."""
    assert_backup_encryption_ready()
    payload = os.urandom(4096)
    expected_hash = hashlib.sha256(payload).hexdigest()
    with tempfile.TemporaryDirectory(prefix="edurecover-backup-selftest-") as tmpdir:
        root = Path(tmpdir)
        source, encrypted, restored = root / "source.bin", root / "backup.edurbk", root / "restored.bin"
        source.write_bytes(payload)
        enc = encrypt_file(source, encrypted)
        dec = decrypt_file(encrypted, restored)
        if not (
            hashlib.sha256(restored.read_bytes()).hexdigest()
            == dec["decrypted_sha256"] == enc["source_sha256"] == expected_hash
        ):
            raise BackupCiphertextInvalid("Backup self-test restore hash mismatch.")
    return {
        "ready": True,
        "roundtrip_verified": True,
        "algorithm": "AES-256-GCM",
        "format_version": enc["format_version"],
        "test_payload_bytes": len(payload),
        "temporary_artifacts_removed": True,
        "master_key_value_exposed": False,
    }
