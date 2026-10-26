"""Prepaid ANU requests: one durable reservation per outbound attempt.

Numbers and payment credentials are never stored. An uncertain attempt keeps
its reservation; automatic retries cannot silently create another supplier bill.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.services.secret_vault import state_root


class QuotaError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)


def _positive(value: int, maximum: int = 1_000_000) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise QuotaError("QRNG_INVALID_COUNT", 422)


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", value):
        raise QuotaError("QRNG_INVALID_IDENTIFIER", 422)


def _receipt(provider: str, reference: str) -> str:
    if (not re.fullmatch(r"[a-z0-9_-]{2,40}", provider)
            or not isinstance(reference, str) or not 1 <= len(reference.strip()) <= 160):
        raise QuotaError("QRNG_INVALID_PAYMENT_REFERENCE", 422)
    return hashlib.sha256(json.dumps([provider, reference.strip()]).encode()).hexdigest()


def _path() -> Path:
    root = state_root()
    if root.is_symlink():
        raise QuotaError("QRNG_STORAGE_UNAVAILABLE", 503)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder = root / "qrng"
    if folder.is_symlink():
        raise QuotaError("QRNG_STORAGE_UNAVAILABLE", 503)
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    path = folder / "prepaid.sqlite3"
    if path.is_symlink():
        raise QuotaError("QRNG_STORAGE_UNAVAILABLE", 503)
    return path


@contextmanager
def _db():
    conn = None
    try:
        path = _path()
        conn = sqlite3.connect(path, timeout=15)
        path.chmod(0o600)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=FULL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS prepayment (
          receipt TEXT PRIMARY KEY, account_id TEXT NOT NULL,
          calls INTEGER NOT NULL CHECK(calls>0),
          remaining INTEGER NOT NULL CHECK(remaining>=0 AND remaining<=calls),
          amount_minor INTEGER NOT NULL CHECK(amount_minor>0), currency TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('ACTIVE','REVOKED')),
          verified_at INTEGER NOT NULL, verification TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS prepaid_account ON prepayment(account_id,state);
        CREATE TABLE IF NOT EXISTS invocation (
          account_id TEXT NOT NULL, request_id TEXT NOT NULL, receipt TEXT NOT NULL,
          length INTEGER NOT NULL, state TEXT NOT NULL,
          reserved_at INTEGER NOT NULL, utc_day TEXT NOT NULL,
          PRIMARY KEY(account_id,request_id)
        );
        CREATE INDEX IF NOT EXISTS invocation_day ON invocation(utc_day);
        """)
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except (OSError, sqlite3.Error):
        if conn is not None:
            conn.rollback()
        raise QuotaError("QRNG_STORAGE_UNAVAILABLE", 503) from None
    except BaseException:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()


def grant_verified_prepayment(*, account_id: str, provider: str, reference: str,
                             calls: int, amount_minor: int, currency: str) -> dict:
    """Called only after owner auth and manual settlement verification.

    This function does not verify a bank transfer or a payment-gateway event.
    The private owner API is the trust boundary, not a user's paid=true claim.
    """
    _identifier(account_id)
    _positive(calls)
    _positive(amount_minor, 10**12)
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise QuotaError("QRNG_INVALID_CURRENCY", 422)
    receipt = _receipt(provider, reference)
    with _db() as db:
        old = db.execute("SELECT * FROM prepayment WHERE receipt=?", (receipt,)).fetchone()
        if old:
            if any(old[k] != v for k, v in {"account_id": account_id, "calls": calls,
                       "amount_minor": amount_minor, "currency": currency}.items()):
                raise QuotaError("QRNG_PAYMENT_CONFLICT")
            if old["state"] != "ACTIVE":
                raise QuotaError("QRNG_PAYMENT_REVOKED")
            return {"receipt": receipt, "duplicate": True, "calls": old["calls"]}
        db.execute("INSERT INTO prepayment VALUES(?,?,?,?,?,?,'ACTIVE',?,'MANUAL_OWNER')",
                   (receipt, account_id, calls, calls, amount_minor, currency, int(time.time())))
    return {"receipt": receipt, "duplicate": False, "calls": calls}


def revoke_prepayment(*, provider: str, reference: str) -> dict:
    """Cancel unused entitlement after refund/chargeback review; no money is moved."""
    receipt = _receipt(provider, reference)
    with _db() as db:
        row = db.execute("SELECT remaining FROM prepayment WHERE receipt=?", (receipt,)).fetchone()
        if not row:
            raise QuotaError("QRNG_PAYMENT_NOT_FOUND", 404)
        db.execute("UPDATE prepayment SET remaining=0,state='REVOKED' WHERE receipt=?", (receipt,))
    return {"receipt": receipt, "revoked_unused_calls": row["remaining"]}


