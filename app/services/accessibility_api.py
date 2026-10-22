"""Owner accessibility tools: calibrated Braille, limited sign inputs, sign playback."""
from __future__ import annotations

import asyncio
from dataclasses import fields
from functools import lru_cache
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.services.owner_basic_auth import require_owner
from app.services.braille_core import BrailleError, BrailleGrid, decode_braille, translate_braille_unicode
from app.services.media_core import MediaConfig, MediaError, probe_media
from app.services.accessibility_store import AccessibilityError, AccessibilityStore, accessibility_root

router = APIRouter(prefix="/owner/accessibility", dependencies=[Depends(require_owner)], include_in_schema=False)
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin"}
_inference_lock = asyncio.Semaphore(1)
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv"}


@lru_cache(maxsize=1)
def store():
    return AccessibilityStore(accessibility_root())


def start_accessibility_cleanup():
    store().start()


def stop_accessibility_cleanup():
    store().stop()


def error_response(exc):
    return JSONResponse({"error_code": getattr(exc, "code", "ACCESSIBILITY_FAILED"), "detail": str(exc)},
                        status_code=getattr(exc, "status", 400), headers=HEADERS)


def _validate_filename(filename):
    if not 1 <= len(filename) <= 200 or any(c in filename for c in ("/", "\\", "\x00")):
        raise AccessibilityError("INVALID_FILENAME", "파일 이름이 올바르지 않습니다.")
    suffix = Path(filename).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        raise AccessibilityError("INVALID_VIDEO_TYPE", "MP4·WebM·MOV·MKV 영상 파일을 선택하십시오.", 415)
    return suffix


async def _receive(request, target, maximum, explicit_authorization):
    if not explicit_authorization or request.headers.get("X-Accessibility-Upload") != "1":
        raise AccessibilityError("AUTHORIZATION_REQUIRED", "파일 처리 동의를 확인하십시오.")
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/octet-stream":
        raise AccessibilityError("INVALID_CONTENT_TYPE", "파일 전송 형식이 올바르지 않습니다.", 415)
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > maximum):
        raise AccessibilityError("UPLOAD_TOO_LARGE", "파일 크기가 허용 범위를 넘었습니다.", 413)
    store().check_space(maximum)
    if shutil.disk_usage(store().root).free < maximum + 16 * 1024 * 1024:
        raise AccessibilityError("STORAGE_FULL", "사용 가능한 저장 공간이 부족합니다.", 507)

    async def receive():
        total = 0
        with target.open("xb") as output:
            async for chunk in request.stream():
                total += len(chunk)
                if total > maximum:
                    raise AccessibilityError("UPLOAD_TOO_LARGE", "파일 크기가 허용 범위를 넘었습니다.", 413)
                output.write(chunk)
        if total == 0:
            raise AccessibilityError("UPLOAD_EMPTY", "빈 파일은 처리할 수 없습니다.")
    try:
        await asyncio.wait_for(receive(), 180)
    except asyncio.TimeoutError:
        raise AccessibilityError("UPLOAD_TIMEOUT", "파일 전송 시간이 초과됐습니다.", 408) from None


def _video_info(path):
    info = probe_media(path, MediaConfig(max_upload_bytes=32*1024*1024, max_duration_seconds=60))
    if not info["has_video"]:
        raise AccessibilityError("VIDEO_REQUIRED", "수어 동작이 보이는 영상 파일이 필요합니다.")
    return info


