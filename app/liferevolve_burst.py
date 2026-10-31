"""LifeRevolve continuous capture ("burst"): tap-by-tap screen frames.

Rules (추가기재안 청구항 18~27):
- a double tap (<=400ms apart, <=40px) opens a burst; each later tap adds one frame
- a tap within 120ms of the previous accepted tap is a duplicate and is dropped
- the burst closes on a long press or after 5s without a tap
- each frame records signal id, burst id, sequence number, tap time and SHA-256
- missing sequence numbers are reported as missing, never filled in
- frames reach a recipient (AI, room, chat) only under that recipient's approval,
  which has a purpose and an expiry and can be revoked
- an AI "I see your current screen" claim is checked against the latest frame:
  no frame / not sent to AI / past screen (hash mismatch or older than 3s) / current
- a screen the source app protects from capture (Android shows it to screen sharing as
  a black frame) is never stored or sent; only the fact "protected, blocked" is recorded
- learning provenance: a frame may be used to train or tune a model only under a
  separate "train" approval (an "ai" approval to look is not an approval to learn);
  every use names the model and data set and goes into the chain, so for any frame
  hash the chain shows whether, by which model and under which approval it was learned
- answer binding: each AI answer is hashed and bound to the frame hashes it cites;
  a citation counts as grounded only if that frame was really delivered to the AI
- no inference outside the user's choice (privacy): a capture-protected frame, or a
  frame the user never released to the AI, is outside what the user chose to give.
  Deliveries tell the AI which sequence numbers it must not guess at; an answer that
  says it describes such a frame is withheld and the violation is recorded
- revoking an approval records that the recipient's copies must be deleted, and the
  recipient's deletion confirmation is recorded too
- every event is appended to a hash chain (each record carries the previous record's
  SHA-256), so any later change, deletion or reordering of a record is detectable;
  each record is also sealed with a server key, the table refuses UPDATE and DELETE,
  and the user can keep receipts (index, hash) that expose even a full rebuild
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from PIL import Image
from starlette.concurrency import run_in_threadpool

from app.platform_core.life_auth import require_life_user
from app.platform_core.persistent_domain_store import get_resource, resource_transaction
from app.liferevolve_ledger import seal, seal_ok
from app.liferevolve_screen import CACHE_HEADERS, MAX_BODY_BYTES, _mutation_allowed, _store, _validated_image

router = APIRouter(tags=["liferevolve-burst"])
DOUBLE_TAP_MS = 400
DOUBLE_TAP_PX = 40
DEDUPE_MS = 120
IDLE_CLOSE_MS = 5000
CURRENT_MS = 3000
MAX_BURST_FRAMES = 24
RECIPIENTS = {"ai", "room", "chat"}
GRANT_RECIPIENTS = RECIPIENTS | {"train"}
PROTECTED_MAX_LEVEL = 8  # every pixel this dark → treated as a capture-protected screen
GENESIS = "0" * 64


def looks_protected(raw: bytes) -> bool:
    """Android blanks capture-protected windows (FLAG_SECURE) to black in screen sharing.
    We never try to get around that; an all-black frame is simply not sent."""
    with Image.open(io.BytesIO(raw)) as im:
        lo, hi = im.convert("L").getextrema()
    return hi <= PROTECTED_MAX_LEVEL


def record_hash(prev: str, idx: int, event: str, body: dict, at: str) -> str:
    canon = json.dumps({"prev": prev, "idx": idx, "event": event, "body": body, "at": at},
                       ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def verify_chain(records: list[dict], receipts: list[dict] | None = None) -> dict:
    prev = GENESIS
    for i, r in enumerate(records):
        if r["idx"] != i:
            return {"intact": False, "broken_at": i, "reason": "INDEX_GAP"}
        if r["prev_hash"] != prev:
            return {"intact": False, "broken_at": i, "reason": "PREV_MISMATCH"}
        if record_hash(prev, i, r["event"], r["body"], r["at"]) != r["hash"]:
            return {"intact": False, "broken_at": i, "reason": "HASH_MISMATCH"}
        if "seal" in r and not seal_ok(r["hash"], r["seal"]):
            return {"intact": False, "broken_at": i, "reason": "SEAL_INVALID"}
        prev = r["hash"]
    by_idx = {r["idx"]: r["hash"] for r in records}
    for rc in receipts or []:
        if by_idx.get(rc.get("idx")) != rc.get("hash"):
            return {"intact": False, "broken_at": rc.get("idx"), "reason": "RECEIPT_MISMATCH"}
    return {"intact": True, "length": len(records), "head_hash": prev}


def classify_tap(prev: dict | None, tap: dict) -> str:
    """Gesture rule shared with the Android overlay. tap = {t_ms, x, y, long}."""
    if tap.get("long"):
        return "close"
    if prev is None:
        return "single"
    dt = tap["t_ms"] - prev["t_ms"]
    dist = ((tap["x"] - prev["x"]) ** 2 + (tap["y"] - prev["y"]) ** 2) ** 0.5
    if dt <= DEDUPE_MS:
        return "duplicate"
    if dt <= DOUBLE_TAP_MS and dist <= DOUBLE_TAP_PX:
        return "double"
    return "single"


def missing_sequences(seqs: list[int]) -> list[int]:
    if not seqs:
        return []
    have = set(seqs)
    return [n for n in range(1, max(seqs) + 1) if n not in have]


def recognition_status(frame: dict | None, sent_to_ai: bool, claimed_sha: str, now: datetime) -> str:
    if frame is None:
        return "NO_FRAME"
    if not sent_to_ai:
        return "NOT_SENT_TO_AI"
    age_ms = (now - datetime.fromisoformat(frame["tapped_at"])).total_seconds() * 1000
    if claimed_sha != frame["sha256"] or age_ms > CURRENT_MS:
        return "PAST_SCREEN"
    return "CURRENT"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tables(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS liferevolve_burst (
        burst_id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, signal_id TEXT NOT NULL,
        status TEXT NOT NULL, opened_at TEXT NOT NULL, last_tap_at TEXT NOT NULL,
        last_tap_ms INTEGER NOT NULL, closed_at TEXT, close_reason TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS liferevolve_burst_frame (
        burst_id TEXT NOT NULL, seq INTEGER NOT NULL, resource_id TEXT NOT NULL,
        tapped_at TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY (burst_id, seq))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS liferevolve_burst_grant (
        grant_id TEXT PRIMARY KEY, burst_id TEXT NOT NULL, recipient TEXT NOT NULL,
        purpose TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS liferevolve_burst_delivery (
        burst_id TEXT NOT NULL, seq INTEGER NOT NULL, recipient TEXT NOT NULL,
        grant_id TEXT NOT NULL, delivered_at TEXT NOT NULL, PRIMARY KEY (burst_id, seq, recipient))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS liferevolve_burst_ledger (
        burst_id TEXT NOT NULL, idx INTEGER NOT NULL, event TEXT NOT NULL, body TEXT NOT NULL,
        at TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL, seal TEXT NOT NULL,
        PRIMARY KEY (burst_id, idx))""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS liferevolve_burst_ledger_no_update BEFORE UPDATE ON liferevolve_burst_ledger
        BEGIN SELECT RAISE(ABORT, 'ledger_append_only'); END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS liferevolve_burst_ledger_no_delete BEFORE DELETE ON liferevolve_burst_ledger
        BEGIN SELECT RAISE(ABORT, 'ledger_append_only'); END""")


