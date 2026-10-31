"""Bounded, durable media jobs. Only the authenticated owner uses this store."""
from __future__ import annotations

import fcntl
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
import uuid

from app.services.media_core import MediaConfig, MediaError, process_media

logger = logging.getLogger(__name__)
ARTIFACTS = {"transcript.json", "transcript.txt", "transcript.srt", "transcript.vtt", "analysis.json"}
ACTIVE = ("uploading", "queued", "running")


class JobError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class MediaJobs:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = self.root / "jobs.sqlite3"
        self.stop_event = threading.Event()
        self.thread = None
        with self.connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, filename TEXT NOT NULL,
                purpose TEXT NOT NULL, language TEXT NOT NULL, include_ocr INTEGER NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, stage TEXT NOT NULL,
                error_code TEXT, error_message TEXT)""")

    def connect(self):
        con = sqlite3.connect(self.db, timeout=15)
        con.row_factory = sqlite3.Row
        return con

    def path(self, job_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise JobError("JOB_NOT_FOUND", "작업을 찾지 못했습니다.", 404)
        return self.root / job_id

    def create(self, filename, purpose, language, include_ocr):
        self.cleanup()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('uploading','queued','running')").fetchone()[0] >= 2:
                raise JobError("QUEUE_FULL", "진행 중인 작업이 끝난 후 다시 시도하십시오.", 429)
            # Reserve enough disk for both uploads, extracted audio and OCR images.
            used = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
            upload_bytes = MediaConfig.from_env().max_upload_bytes
            outstanding = 0
            for row in con.execute("SELECT id FROM jobs WHERE status='uploading'"):
                present = sum(p.stat().st_size for p in self.path(row[0]).glob("source.*") if p.is_file())
                outstanding += max(0, upload_bytes - present)
            required = outstanding + upload_bytes + 64 * 1024 * 1024
            if used + required > 350 * 1024 * 1024 or shutil.disk_usage(self.root).free < required:
                raise JobError("STORAGE_FULL", "저장 공간이 부족합니다. 완료한 작업을 삭제하십시오.", 507)
            job_id, now = uuid.uuid4().hex, time.time()
            self.path(job_id).mkdir(mode=0o700)
            con.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)", (job_id, "uploading", filename, purpose, language, int(include_ocr), now, now, "파일 수신 중", None, None))
        return self.get(job_id)

    def update(self, job_id, status, stage, code=None, message=None):
        with self.connect() as con:
            con.execute("UPDATE jobs SET status=?,stage=?,updated_at=?,error_code=?,error_message=? WHERE id=?", (status, stage, time.time(), code, message, job_id))

    def get(self, job_id, include_result=False):
        self.path(job_id)
        with self.connect() as con:
            row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise JobError("JOB_NOT_FOUND", "작업을 찾지 못했습니다.", 404)
        job = dict(row)
        if include_result and job["status"] == "completed":
            result_path = self.path(job_id) / "transcript.json"
            if result_path.is_file():
                job["result"] = json.loads(result_path.read_text(encoding="utf-8"))
        return job

    def list(self):
        self.cleanup()
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 50")]

    def discard(self, job_id, force=False):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise JobError("JOB_NOT_FOUND", "작업을 찾지 못했습니다.", 404)
            if row[0] in ACTIVE and not force:
                raise JobError("JOB_ACTIVE", "진행 중인 작업은 삭제할 수 없습니다.", 409)
            shutil.rmtree(self.path(job_id), ignore_errors=True)
            con.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    def cleanup(self):
        # Keep at most 24 hours; results are owner-only and raw uploads are removed immediately.
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            abandoned = con.execute("SELECT id FROM jobs WHERE status='uploading' AND updated_at<?", (time.time()-600,)).fetchall()
            for row in abandoned:
                self._remove_input(row[0])
                con.execute("UPDATE jobs SET status='failed',stage=?,updated_at=?,error_code='INTERRUPTED',error_message=? WHERE id=?", ("파일 전송 중단", time.time(), "파일 전송이 중단됐습니다. 파일을 다시 제출하십시오.", row[0]))
            rows = con.execute("SELECT id FROM jobs WHERE status NOT IN ('uploading','queued','running') AND updated_at<?", (time.time()-86400,)).fetchall()
            for row in rows:
                shutil.rmtree(self.path(row[0]), ignore_errors=True)
                con.execute("DELETE FROM jobs WHERE id=?", (row[0],))

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run, daemon=True, name="edurecover-media")
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def run(self):
        # A volume-wide lock avoids running the same paid request in two server processes.
        with (self.root / "worker.lock").open("a") as lock:
            while not self.stop_event.is_set():
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    self.stop_event.wait(1)
            if self.stop_event.is_set():
                return
            with self.connect() as con:
                interrupted = con.execute("SELECT id FROM jobs WHERE status='running' OR (status='uploading' AND updated_at<?)", (time.time()-600,)).fetchall()
                for row in interrupted:
                    self.update(row[0], "failed", "재시작으로 중단됨", "INTERRUPTED", "서버가 재시작되어 작업이 중단됐습니다. 파일을 다시 제출하십시오.")
                    self._remove_input(row[0])
            while not self.stop_event.is_set():
                try:
                    with self.connect() as con:
                        con.execute("BEGIN IMMEDIATE")
                        row = con.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
                        if row:
                            con.execute("UPDATE jobs SET status='running',stage=?,updated_at=? WHERE id=?", ("분석 시작", time.time(), row["id"]))
                    if row:
                        self.execute(dict(row))
                    else:
                        self.cleanup()
                        self.stop_event.wait(0.5)
                except Exception:
                    logger.exception("media_worker_iteration_failed")
                    self.stop_event.wait(1)

    def _remove_input(self, job_id):
        for path in self.path(job_id).glob("source.*"):
            path.unlink(missing_ok=True)
        # TemporaryDirectory cannot clean up after a killed process. Remove its
        # extracted WAV/frame files when recovering an interrupted job as well.
        for path in self.path(job_id).glob("media-work-*"):
            shutil.rmtree(path, ignore_errors=True)
        for subdir in ("work", "frames", "chunks"):
            shutil.rmtree(self.path(job_id) / subdir, ignore_errors=True)

    def execute(self, job):
        job_id = job["id"]
        try:
            sources = list(self.path(job_id).glob("source.*"))
            if len(sources) != 1:
                raise MediaError("INPUT_MISSING", "제출한 파일을 찾지 못했습니다.")
            process_media(sources[0], self.path(job_id), purpose=job["purpose"], language=job["language"], include_ocr=bool(job["include_ocr"]), progress=lambda stage, *_: self.update(job_id, "running", stage))
            self.update(job_id, "completed", "처리 완료")
        except MediaError as exc:
            self.update(job_id, "failed", "처리 실패", exc.code, str(exc))
        except Exception:
            logger.exception("media_job_failed id=%s", job_id)
            self.update(job_id, "failed", "처리 실패", "PROCESSING_FAILED", "파일 처리 중 오류가 발생했습니다.")
        finally:
            self._remove_input(job_id)


def default_root():
    return Path(os.getenv("MEDIA_STORAGE_ROOT", str(Path(os.getenv("EDURECOVER_CLASSROOM_STATE_DIR", ".edurecover_classroom_state")) / "media")))
