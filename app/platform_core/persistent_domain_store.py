from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException

from app.services.secret_vault import state_root


Domain = Literal["life", "medical", "legal"]


def _key() -> bytes:
    raw = os.getenv("LIFERECOVER_DATA_KEY", "").strip()
    if not raw:
        raise HTTPException(status_code=503, detail="liferecover_data_key_not_configured")
    try:
        if len(raw) == 64 and all(c in "0123456789abcdefABCDEF" for c in raw):
            key = bytes.fromhex(raw)
        else:
            key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="liferecover_data_key_invalid") from exc
    if len(key) != 32:
        raise HTTPException(status_code=503, detail="liferecover_data_key_invalid")
    return key


def _db_path(domain: Domain) -> Path:
    root = state_root() / "liferecover"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    path = root / f"{domain}.sqlite3"
    return path


@contextmanager
def _db(domain: Domain):
    conn = sqlite3.connect(_db_path(domain))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS resource (
            resource_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            nonce BLOB NOT NULL,
            ciphertext BLOB NOT NULL,
            payload_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_resource_subject ON resource(subject_id, kind)")
        conn.commit()
        yield conn
    finally:
        conn.close()


def _aad(domain: Domain, subject_id: str, resource_id: str, kind: str) -> bytes:
    return f"{domain}|{subject_id}|{resource_id}|{kind}".encode("utf-8")


def put_resource(
    *,
    domain: Domain,
    subject_id: str,
    kind: str,
    payload: dict[str, Any],
    resource_id: str | None = None,
) -> str:
    resource_id = resource_id or f"{domain[:3]}_{uuid.uuid4().hex}"
    plaintext = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key()).encrypt(nonce, plaintext, _aad(domain, subject_id, resource_id, kind))
    digest = hashlib.sha256(plaintext).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    with _db(domain) as conn:
        existing = conn.execute(
            "SELECT created_at FROM resource WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        conn.execute(
            """
            INSERT INTO resource(resource_id,subject_id,kind,nonce,ciphertext,payload_sha256,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(resource_id) DO UPDATE SET
              subject_id=excluded.subject_id,
              kind=excluded.kind,
              nonce=excluded.nonce,
              ciphertext=excluded.ciphertext,
              payload_sha256=excluded.payload_sha256,
              updated_at=excluded.updated_at
            """,
            (resource_id, subject_id, kind, nonce, ciphertext, digest, created_at, now),
        )
        conn.commit()
    return resource_id


def get_resource(*, domain: Domain, subject_id: str, resource_id: str) -> dict[str, Any] | None:
    with _db(domain) as conn:
        row = conn.execute(
            "SELECT * FROM resource WHERE resource_id = ? AND subject_id = ?",
            (resource_id, subject_id),
        ).fetchone()
    if not row:
        return None
    plaintext = AESGCM(_key()).decrypt(
        row["nonce"],
        row["ciphertext"],
        _aad(domain, subject_id, resource_id, row["kind"]),
    )
    if hashlib.sha256(plaintext).hexdigest() != row["payload_sha256"]:
        raise HTTPException(status_code=500, detail="stored_payload_integrity_failure")
    return {
        "resource_id": resource_id,
        "kind": row["kind"],
        "payload": json.loads(plaintext.decode("utf-8")),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_resources(*, domain: Domain, subject_id: str, kind: str | None = None) -> list[dict[str, Any]]:
    with _db(domain) as conn:
        if kind:
            rows = conn.execute(
                "SELECT resource_id FROM resource WHERE subject_id=? AND kind=? ORDER BY updated_at DESC",
                (subject_id, kind),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT resource_id FROM resource WHERE subject_id=? ORDER BY updated_at DESC",
                (subject_id,),
            ).fetchall()
    return [
        item
        for row in rows
        if (item := get_resource(domain=domain, subject_id=subject_id, resource_id=row["resource_id"])) is not None
    ]


def storage_status() -> dict[str, Any]:
    root = state_root() / "liferecover"
    return {
        "root": str(root),
        "domains": {
            d: {"db_exists": _db_path(d).exists()}
            for d in ("life", "medical", "legal")
        },
        "data_key_configured": bool(os.getenv("LIFERECOVER_DATA_KEY", "").strip()),
        "encrypted_payloads": True,
        "algorithm": "AES-256-GCM",
    }