def balance(account_id: str) -> dict:
    _identifier(account_id)
    with _db() as db:
        left = db.execute("SELECT COALESCE(SUM(remaining),0) FROM prepayment "
                          "WHERE account_id=? AND state='ACTIVE'", (account_id,)).fetchone()[0]
        rows = db.execute("SELECT state,COUNT(*) AS n FROM invocation WHERE account_id=? "
                          "GROUP BY state", (account_id,)).fetchall()
    return {"remaining_calls": left, "attempts_by_state": {r["state"]: r["n"] for r in rows},
            "unit": "one_anu_outbound_request", "automatic_refill": False}


def reserve(*, account_id: str, request_id: str, length: int, daily_limit: int) -> None:
    _identifier(account_id)
    _identifier(request_id)
    _positive(length, 1024)
    _positive(daily_limit)
    day = datetime.now(timezone.utc).date().isoformat()
    with _db() as db:
        old = db.execute("SELECT state,length FROM invocation WHERE account_id=? AND request_id=?",
                         (account_id, request_id)).fetchone()
        if old:
            if old["length"] != length:
                raise QuotaError("QRNG_REQUEST_CONFLICT")
            raise QuotaError("QRNG_REQUEST_ALREADY_RESERVED")
        grant = db.execute("SELECT receipt FROM prepayment WHERE account_id=? AND state='ACTIVE' "
                           "AND remaining>0 ORDER BY verified_at,receipt LIMIT 1", (account_id,)).fetchone()
        if not grant:
            raise QuotaError("QRNG_PREPAYMENT_REQUIRED", 402)
        if db.execute("SELECT COUNT(*) FROM invocation WHERE utc_day=?", (day,)).fetchone()[0] >= daily_limit:
            raise QuotaError("QRNG_DAILY_LIMIT_REACHED", 429)
        db.execute("UPDATE prepayment SET remaining=remaining-1 WHERE receipt=?", (grant["receipt"],))
        db.execute("INSERT INTO invocation VALUES(?,?,?,?,'RESERVED',?,?)",
                   (account_id, request_id, grant["receipt"], length, int(time.time()), day))


def _dispatch(account_id: str, request_id: str) -> None:
    with _db() as db:
        row = db.execute("SELECT p.state AS payment_state,i.state FROM invocation i "
                         "JOIN prepayment p ON p.receipt=i.receipt "
                         "WHERE i.account_id=? AND i.request_id=?", (account_id, request_id)).fetchone()
        if not row or row["state"] != "RESERVED" or row["payment_state"] != "ACTIVE":
            raise QuotaError("QRNG_DISPATCH_DENIED")
        db.execute("UPDATE invocation SET state='DISPATCHING' WHERE account_id=? AND request_id=?",
                   (account_id, request_id))


def _finish(account_id: str, request_id: str, state: str) -> None:
    with _db() as db:
        db.execute("UPDATE invocation SET state=? WHERE account_id=? AND request_id=? "
                   "AND state='DISPATCHING'", (state, account_id, request_id))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch_numbers(length: int, api_key: str) -> list[int]:
    """One fixed HTTPS destination; no redirects, retry, or fallback provider."""
    url = "https://api.quantumnumbers.anu.edu.au/?" + urlencode({"length": length, "type": "uint16"})
    request = Request(url, headers={"x-api-key": api_key, "Accept": "application/json"})
    with build_opener(_NoRedirect()).open(request, timeout=20) as response:
        if response.status != 200:
            raise ValueError("invalid_status")
        data = response.read(16385)
    if len(data) > 16384:
        raise ValueError("response_too_large")
    body = json.loads(data)
    values = body.get("data") if isinstance(body, dict) else None
    if (not isinstance(body, dict) or body.get("success") is not True
            or body.get("type") != "uint16" or type(body.get("length")) is not int
            or body["length"] != length or not isinstance(values, list) or len(values) != length
            or any(type(n) is not int or not 0 <= n <= 65535 for n in values)):
        raise ValueError("invalid_provider_response")
    return values


def request_numbers(*, account_id: str, request_id: str, length: int) -> dict:
    """Every supplier request must pass the prepaid reservation before dispatch."""
    key = os.getenv("ANU_QRNG_API_KEY", "").strip()
    raw_limit = os.getenv("ANU_QRNG_DAILY_CALL_LIMIT", "")
    if (os.getenv("ANU_QRNG_ENABLED", "").strip().lower() != "true"
            or not key or len(key) > 1024 or any(not 33 <= ord(c) <= 126 for c in key)
            or not re.fullmatch(r"[1-9][0-9]{0,5}", raw_limit)):
        raise QuotaError("QRNG_NOT_CONFIGURED", 503)
    reserve(account_id=account_id, request_id=request_id, length=length, daily_limit=int(raw_limit))
    _dispatch(account_id, request_id)
    try:
        values = _fetch_numbers(length, key)
    except Exception:
        _finish(account_id, request_id, "UNKNOWN")
        raise QuotaError("QRNG_OUTCOME_UNKNOWN_NO_RETRY", 502) from None
    _finish(account_id, request_id, "SUCCEEDED")
    return {"request_id": request_id, "data": values, "length": length, "type": "uint16",
            "charged_calls": 1, "remaining_calls": balance(account_id)["remaining_calls"]}
