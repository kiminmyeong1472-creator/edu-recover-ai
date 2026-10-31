"""Server-key seal for the continuous-capture record chain (used by liferevolve_burst).

    hash_i = SHA-256(canonical(prev_hash, i, time, event, body))
    seal_i = HMAC-SHA-256(ledger_key, hash_i)

The hash chain shows any change, removal or reordering of a record. The seal adds:
someone who can rewrite the database but does not hold the server key cannot rebuild
a chain that verifies. Receipts (index, hash) handed to the user prove later rewriting
even by someone who does hold the key.
"""
from __future__ import annotations

import hashlib
import hmac

from app.platform_core.persistent_domain_store import _key


def _ledger_key() -> bytes:
    return hmac.new(_key(), b"liferevolve-burst-ledger-v1", hashlib.sha256).digest()


def seal(h: str) -> str:
    return hmac.new(_ledger_key(), h.encode("ascii"), hashlib.sha256).hexdigest()


def seal_ok(h: str, value: str) -> bool:
    return hmac.compare_digest(seal(h), value or "")