def _log(conn, bid: str, event: str, body: dict) -> str:
    last = conn.execute("SELECT idx, hash FROM liferevolve_burst_ledger WHERE burst_id=? ORDER BY idx DESC LIMIT 1", (bid,)).fetchone()
    idx, prev = (last["idx"] + 1, last["hash"]) if last else (0, GENESIS)
    at = _now().isoformat()
    h = record_hash(prev, idx, event, body, at)
    conn.execute("INSERT INTO liferevolve_burst_ledger VALUES(?,?,?,?,?,?,?,?)",
                 (bid, idx, event, json.dumps(body, ensure_ascii=False, sort_keys=True), at, prev, h, seal(h)))
    return h


def _ledger(conn, bid: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM liferevolve_burst_ledger WHERE burst_id=? ORDER BY idx", (bid,)).fetchall()
    return [{"idx": r["idx"], "event": r["event"], "body": json.loads(r["body"]), "at": r["at"],
             "prev_hash": r["prev_hash"], "hash": r["hash"], "seal": r["seal"]} for r in rows]


def _burst(conn, account: str, bid: str):
    row = conn.execute("SELECT * FROM liferevolve_burst WHERE burst_id=? AND subject_id=?", (bid, account)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="burst_not_found")
    if row["status"] == "OPEN" and (_now() - datetime.fromisoformat(row["last_tap_at"])).total_seconds() * 1000 > IDLE_CLOSE_MS:
        conn.execute("UPDATE liferevolve_burst SET status='CLOSED', closed_at=?, close_reason='IDLE' WHERE burst_id=?", (_now().isoformat(), bid))
        _log(conn, bid, "CLOSED", {"reason": "IDLE"})
        row = conn.execute("SELECT * FROM liferevolve_burst WHERE burst_id=?", (bid,)).fetchone()
    return row


def _summary(conn, row) -> dict:
    frames = conn.execute("SELECT seq, resource_id, tapped_at, sha256 FROM liferevolve_burst_frame WHERE burst_id=? ORDER BY seq", (row["burst_id"],)).fetchall()
    seqs = [f["seq"] for f in frames]
    protected = sorted(r["body"]["seq"] for r in _ledger(conn, row["burst_id"]) if r["event"] == "PROTECTED_BLOCKED")
    return {
        "burst_id": row["burst_id"], "signal_id": row["signal_id"], "status": row["status"],
        "opened_at": row["opened_at"], "closed_at": row["closed_at"], "close_reason": row["close_reason"],
        "frames": [{"seq": f["seq"], "screen_id": f["resource_id"], "tapped_at": f["tapped_at"], "sha256": f["sha256"]} for f in frames],
        "protected_sequences": protected,
        "missing_sequences": [n for n in missing_sequences(seqs + protected) if n not in protected],
    }


async def _json(request: Request) -> dict:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="burst_json_required")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES + 512:
            raise HTTPException(status_code=413, detail="burst_request_too_large")
        body.extend(chunk)
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(status_code=422, detail="burst_invalid_json") from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="burst_invalid_request")
    return data