@router.get("", response_class=HTMLResponse)
async def workspace():
    html = Path(__file__).with_name("accessibility_workspace.html").read_text(encoding="utf-8")
    return HTMLResponse(html, headers={**HEADERS, "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})


@router.get("/status")
async def readiness():
    configured = all(os.getenv(k) for k in ("SIGN_RECOGNITION_ENDPOINT", "SIGN_RECOGNITION_MODEL", "SIGN_RECOGNITION_API_KEY"))
    model = os.getenv("MEDIA_SIGN_LOCAL_MODEL_PATH", "")
    return JSONResponse({"sign_configured": configured,
                         "sign_transfer_enabled": os.getenv("SIGN_DATA_POLICY_ACK", "").lower() == "true",
                         "local_landmark_model_configured": bool(model and Path(model).is_file() and importlib.util.find_spec("mediapipe")),
                         "max_image_mb": 10, "max_sign_video_mb": 32, "max_sign_video_seconds": 60,
                         "braille_scope": "정렬한 6점·8점의 점 배열을 읽습니다. 자동 결과는 미확정입니다.",
                         "sign_scope": "등록 동작 비교는 한국수어 전체 문장 번역이 아닙니다. 실제 제공자 응답과 사람 영상의 정확도는 별도 확인이 필요합니다."}, headers=HEADERS)


@router.post("/braille")
async def braille(request: Request, grid: str = Query(max_length=2000), explicit_authorization: bool = False):
    async with _inference_lock:
        try:
            values = json.loads(grid)
            if not isinstance(values, dict) or set(values) - {f.name for f in fields(BrailleGrid)}:
                raise ValueError("invalid grid")
            settings = BrailleGrid(**values)
            with tempfile.TemporaryDirectory(prefix="braille-", dir=store().root) as tmp:
                path = Path(tmp) / "source.image"
                await _receive(request, path, 10*1024*1024, explicit_authorization)
                result = await run_in_threadpool(decode_braille, path, settings)
                if os.getenv("BRAILLE_LIBLOUIS_TABLE"):
                    try:
                        result["translation"] = await run_in_threadpool(translate_braille_unicode, result["unicode_braille"])
                    except BrailleError as exc:
                        result["translation"] = {"state": "UNCONFIRMED", "text": None, "error_code": exc.code, "message": str(exc)}
            return JSONResponse(result, headers=HEADERS)
        except (BrailleError, ValueError, TypeError, MediaError) as exc:
            return error_response(exc)


@router.post("/sign/recognize")
async def recognize(request: Request, filename: str = Query(max_length=200),
                    mode: Literal["provider", "templates"] = "provider", explicit_authorization: bool = False):
    async with _inference_lock:
        try:
            from app.services.sign_core import recognize_sign_video, extract_hand_landmarks, classify_landmarks
            suffix = _validate_filename(filename)
            with tempfile.TemporaryDirectory(prefix="sign-", dir=store().root) as tmp:
                path = Path(tmp) / ("source"+suffix)
                await _receive(request, path, 32*1024*1024, explicit_authorization)
                await run_in_threadpool(_video_info, path)
                if mode == "provider":
                    result = await run_in_threadpool(recognize_sign_video, path)
                else:
                    model = os.getenv("MEDIA_SIGN_LOCAL_MODEL_PATH", "")
                    if not model:
                        raise AccessibilityError("LOCAL_SIGN_MODEL_MISSING", "손 좌표를 읽는 모델 파일을 먼저 지정해야 합니다.", 503)
                    extracted = await run_in_threadpool(extract_hand_landmarks, path, Path(model))
                    result = await run_in_threadpool(classify_landmarks, extracted["sequence"], store().list_templates(include_sequence=True))
                    result["source_video_sha256"] = extracted["source_video_sha256"]
                    result["warnings"] = result.get("warnings", []) + extracted.get("warnings", [])
            return JSONResponse(result, headers=HEADERS)
        except Exception as exc:
            if hasattr(exc, "code"):
                return error_response(exc)
            raise


class LandmarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sequence: list[list[float]] = Field(min_length=2, max_length=120)
    label: str = Field(default="", max_length=200)


async def _landmark_request(request):
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise AccessibilityError("INVALID_CONTENT_TYPE", "손 좌표 자료는 JSON 형식으로 보내야 합니다.", 415)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 2*1024*1024:
            raise AccessibilityError("PAYLOAD_TOO_LARGE", "손 좌표 자료가 너무 큽니다.", 413)
    try:
        return LandmarkRequest.model_validate_json(body)
    except ValueError:
        raise AccessibilityError("INVALID_LANDMARKS", "손 좌표 자료 형식이 올바르지 않습니다.") from None


@router.get("/sign/templates")
async def templates():
    return JSONResponse({"templates": store().list_templates()}, headers=HEADERS)


@router.post("/sign/templates")
async def add_template(request: Request):
    async with _inference_lock:
        try:
            payload = await _landmark_request(request)
            return JSONResponse(store().add_template(payload.label, payload.sequence), headers=HEADERS)
        except Exception as exc:
            if hasattr(exc, "code"):
                return error_response(exc)
            raise


@router.post("/sign/classify")
async def classify(request: Request):
    try:
        from app.services.sign_core import classify_landmarks
        payload = await _landmark_request(request)
        result = await run_in_threadpool(classify_landmarks, payload.sequence, store().list_templates(include_sequence=True))
        return JSONResponse(result, headers=HEADERS)
    except Exception as exc:
        if hasattr(exc, "code"):
            return error_response(exc)
        raise


@router.post("/sign/templates/from-video")
async def register_video_template(request: Request, filename: str = Query(max_length=200), label: str = Query(min_length=1, max_length=200), explicit_authorization: bool = False):
    async with _inference_lock:
        try:
            from app.services.sign_core import extract_hand_landmarks
            model = os.getenv("MEDIA_SIGN_LOCAL_MODEL_PATH", "")
            if not model:
                raise AccessibilityError("LOCAL_SIGN_MODEL_MISSING", "손 좌표를 읽는 모델 파일을 먼저 지정해야 합니다.", 503)
            suffix = _validate_filename(filename)
            with tempfile.TemporaryDirectory(prefix="template-", dir=store().root) as tmp:
                path = Path(tmp) / ("source"+suffix)
                await _receive(request, path, 32*1024*1024, explicit_authorization)
                await run_in_threadpool(_video_info, path)
                extracted = await run_in_threadpool(extract_hand_landmarks, path, Path(model))
                result = store().add_template(label, extracted["sequence"], extracted["source_video_sha256"])
            return JSONResponse(result, headers=HEADERS)
        except Exception as exc:
            if hasattr(exc, "code"):
                return error_response(exc)
            raise


@router.delete("/sign/templates/{template_id}")
async def delete_template(template_id: str):
    try:
        store().delete_template(template_id)
        return JSONResponse({"deleted": True}, headers=HEADERS)
    except AccessibilityError as exc:
        return error_response(exc)


@router.get("/sign/videos")
async def list_videos():
    return JSONResponse({"videos": store().list_videos()}, headers=HEADERS)


@router.post("/sign/videos")
async def upload_video(request: Request, filename: str = Query(max_length=200), phrase: str = Query(min_length=1, max_length=200), title: str = Query(default="", max_length=200), explicit_authorization: bool = False):
    async with _inference_lock:
        try:
            suffix = _validate_filename(filename)
            if suffix not in {".mp4", ".webm"}:
                raise AccessibilityError("PLAYER_VIDEO_TYPE", "재생용 수어 영상은 MP4 또는 WebM 형식이 필요합니다.", 415)
            with tempfile.TemporaryDirectory(prefix="gallery-", dir=store().root) as tmp:
                path = Path(tmp) / ("source"+suffix)
                await _receive(request, path, 32*1024*1024, explicit_authorization)
                info = await run_in_threadpool(_video_info, path)
                formats = info["format"].split(",")
                if (suffix == ".mp4" and "mov" not in formats) or (suffix == ".webm" and "webm" not in formats):
                    raise AccessibilityError("PLAYER_CONTAINER_MISMATCH", "영상 파일의 실제 형식과 확장자가 다릅니다. MP4 또는 WebM으로 저장한 파일을 제출하십시오.", 415)
                result = store().add_video(path, phrase.strip(), title.strip(), info["duration_seconds"])
            return JSONResponse(result, status_code=201, headers=HEADERS)
        except (AccessibilityError, MediaError) as exc:
            return error_response(exc)


@router.get("/sign/videos/{video_id}/content")
async def video_content(video_id: str):
    try:
        path = store().video_path(video_id)
        return FileResponse(path, media_type="video/webm" if path.suffix == ".webm" else "video/mp4", headers=HEADERS)
    except AccessibilityError as exc:
        return error_response(exc)


@router.delete("/sign/videos/{video_id}")
async def delete_video(video_id: str):
    try:
        store().delete_video(video_id)
        return JSONResponse({"deleted": True}, headers=HEADERS)
    except AccessibilityError as exc:
        return error_response(exc)


class ExposureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    session_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    event: Literal["loadedmetadata", "play", "playing", "pause", "ended", "error"]
    current_time: float = Field(ge=0, le=62)
    visible: bool = False


@router.post("/sign/exposure")
async def record_exposure(payload: ExposureRequest):
    try:
        if not math.isfinite(payload.current_time):
            raise AccessibilityError("INVALID_PLAYBACK_TIME", "영상 재생시간이 올바르지 않습니다.")
        result = store().record_exposure(**payload.model_dump())
        return JSONResponse(result, headers=HEADERS)
    except AccessibilityError as exc:
        return error_response(exc)


@router.get("/sign/exposure")
async def read_exposure(session_id: str = Query(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")):
    return JSONResponse({"events": store().exposures(session_id), "understanding_verified": False}, headers=HEADERS)
