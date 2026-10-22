"""LifeRevolve screen library: user-selected images, never implicit AI input."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from PIL import Image, UnidentifiedImageError
from cryptography.exceptions import InvalidTag
from starlette.concurrency import run_in_threadpool

from app.platform_core.life_auth import require_life_user
from app.platform_core.persistent_domain_store import get_resource, put_resource, resource_transaction

router = APIRouter(tags=["liferevolve-screen"])
STATIC_ROOT = Path(__file__).with_name("liferevolve_static")
KIND = "liferevolve_screen_frame_v1"
INFO_KIND = KIND + "_info"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_BODY_BYTES = ((MAX_IMAGE_BYTES + 2) // 3) * 4 + 8192
MAX_PIXELS = 16 * 1024 * 1024
MAX_DIMENSION = 8192
MAX_ACCOUNT_IMAGES = 24
MAX_ACCOUNT_BYTES = 64 * 1024 * 1024
MAX_GLOBAL_STORED_BYTES = 192 * 1024 * 1024
FORMATS = {"PNG": ("image/png", "png"), "JPEG": ("image/jpeg", "jpg"), "WEBP": ("image/webp", "webp")}
PAGE_HEADERS = {
    "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
}
CACHE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}


def non_screen_life_resources(account: str) -> list[dict]:
    """Keep ordinary activity pages from decrypting an entire image library."""
    with resource_transaction("life") as conn:
        ids = conn.execute("SELECT resource_id FROM resource WHERE subject_id=? AND kind NOT LIKE ? ORDER BY updated_at DESC", (account, "liferevolve_screen_%")).fetchall()
        return [item for row in ids if (item := get_resource(domain="life", subject_id=account, resource_id=row["resource_id"])) is not None]


def page_html(name: str) -> str:
    if name not in {"home.html", "screen.html"}:
        raise ValueError("unknown_page")
    return (STATIC_ROOT / name).read_text(encoding="utf-8")


def _table(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS liferevolve_screen (
        resource_id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, name TEXT NOT NULL,
        width INTEGER NOT NULL, height INTEGER NOT NULL, byte_size INTEGER NOT NULL,
        media_type TEXT NOT NULL, extension TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_liferevolve_screen_subject ON liferevolve_screen(subject_id, created_at)")


def _mutation_allowed(request: Request) -> None:
    # A custom header cannot be sent cross-origin without a successful preflight.
    # This router grants no cross-origin permission and also checks Origin.
    if request.headers.get("x-liferevolve-action") != "screen-import":
        raise HTTPException(status_code=403, detail="screen_action_header_required")
    def origin_pair(value: str):
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                return None
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            return parsed.scheme, parsed.hostname.lower(), port
        except ValueError:
            return None

    # An explicit public HTTPS origin avoids trusting forwarded headers when a
    # reverse proxy terminates TLS before the application process.
    configured = os.getenv("LIFEREVOLVE_PUBLIC_ORIGIN", "").strip()
    expected = origin_pair(configured or str(request.base_url))
    if expected is None or (configured and expected[0] != "https"):
        raise HTTPException(status_code=503, detail="screen_public_origin_invalid")
    origin = request.headers.get("origin")
    if origin is not None and origin_pair(origin) != expected:
        raise HTTPException(status_code=403, detail="screen_origin_mismatch")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="screen_cross_site_blocked")


def _metadata(row, name: str = "화면 자료") -> dict:
    rid = row["resource_id"]
    return {
        "id": rid, "name": name, "width": row["width"], "height": row["height"],
        "byte_size": row["byte_size"], "created_at": row["created_at"], "status": "STORED",
        "image_url": f"/life/api/screens/{rid}/image", "download_url": f"/life/api/screens/{rid}/download",
        "analysis_status": "NOT_RUN", "current_screen": False, "coverage_percent": None,
    }


def _validated_image(encoded: str) -> tuple[bytes, int, int, str, str]:
    if not isinstance(encoded, str) or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise HTTPException(status_code=413, detail="screen_image_too_large")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(status_code=422, detail="screen_invalid_base64") from None
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="screen_image_too_large")
    # Content signatures, not the submitted filename or MIME header, choose type.
    signature_ok = raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8\xff") or (raw[:4] == b"RIFF" and raw[8:12] == b"WEBP")
    if not signature_ok:
        raise HTTPException(status_code=422, detail="screen_unsupported_image")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            fmt = image.format
            width, height = image.size
            if fmt not in FORMATS or getattr(image, "is_animated", False):
                raise HTTPException(status_code=422, detail="screen_unsupported_image")
            if width < 1 or height < 1 or width > MAX_DIMENSION or height > MAX_DIMENSION or width * height > MAX_PIXELS:
                raise HTTPException(status_code=413, detail="screen_dimensions_too_large")
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
    except HTTPException:
        raise
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise HTTPException(status_code=422, detail="screen_invalid_image") from None
    media_type, extension = FORMATS[fmt]
    return raw, width, height, media_type, extension


def _store(account: str, fields: dict) -> dict:
    if not isinstance(fields, dict) or set(fields) != {"name", "base64"} or not isinstance(fields["name"], str):
        raise HTTPException(status_code=422, detail="screen_invalid_request")
    name = fields["name"].replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isprintable()).strip()[:160] or "화면 자료"
    raw, width, height, media_type, extension = _validated_image(fields["base64"])
    rid = "scr_" + uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    payload = {"base64": base64.b64encode(raw).decode("ascii"), "sha256": hashlib.sha256(raw).hexdigest()}
    # Quota checks and both encrypted bytes and index commit as one transaction.
    with resource_transaction("life") as conn:
        _table(conn)
        count, total = conn.execute("SELECT COUNT(*), COALESCE(SUM(byte_size),0) FROM liferevolve_screen WHERE subject_id=?", (account,)).fetchone()
        if count >= MAX_ACCOUNT_IMAGES or total + len(raw) > MAX_ACCOUNT_BYTES:
            raise HTTPException(status_code=429, detail="screen_account_quota_reached")
        stored = conn.execute("SELECT COALESCE(SUM(length(ciphertext)),0) FROM resource WHERE kind=?", (KIND,)).fetchone()[0]
        if stored + len(payload["base64"]) + 1024 > MAX_GLOBAL_STORED_BYTES:
            raise HTTPException(status_code=507, detail="screen_storage_capacity_reached")
        put_resource(domain="life", subject_id=account, kind=KIND, payload=payload, resource_id=rid)
        # Filenames may themselves contain private information. Keep the title in
        # a small encrypted resource so listing never decrypts image-sized data.
        put_resource(domain="life", subject_id=account, kind=INFO_KIND, payload={"name": name}, resource_id=rid + "_info")
        conn.execute("INSERT INTO liferevolve_screen VALUES(?,?,?,?,?,?,?,?,?)", (rid, account, "", width, height, len(raw), media_type, extension, now))
        row = conn.execute("SELECT * FROM liferevolve_screen WHERE resource_id=?", (rid,)).fetchone()
        result = _metadata(row, name)
    return result


@router.get("/life/screen", response_class=HTMLResponse)
def screen_page(request: Request):
    try:
        require_life_user(request)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        return RedirectResponse("/life/auth/login", status_code=303)
    return HTMLResponse(page_html("screen.html"), headers=PAGE_HEADERS)


@router.get("/life/assets/{asset}")
def screen_asset(asset: str):
    entries = {"life.css": "text/css", "screen.js": "text/javascript"}
    if asset == "companion.png":
        return Response(_companion_png(), media_type="image/png", headers={"Cache-Control": "public,max-age=3600", "X-Content-Type-Options": "nosniff"})
    if asset not in entries:
        raise HTTPException(status_code=404, detail="asset_not_found")
    return Response((STATIC_ROOT / asset).read_bytes(), media_type=entries[asset], headers={"Cache-Control": "public,max-age=300", "X-Content-Type-Options": "nosniff"})


@lru_cache(maxsize=1)
def _companion_png() -> bytes:
    return base64.b64decode((STATIC_ROOT / "companion.png.b64").read_bytes())


@router.post("/life/api/screens", status_code=201)
async def import_screen(request: Request):
    account = require_life_user(request)
    _mutation_allowed(request)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="screen_json_required")
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise HTTPException(status_code=400, detail="screen_invalid_content_length") from None
    if length < 0 or length > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="screen_request_too_large")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="screen_request_too_large")
        body.extend(chunk)
    try:
        fields = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(status_code=422, detail="screen_invalid_json") from None
    item = await run_in_threadpool(_store, account, fields)
    return JSONResponse({"screen": item}, status_code=201, headers=CACHE_HEADERS)


@router.get("/life/api/screens")
def list_screens(request: Request):
    account = require_life_user(request)
    with resource_transaction("life") as conn:
        _table(conn)
        rows = conn.execute("SELECT * FROM liferevolve_screen WHERE subject_id=? ORDER BY created_at DESC", (account,)).fetchall()
        items = []
        for row in rows:
            try:
                info = get_resource(domain="life", subject_id=account, resource_id=row["resource_id"] + "_info")
            except InvalidTag:
                raise HTTPException(status_code=500, detail="screen_integrity_failure") from None
            if not info or info["kind"] != INFO_KIND:
                raise HTTPException(status_code=500, detail="screen_storage_incomplete")
            items.append(_metadata(row, info["payload"]["name"]))
    return JSONResponse({"screens": items, "limits": {"max_image_bytes": MAX_IMAGE_BYTES, "max_account_images": MAX_ACCOUNT_IMAGES, "max_account_bytes": MAX_ACCOUNT_BYTES}}, headers=CACHE_HEADERS)


def _read_image(account: str, rid: str) -> tuple[bytes, str, str]:
    with resource_transaction("life") as conn:
        _table(conn)
        row = conn.execute("SELECT * FROM liferevolve_screen WHERE resource_id=? AND subject_id=?", (rid, account)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="screen_not_found")
        try:
            stored = get_resource(domain="life", subject_id=account, resource_id=rid)
        except InvalidTag:
            raise HTTPException(status_code=500, detail="screen_integrity_failure") from None
    if stored is None or stored["kind"] != KIND:
        raise HTTPException(status_code=500, detail="screen_storage_incomplete")
    raw = base64.b64decode(stored["payload"]["base64"], validate=True)
    if len(raw) != row["byte_size"] or hashlib.sha256(raw).hexdigest() != stored["payload"]["sha256"]:
        raise HTTPException(status_code=500, detail="screen_integrity_failure")
    return raw, row["media_type"], row["extension"]


@router.get("/life/api/screens/{rid}/image")
def screen_image(rid: str, request: Request):
    raw, media_type, _ = _read_image(require_life_user(request), rid)
    return Response(raw, media_type=media_type, headers=CACHE_HEADERS)


@router.get("/life/api/screens/{rid}/download")
def screen_download(rid: str, request: Request):
    raw, media_type, extension = _read_image(require_life_user(request), rid)
    return Response(raw, media_type=media_type, headers={**CACHE_HEADERS, "Content-Disposition": f'attachment; filename="screen-{rid}.{extension}"'})


@router.delete("/life/api/screens/{rid}", status_code=204)
def delete_screen(rid: str, request: Request):
    account = require_life_user(request)
    _mutation_allowed(request)
    with resource_transaction("life") as conn:
        _table(conn)
        row = conn.execute("SELECT resource_id FROM liferevolve_screen WHERE resource_id=? AND subject_id=?", (rid, account)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="screen_not_found")
        conn.execute("DELETE FROM resource WHERE subject_id=? AND ((resource_id=? AND kind=?) OR (resource_id=? AND kind=?))", (account, rid, KIND, rid + "_info", INFO_KIND))
        conn.execute("DELETE FROM liferevolve_screen WHERE resource_id=? AND subject_id=?", (rid, account))
    return Response(status_code=204, headers=CACHE_HEADERS)