@router.post("/life/api/burst", status_code=201)
async def open_burst(request: Request):
    """Called by the client after it recognised a double tap."""
    account = require_life_user(request)
    _mutation_allowed(request)
    data = await _json(request)
    tap_ms = data.get("tap_ms")
    if not isinstance(tap_ms, int):
        raise HTTPException(status_code=422, detail="burst_tap_ms_required")
    bid, now = "bst_" + uuid.uuid4().hex, _now().isoformat()
    with resource_transaction("life") as conn:
        _tables(conn)
        conn.execute("UPDATE liferevolve_burst SET status='CLOSED', closed_at=?, close_reason='REPLACED' WHERE subject_id=? AND status='OPEN'", (now, account))
        sig = "sig_" + uuid.uuid4().hex
        conn.execute("INSERT INTO liferevolve_burst VALUES(?,?,?,?,?,?,?,NULL,NULL)", (bid, account, sig, "OPEN", now, now, tap_ms))
        _log(conn, bid, "OPENED", {"signal_id": sig, "tap_ms": tap_ms})
        result = _summary(conn, _burst(conn, account, bid))
    return JSONResponse({"burst": result}, status_code=201, headers=CACHE_HEADERS)


@router.post("/life/api/burst/{bid}/frames", status_code=201)
async def add_frame(bid: str, request: Request):
    account = require_life_user(request)
    _mutation_allowed(request)
    data = await _json(request)
    seq, tap_ms = data.get("seq"), data.get("tap_ms")
    if not isinstance(seq, int) or seq < 1 or not isinstance(tap_ms, int):
        raise HTTPException(status_code=422, detail="burst_seq_and_tap_ms_required")
    with resource_transaction("life") as conn:
        _tables(conn)
        row = _burst(conn, account, bid)
        if row["status"] != "OPEN":
            raise HTTPException(status_code=409, detail="burst_closed")
        if conn.execute("SELECT 1 FROM liferevolve_burst_frame WHERE burst_id=? AND seq=?", (bid, seq)).fetchone():
            raise HTTPException(status_code=409, detail="burst_seq_exists")
        if tap_ms - row["last_tap_ms"] <= DEDUPE_MS and seq > 1:
            _log(conn, bid, "DUPLICATE_TAP", {"seq": seq, "tap_ms": tap_ms})
            return JSONResponse({"accepted": False, "reason": "DUPLICATE_TAP"}, status_code=200, headers=CACHE_HEADERS)
        if conn.execute("SELECT COUNT(*) FROM liferevolve_burst_frame WHERE burst_id=?", (bid,)).fetchone()[0] >= MAX_BURST_FRAMES:
            raise HTTPException(status_code=429, detail="burst_frame_limit")
    protected = bool(data.get("protected")) or await run_in_threadpool(lambda: looks_protected(_validated_image(data.get("base64"))[0]))
    if protected:
        # The image is dropped here: never stored, never sent. Only the fact is recorded.
        with resource_transaction("life") as conn:
            _log(conn, bid, "PROTECTED_BLOCKED", {"seq": seq, "tap_ms": tap_ms, "by": "device" if data.get("protected") else "server"})
            conn.execute("UPDATE liferevolve_burst SET last_tap_at=?, last_tap_ms=? WHERE burst_id=?", (_now().isoformat(), tap_ms, bid))
            result = _summary(conn, _burst(conn, account, bid))
        return JSONResponse({"accepted": False, "reason": "PROTECTED_SCREEN", "burst": result}, status_code=200, headers=CACHE_HEADERS)
    item = await run_in_threadpool(_store, account, {"name": data.get("name") or f"연속화면 {seq}", "base64": data.get("base64")})
    sha = get_resource(domain="life", subject_id=account, resource_id=item["id"])["payload"]["sha256"]
    now = _now().isoformat()
    with resource_transaction("life") as conn:
        conn.execute("INSERT INTO liferevolve_burst_frame VALUES(?,?,?,?,?)", (bid, seq, item["id"], now, sha))
        _log(conn, bid, "FRAME", {"seq": seq, "tap_ms": tap_ms, "sha256": sha})
        conn.execute("UPDATE liferevolve_burst SET last_tap_at=?, last_tap_ms=? WHERE burst_id=?", (now, tap_ms, bid))
        result = _summary(conn, _burst(conn, account, bid))
    return JSONResponse({"accepted": True, "seq": seq, "sha256": sha, "burst": result}, status_code=201, headers=CACHE_HEADERS)


