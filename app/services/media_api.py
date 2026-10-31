"""Owner-only news/lecture audio and video transcription routes."""
from __future__ import annotations

import asyncio
from functools import lru_cache
import os
from pathlib import Path
import shutil
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.services.owner_basic_auth import require_owner
from app.services.media_core import MediaConfig, MediaError
from app.services.media_jobs import ARTIFACTS, JobError, MediaJobs, default_root

router = APIRouter(prefix="/owner/media", dependencies=[Depends(require_owner)], include_in_schema=False)
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin"}
EXTENSIONS = {".wav", ".mp3", ".mp4", ".m4a", ".webm", ".ogg", ".flac", ".mov", ".mkv"}


@lru_cache(maxsize=1)
def store():
    return MediaJobs(default_root())


def start_media_worker():
    store().start()


def stop_media_worker():
    store().stop()


def fail(exc):
    return JSONResponse({"error_code": exc.code, "detail": str(exc)}, status_code=getattr(exc, "status", 400), headers=HEADERS)


@router.get("", response_class=HTMLResponse)
async def workspace():
    html = Path(__file__).with_name("media_workspace.html").read_text(encoding="utf-8")
    return HTMLResponse(html, headers={**HEADERS, "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; media-src 'self' blob:; img-src 'self' blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})


@router.get("/status")
async def readiness():
    try:
        config = MediaConfig.from_env()
        status = {"asr_configured": bool(config.asr_endpoint and config.asr_model and config.asr_api_key),
                  "asr_transfer_enabled": os.getenv("MEDIA_ASR_DATA_POLICY_ACK", "").lower() == "true",
                  "ffmpeg_available": bool(shutil.which("ffmpeg")), "ffprobe_available": bool(shutil.which("ffprobe")),
                  "ocr_available": bool(shutil.which("tesseract")),
                  "max_upload_mb": config.max_upload_bytes // (1024 * 1024), "max_duration_seconds": config.max_duration_seconds,
                  "scope": "음성 전사와 일정 간격 영상의 자막·판서·슬라이드 글자 인식. 장면·사물·행동의 의미 분석은 포함하지 않습니다.",
                  "retention_hours": 24, "actual_asr_verified": False}
        return JSONResponse(status, headers=HEADERS)
    except (MediaError, ValueError) as exc:
        return JSONResponse({"error_code": "MEDIA_CONFIG_INVALID", "detail": "미디어 설정을 확인하십시오."}, status_code=503, headers=HEADERS)


@router.get("/jobs")
async def list_jobs():
    return JSONResponse({"jobs": store().list()}, headers=HEADERS)


@router.post("/jobs", status_code=202)
async def submit(request: Request, filename: str = Query(min_length=1, max_length=200),
                 purpose: Literal["news", "lecture"] = "lecture", language: Literal["ko", "en", "auto"] = "ko",
                 include_ocr: bool = True, explicit_authorization: bool = False):
    if not explicit_authorization or request.headers.get("X-Media-Upload") != "1":
        raise HTTPException(400, "파일 처리와 외부 음성 전송 동의를 확인하십시오.")
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/octet-stream":
        raise HTTPException(415, "음성 또는 동영상 파일을 선택하십시오.")
    extension = Path(filename).suffix.lower()
    if extension not in EXTENSIONS or any(c in filename for c in ("/", "\\", "\x00")):
        raise HTTPException(415, "지원하지 않는 파일 형식입니다.")
    job = None
    try:
        maximum = MediaConfig.from_env().max_upload_bytes
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > maximum):
            raise JobError("UPLOAD_TOO_LARGE", "파일 크기가 허용 범위를 넘었습니다.", 413)
        job = store().create(filename, purpose, language, include_ocr)
        target = store().path(job["id"]) / ("source" + extension)

        async def receive():
            received = 0
            with target.open("xb") as dest:
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > maximum:
                        raise JobError("UPLOAD_TOO_LARGE", "파일 크기가 허용 범위를 넘었습니다.", 413)
                    dest.write(chunk)
            if not received:
                raise JobError("UPLOAD_EMPTY", "빈 파일은 처리할 수 없습니다.")

        await asyncio.wait_for(receive(), timeout=180)
        store().update(job["id"], "queued", "처리 대기 중")
        return JSONResponse(store().get(job["id"]), status_code=202, headers=HEADERS)
    except BaseException as exc:
        if job:
            store().discard(job["id"], force=True)
        if isinstance(exc, (JobError, MediaError)):
            return fail(exc)
        if isinstance(exc, asyncio.TimeoutError):
            return fail(JobError("UPLOAD_TIMEOUT", "파일 전송 시간이 초과됐습니다.", 408))
        raise


@router.get("/jobs/{job_id}")
async def job_status(job_id: str):
    try:
        return JSONResponse(store().get(job_id, include_result=True), headers=HEADERS)
    except JobError as exc:
        return fail(exc)


@router.get("/jobs/{job_id}/files/{name}")
async def download(job_id: str, name: str):
    try:
        job = store().get(job_id)
        if job["status"] != "completed" or name not in ARTIFACTS:
            raise JobError("ARTIFACT_NOT_FOUND", "결과 파일을 찾지 못했습니다.", 404)
        path = store().path(job_id) / name
        if not path.is_file():
            raise JobError("ARTIFACT_NOT_FOUND", "결과 파일을 찾지 못했습니다.", 404)
        mime = "application/json" if name.endswith(".json") else "text/vtt" if name.endswith(".vtt") else "text/plain"
        return FileResponse(path, media_type=mime, filename=name, headers=HEADERS)
    except JobError as exc:
        return fail(exc)


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    try:
        store().discard(job_id)
        return JSONResponse({"deleted": True}, headers=HEADERS)
    except JobError as exc:
        return fail(exc)
