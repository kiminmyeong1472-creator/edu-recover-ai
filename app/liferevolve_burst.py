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
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.platform_core.life_auth import require_life_user
from app.platform_core.persistent_domain_store import get_resource, resource_transaction
from app.liferevolve_screen import CACHE_HEADERS, MAX_BODY_BYTES, _mutation_allowed, _store

router = APIRouter(tags=["liferevolve-burst"])
DOUBLE_TAP_MS = 400
DOUBLE_TAP_PX = 40
DEDUPE_MS = 120
IDLE_CLOSE_MS = 5000
CURRENT_MS = 3000
MAX_BURST_FRAMES = 24
RECIPIENTS = {"ai", "room", "chat"}


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


def _burst(conn, account: str, bid: str):
    row = conn.execute("SELECT * FROM liferevolve_burst WHERE burst_id=? AND subject_id=?", (bid, account)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="burst_not_found")
    if row["status"] == "OPEN" and (_now() - datetime.fromisoformat(row["last_tap_at"])).total_seconds() * 1000 > IDLE_CLOSE_MS:
        conn.execute("UPDATE liferevolve_burst SET status='CLOSED', closed_at=?, close_reason='IDLE' WHERE burst_id=?", (_now().isoformat(), bid))
        row = conn.execute("SELECT * FROM liferevolve_burst WHERE burst_id=?", (bid,)).fetchone()
    return row


def _summary(conn, row) -> dict:
    frames = conn.execute("SELECT seq, resource_id, tapped_at, sha256 FROM liferevolve_burst_frame WHERE burst_id=? ORDER BY seq", (row["burst_id"],)).fetchall()
    seqs = [f["seq"] for f in frames]
    return {
        "burst_id": row["burst_id"], "signal_id": row["signal_id"], "status": row["status"],
        "opened_at": row["opened_at"], "closed_at": row["closed_at"], "close_reason": row["close_reason"],
        "frames": [{"seq": f["seq"], "screen_id": f["resource_id"], "tapped_at": f["tapped_at"], "sha256": f["sha256"]} for f in frames],
        "missing_sequences": missing_sequences(seqs),
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
        conn.execute("INSERT INTO liferevolve_burst VALUES(?,?,?,?,?,?,?,NULL,NULL)", (bid, account, "sig_" + uuid.uuid4().hex, "OPEN", now, now, tap_ms))
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
            return JSONResponse({"accepted": False, "reason": "DUPLICATE_TAP"}, status_code=200, headers=CACHE_HEADERS)
        if conn.execute("SELECT COUNT(*) FROM liferevolve_burst_frame WHERE burst_id=?", (bid,)).fetchone()[0] >= MAX_BURST_FRAMES:
            raise HTTPException(status_code=429, detail="burst_frame_limit")
    item = await run_in_threadpool(_store, account, {"name": data.get("name") or f"연속화면 {seq}", "base64": data.get("base64")})
    sha = get_resource(domain="life", subject_id=account, resource_id=item["id"])["payload"]["sha256"]
    now = _now().isoformat()
    with resource_transaction("life") as conn:
        conn.execute("INSERT INTO liferevolve_burst_frame VALUES(?,?,?,?,?)", (bid, seq, item["id"], now, sha))
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
    if recipient not in RECIPIENTS or not isinstance(purpose, str) or not purpose.strip() or not isinstance(minutes, int) or not 1 <= minutes <= 120:
        raise HTTPException(status_code=422, detail="burst_invalid_grant")
    gid = "grt_" + uuid.uuid4().hex
    expires = (_now() + timedelta(minutes=minutes)).isoformat()
    with resource_transaction("life") as conn:
        _tables(conn)
        _burst(conn, account, bid)
        conn.execute("INSERT INTO liferevolve_burst_grant VALUES(?,?,?,?,?,NULL)", (gid, bid, recipient, purpose.strip()[:200], expires))
    return JSONResponse({"grant_id": gid, "recipient": recipient, "expires_at": expires}, status_code=201, headers=CACHE_HEADERS)


@router.delete("/life/api/burst/{bid}/grants/{gid}", status_code=204)
def revoke(bid: str, gid: str, request: Request):
    account = require_life_user(request)
    _mutation_allowed(request)
    with resource_transaction("life") as conn:
        _tables(conn)
        _burst(conn, account, bid)
        cur = conn.execute("UPDATE liferevolve_burst_grant SET revoked_at=? WHERE grant_id=? AND burst_id=? AND revoked_at IS NULL", (_now().isoformat(), gid, bid))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="grant_not_found")
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
            raise HTTPException(status_code=403, detail="burst_recipient_not_approved")
        have = {r["seq"] for r in conn.execute("SELECT seq FROM liferevolve_burst_frame WHERE burst_id=?", (bid,))}
        sent, missing = [], []
        for s in seqs:
            if s not in have:
                missing.append(s)
                continue
            conn.execute("INSERT OR IGNORE INTO liferevolve_burst_delivery VALUES(?,?,?,?,?)", (bid, s, recipient, g["grant_id"], _now().isoformat()))
            sent.append(s)
    return JSONResponse({"recipient": recipient, "delivered": sent, "missing": missing}, headers=CACHE_HEADERS)


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
    return JSONResponse({"status": recognition_status(frame, sent, claimed, _now()), "latest_seq": frame["seq"] if frame else None}, headers=CACHE_HEADERS)