@router.post("/life/api/burst/{bid}/close")
async def close_burst(bid: str, request: Request):
    account = require_life_user(request)
    _mutation_allowed(request)
    with resource_transaction("life") as conn:
        _tables(conn)
        row = _burst(conn, account, bid)
        if row["status"] == "OPEN":
            conn.execute("UPDATE liferevolve_burst SET status='CLOSED', closed_at=?, close_reason='LONG_PRESS' WHERE burst_id=?", (_now().isoformat(), bid))
            _log(conn, bid, "CLOSED", {"reason": "LONG_PRESS"})
        result = _summary(conn, _burst(conn, account, bid))
    return JSONResponse({"burst": result}, headers=CACHE_HEADERS)


@router.get("/life/api/burst/{bid}")
def get_burst(bid: str, request: Request):
    account = require_life_user(request)
    with resource_transaction("life") as conn:
        _tables(conn)
        result = _summary(conn, _burst(conn, account, bid))
    return JSONResponse({"burst": result}, headers=CACHE_HEADERS)


@router.post("/life/api/burst/{bid}/grants", status_code=201)
async def grant(bid: str, request: Request):
    """The user approves one recipient for one purpose, for a limited time."""
    account = require_life_user(request)
    _mutation_allowed(request)
    data = await _json(request)
    recipient, purpose, minutes = data.get("recipient"), data.get("purpose"), data.get("minutes", 10)
    if recipient not in GRANT_RECIPIENTS or not isinstance(purpose, str) or not purpose.strip() or not isinstance(minutes, int) or not 1 <= minutes <= 120:
        raise HTTPException(status_code=422, detail="burst_invalid_grant")
    gid = "grt_" + uuid.uuid4().hex
    expires = (_now() + timedelta(minutes=minutes)).isoformat()
    with resource_transaction("life") as conn:
        _tables(conn)
        _burst(conn, account, bid)
        conn.execute("INSERT INTO liferevolve_burst_grant VALUES(?,?,?,?,?,NULL)", (gid, bid, recipient, purpose.strip()[:200], expires))
        _log(conn, bid, "GRANTED", {"grant_id": gid, "recipient": recipient, "purpose": purpose.strip()[:200], "expires_at": expires})
    return JSONResponse({"grant_id": gid, "recipient": recipient, "expires_at": expires}, status_code=201, headers=CACHE_HEADERS)


