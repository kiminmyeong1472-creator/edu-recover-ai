"""Serve the exact verified Android build; never substitute a placeholder APK."""
from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter()
RELEASE_DIR = Path(__file__).with_name("liferevolve_release")


@lru_cache(maxsize=1)
def _verified_release() -> tuple[dict, bytes]:
    try:
        manifest = json.loads((RELEASE_DIR / "manifest.json").read_text("utf-8"))
        encoded = (RELEASE_DIR / "LifeRevolve.apk.b64").read_bytes()
        data = base64.b64decode(encoded, validate=True)
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(503, "android_release_not_ready") from exc
    if not data.startswith(b"PK") or not data or len(data) != manifest.get("size_bytes"):
        raise HTTPException(503, "android_release_invalid")
    if hashlib.sha256(data).hexdigest() != manifest.get("sha256"):
        raise HTTPException(503, "android_release_integrity_failure")
    return manifest, data


@router.get("/life/android.apk")
def android_apk() -> Response:
    manifest, data = _verified_release()
    return Response(
        data,
        media_type="application/vnd.android.package-archive",
        headers={
            "Content-Disposition": 'attachment; filename="LifeRevolve_0_3_0_20261022.apk"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=300",
            "ETag": '"' + manifest["sha256"] + '"',
        },
    )


@router.get("/life/app-version")
def app_version() -> dict:
    manifest, _ = _verified_release()
    return {**manifest, "download_url": "/life/android.apk"}