@router.delete("/life/api/burst/{bid}/grants/{gid}", status_code=204)
def revoke(bid: str, gid: str, request: Request):
    account = require_life_user(request)
    _mutation_allowed(request)
    with resource_transaction("life") as conn:
        _tables(conn)
        _burst(conn, account, bid)
        g = conn.execute("SELECT recipient FROM liferevolve_burst_grant WHERE grant_id=? AND burst_id=? AND revoked_at IS NULL", (gid, bid)).fetchone()
        if g is None:
            raise HTTPException(status_code=404, detail="grant_not_found")
        conn.execute("UPDATE liferevolve_burst_grant SET revoked_at=? WHERE grant_id=?", (_now().isoformat(), gid))
        _log(conn, bid, "REVOKED", {"grant_id": gid, "recipient": g["recipient"], "copies": "DELETION_REQUIRED"})
    return JSONResponse(None, status_code=204, headers=CACHE_HEADERS)


def _active_grant(conn, bid: str, recipient: str):
    return conn.execute("SELECT grant_id FROM liferevolve_burst_grant WHERE burst_id=? AND recipient=? AND revoked_at IS NULL AND expires_at>? ORDER BY expires_at DESC", (bid, recipient, _now().isoformat())).fetchone()


@router.post("/life/api/burst/{bid}/deliver")
async def deliver(bid: str, request: Request):
    """Release frames to a recipient. Without an active grant nothing is released."""
    account = require_life_user(request)
    _mutation_allowed(request)
    data = await _json(request)
    recipient, seqs = data.get("recipient"), data.get("seqs")
    if recipient not in RECIPIENTS or not isinstance(seqs, list) or not all(isinstance(s, int) for s in seqs):
        raise HTTPException(status_code=422, detail="burst_invalid_delivery")
    with resource_transaction("life") as conn:
        _tables(conn)
        _burst(conn, account, bid)
        g = _active_grant(conn, bid, recipient)
        if g is None:
            _log(conn, bid, "DELIVERY_REFUSED", {"recipient": recipient, "seqs": seqs})
    if g is None:  # raised after the transaction so the refusal record is kept
        raise HTTPException(status_code=403, detail="burst_recipient_not_approved")
    with resource_transaction("life") as conn:
        have = {r["seq"] for r in conn.execute("SELECT seq FROM liferevolve_burst_frame WHERE burst_id=?", (bid,))}
        sent, missing = [], []
        for s in seqs:
            if s not in have:
                missing.append(s)
                continue
            conn.execute("INSERT OR IGNORE INTO liferevolve_burst_delivery VALUES(?,?,?,?,?)", (bid, s, recipient, g["grant_id"], _now().isoformat()))
            sent.append(s)
        banned = _off_limits(conn, bid, recipient)
        _log(conn, bid, "DELIVERED", {"recipient": recipient, "grant_id": g["grant_id"], "delivered": sent, "missing": missing, "do_not_infer": banned})
    return JSONResponse({"recipient": recipient, "delivered": sent, "missing": missing, "do_not_infer": banned}, headers=CACHE_HEADERS)


@router.post("/life/api/burst/{bid}/recognition-check")
async def recognition_check(bid: str, request: Request):
    """Check an AI's claim that it sees the user's current screen."""
    account = require_life_user(request)
    _mutation_allowed(request)
    data = await _json(request)
    claimed = data.get("sha256", "")
    with resource_transaction("life") as conn:
        _tables(conn)
        _burst(conn, account, bid)
        f = conn.execute("SELECT * FROM liferevolve_burst_frame WHERE burst_id=? ORDER BY seq DESC LIMIT 1", (bid,)).fetchone()
        frame = dict(f) if f else None
        sent = bool(f) and conn.execute("SELECT 1 FROM liferevolve_burst_delivery WHERE burst_id=? AND seq=? AND recipient='ai'", (bid, f["seq"])).fetchone() is not None
        status = recognition_status(frame, sent, claimed, _now())
        blocked = [r["body"]["seq"] for r in _ledger(conn, bid) if r["event"] == "PROTECTED_BLOCKED"]
        if blocked and (frame is None or max(blocked) > frame["seq"]):
            status = "PROTECTED_SCREEN"  # the newest tap was a protected screen: nothing current was sent
        _log(conn, bid, "RECOGNITION_CHECK", {"claimed_sha256": claimed, "status": status, "latest_seq": frame["seq"] if frame else None})
    return JSONResponse({"status": status, "latest_seq": frame["seq"] if frame else None}, headers=CACHE_HEADERS)


@router.get("/life/api/burst/{bid}/ledger")
def ledger(bid: str, request: Request):
    """The full record and a fresh recomputation of the chain."""
    account = require_life_user(request)
    with resource_transaction("life") as conn:
        _tables(conn)
        _burst(conn, account, bid)
        recs = _ledger(conn, bid)
    v = verify_chain(recs)
    receipt = {"idx": recs[-1]["idx"], "hash": recs[-1]["hash"]} if recs else None
    return JSONResponse({"records": recs, "verification": v, "receipt": receipt}, headers=CACHE_HEADERS)


@router.post("/life/api/burst/{bid}/ledger/verify")
async def ledger_verify(bid: str, request: Request):
    """Check the chain against receipts the user kept earlier (kept outside this server)."""
    account = require_life_user(request)
    _mutation_allowed(request)
    data = await _json(request)
    receipts = data.get("receipts", [])
    if not isinstance(receipts, list) or not all(isinstance(r, dict) for r in receipts):
        raise HTTPException(status_code=422, detail="burst_invalid_receipts")
    with resource_transaction("life") as conn:
        _tables(conn)
        _burst(conn, account, bid)
        recs = _ledger(conn, bid)
    return JSONResponse({"verification": verify_chain(recs, receipts)}, headers=CACHE_HEADERS)



def _off_limits(conn, bid: str, recipient: str = "ai") -> list[int]:
    """Sequence numbers the recipient must not describe or guess at: capture-protected
    taps and frames that exist but were not released to this recipient."""
    protected = {r["body"]["seq"] for r in _ledger(conn, bid) if r["event"] == "PROTECTED_BLOCKED"}
    stored = {r["seq"] for r in conn.execute("SELECT seq FROM liferevolve_burst_frame WHERE burst_id=?", (bid,))}
    released = {r["seq"] for r in conn.execute("SELECT seq FROM liferevolve_burst_delivery WHERE burst_id=? AND recipient=?", (bid, recipient))}
    return sorted(protected | (stored - released))


def _frames_by_seq(conn, bid: str) -> dict:
    return {r["seq"]: r["sha256"] for r in conn.execute("SELECT seq, sha256 FROM liferevolve_burst_frame WHERE burst_id=?", (bid,))}


@router.post("/life/api/burst/{bid}/training-use")
async def training_use(bid: str, request: Request):
    """Record that a model is about to learn from frames. Refused without a "train" approval."""
    account = require_life_user(request)
    _mutation_allowed(request)
    data = await _json(request)
    seqs, model_id, dataset_id = data.get("seqs"), data.get("model_id"), data.get("dataset_id")
    if not isinstance(seqs, list) or not seqs or not all(isinstance(x, int) for x in seqs) \
            or not isinstance(model_id, str) or not model_id.strip() or not isinstance(dataset_id, str) or not dataset_id.strip():
        raise HTTPException(status_code=422, detail="burst_invalid_training_use")
    with resource_transaction("life") as conn:
        _tables(conn)
        _burst(conn, account, bid)
        g = _active_grant(conn, bid, "train")
        if g is None:
            _log(conn, bid, "TRAINING_REFUSED", {"model_id": model_id[:120], "seqs": seqs, "reason": "NO_TRAIN_APPROVAL"})
    if g is None:  # raised after the transaction so the refusal record is kept
        raise HTTPException(status_code=403, detail="burst_training_not_approved")
    with resource_transaction("life") as conn:
        frames = _frames_by_seq(conn, bid)
        used = [{"seq": x, "sha256": frames[x]} for x in seqs if x in frames]
        skipped = [x for x in seqs if x not in frames]  # missing or capture-protected: never learned
        _log(conn, bid, "TRAINING_USE", {"grant_id": g["grant_id"], "model_id": model_id[:120], "dataset_id": dataset_id[:120], "frames": used, "skipped": skipped})
    return JSONResponse({"used": used, "skipped": skipped, "grant_id": g["grant_id"]}, headers=CACHE_HEADERS)


@router.get("/life/api/burst/{bid}/training-proof")
def training_proof(bid: str, sha256: str, request: Request):
    """For one frame hash: was it learned, by which model, under which approval — or provably not."""
    account = require_life_user(request)
    with resource_transaction("life") as conn:
        _tables(conn)
        _burst(conn, account, bid)
        recs = _ledger(conn, bid)
    chain = verify_chain(recs)
    uses = [{"idx": r["idx"], "at": r["at"], "model_id": r["body"]["model_id"], "dataset_id": r["body"]["dataset_id"], "grant_id": r["body"]["grant_id"]}
            for r in recs if r["event"] == "TRAINING_USE" and any(f["sha256"] == sha256 for f in r["body"]["frames"])]
    known = any(r["event"] == "FRAME" and r["body"]["sha256"] == sha256 for r in recs)
    if not chain["intact"]:
        verdict = "CHAIN_BROKEN"  # nothing can be proven from a broken record
    elif not known:
        verdict = "UNKNOWN_FRAME"
    else:
        verdict = "USED_FOR_TRAINING" if uses else "NOT_USED_FOR_TRAINING"
    return JSONResponse({"sha256": sha256, "verdict": verdict, "uses": uses, "chain": chain}, headers=CACHE_HEADERS)


@router.post("/life/api/burst/{bid}/answers")
async def bind_answer(bid: str, request: Request):
    """Bind an AI answer to the frames it says it is based on. Only the answer's hash is kept."""
    account = require_life_user(request)
    _mutation_allowed(request)
    data = await _json(request)
    text, cited, about = data.get("answer"), data.get("cited_sha256"), data.get("about_seqs", [])
    if not isinstance(text, str) or not text.strip() or not isinstance(cited, list) or not all(isinstance(c, str) for c in cited) \
            or not isinstance(about, list) or not all(isinstance(x, int) for x in about):
        raise HTTPException(status_code=422, detail="burst_invalid_answer")
    answer_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with resource_transaction("life") as conn:
        _tables(conn)
        _burst(conn, account, bid)
        frames = {sha: seq for seq, sha in _frames_by_seq(conn, bid).items()}
        delivered = {r["seq"] for r in conn.execute("SELECT seq FROM liferevolve_burst_delivery WHERE burst_id=? AND recipient='ai'", (bid,))}
        result = []
        for c in cited:
            if c not in frames:
                result.append({"sha256": c, "status": "UNKNOWN_FRAME"})
            elif frames[c] not in delivered:
                result.append({"sha256": c, "seq": frames[c], "status": "NOT_DELIVERED_TO_AI"})
            else:
                result.append({"sha256": c, "seq": frames[c], "status": "GROUNDED"})
        overall = "GROUNDED" if result and all(r["status"] == "GROUNDED" for r in result) else ("NO_EVIDENCE" if not result else "UNGROUNDED")
        protected = {r["body"]["seq"] for r in _ledger(conn, bid) if r["event"] == "PROTECTED_BLOCKED"}
        banned = set(_off_limits(conn, bid, "ai"))
        violations = [{"seq": x, "kind": "PROTECTED_SCREEN" if x in protected else "NOT_CHOSEN_BY_USER"} for x in about if x in banned]
        withheld = bool(violations)
        if withheld:
            # The answer is not shown; the attempt is recorded so it can be shown later.
            _log(conn, bid, "INFERENCE_VIOLATION", {"answer_sha256": answer_sha, "violations": violations})
        h = _log(conn, bid, "ANSWER_BOUND", {"answer_sha256": answer_sha, "citations": result, "overall": overall, "withheld": withheld})
    return JSONResponse({"answer_sha256": answer_sha, "citations": result, "overall": overall,
                         "withheld": withheld, "violations": violations, "record_hash": h}, headers=CACHE_HEADERS)


@router.post("/life/api/burst/{bid}/grants/{gid}/deletion-confirmed")
async def deletion_confirmed(bid: str, gid: str, request: Request):
    """The recipient reports that its copies under a revoked approval were deleted."""
    account = require_life_user(request)
    _mutation_allowed(request)
    with resource_transaction("life") as conn:
        _tables(conn)
        _burst(conn, account, bid)
        g = conn.execute("SELECT recipient, revoked_at FROM liferevolve_burst_grant WHERE grant_id=? AND burst_id=?", (gid, bid)).fetchone()
        if g is None or g["revoked_at"] is None:
            raise HTTPException(status_code=409, detail="grant_not_revoked")
        _log(conn, bid, "DELETION_CONFIRMED", {"grant_id": gid, "recipient": g["recipient"]})
    return JSONResponse({"grant_id": gid, "status": "DELETION_CONFIRMED"}, headers=CACHE_HEADERS)
