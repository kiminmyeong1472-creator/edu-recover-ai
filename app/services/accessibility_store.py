"""Owner-only sign video library, registered templates and actual player event records."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

class AccessibilityError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


class AccessibilityStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.videos = root / "videos"
        self.videos.mkdir(mode=0o700, exist_ok=True)
        self.db = root / "accessibility.sqlite3"
        with self.connect() as con:
            con.executescript("""CREATE TABLE IF NOT EXISTS videos (
                id TEXT PRIMARY KEY, phrase TEXT, title TEXT, extension TEXT,
                duration REAL, sha256 TEXT, created_at REAL);
                CREATE TABLE IF NOT EXISTS templates (
                id TEXT PRIMARY KEY, label TEXT, sequence_json TEXT, source_sha256 TEXT, created_at REAL);
                CREATE TABLE IF NOT EXISTS exposure (
                id TEXT PRIMARY KEY, video_id TEXT, source_sha256 TEXT, session_id TEXT,
                event TEXT, current_time REAL, visible INTEGER, received_at REAL);""")
        self.db.chmod(0o600)
        self.stop_event = threading.Event()
        self.cleanup_thread = None

    def connect(self):
        con = sqlite3.connect(self.db, timeout=15)
        con.row_factory = sqlite3.Row
        return con

    def check_space(self, incoming=0):
        self.cleanup_temporary()
        # Video archive capped at96MiB; processing uploads also count against this cap.
        used = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
        if used + incoming > 96 * 1024 * 1024:
            raise AccessibilityError("ACCESSIBILITY_STORAGE_FULL", "접근성 영상 저장 공간이 부족합니다. 불필요한 영상을 삭제하십시오.", 507)

    def cleanup_temporary(self):
        # A received request times out within180s; allow600s before reaping killed requests.
        import shutil
        for path in self.root.iterdir():
            if path.is_dir() and path.name.startswith(("braille-", "sign-", "template-", "gallery-")) and path.stat().st_mtime < time.time()-600:
                shutil.rmtree(path, ignore_errors=True)

    def start(self):
        if self.cleanup_thread and self.cleanup_thread.is_alive():
            return
        self.stop_event.clear()
        def cleanup_loop():
            while not self.stop_event.is_set():
                try:
                    self.cleanup_temporary()
                except OSError:
                    pass
                self.stop_event.wait(60)
        self.cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True, name="accessibility-cleanup")
        self.cleanup_thread.start()

    def stop(self):
        self.stop_event.set()

    def add_video(self, source: Path, phrase: str, title: str, duration: float):
        if not isinstance(phrase, str) or not 1 <= len(phrase.strip()) <= 200:
            raise AccessibilityError("INVALID_VIDEO_LABEL", "수어 영상에 붙일 문구를 입력하십시오.")
        self.check_space()  # The staged source already counts once.
        video_id = uuid.uuid4().hex
        extension = source.suffix.lower()
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        target = self.videos / (video_id + extension)
        source.replace(target)
        try:
            with self.connect() as con:
                con.execute("INSERT INTO videos VALUES (?,?,?,?,?,?,?)", (video_id, phrase, title, extension, duration, digest, time.time()))
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return self.get_video(video_id)

    def get_video(self, video_id):
        if not re.fullmatch(r"[0-9a-f]{32}", video_id):
            raise AccessibilityError("VIDEO_NOT_FOUND", "수어 영상을 찾지 못했습니다.", 404)
        with self.connect() as con:
            row = con.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        if row is None:
            raise AccessibilityError("VIDEO_NOT_FOUND", "수어 영상을 찾지 못했습니다.", 404)
        return {**dict(row), "expert_verified": False, "meaning_state": "OWNER_LABEL_UNCONFIRMED"}

    def list_videos(self):
        with self.connect() as con:
            ids = [r[0] for r in con.execute("SELECT id FROM videos ORDER BY created_at DESC LIMIT 100")]
        return [self.get_video(i) for i in ids]

    def video_path(self, video_id):
        item = self.get_video(video_id)
        path = self.videos / (item["id"] + item["extension"])
        if not path.is_file():
            raise AccessibilityError("VIDEO_NOT_FOUND", "수어 영상 파일을 찾지 못했습니다.", 404)
        return path

    def delete_video(self, video_id):
        path = self.video_path(video_id)
        with self.connect() as con:
            con.execute("DELETE FROM videos WHERE id=?", (video_id,))
            path.unlink(missing_ok=True)

    def add_template(self, label, sequence, source_sha256=""):
        from app.services.sign_core import classify_landmarks
        # Validate sequence shape/range through the same recognizer used for inference.
        classify_landmarks(sequence, [{"id": "validation", "label": label, "sequence": sequence}])
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 200:
            raise AccessibilityError("INVALID_LABEL", "등록할 수어·손동작 이름을 입력하십시오.")
        raw = json.dumps(sequence, allow_nan=False, separators=(",", ":"))
        if len(raw.encode()) > 512 * 1024:
            raise AccessibilityError("TEMPLATE_TOO_LARGE", "등록 자료가 너무 큽니다.", 413)
        template_id = uuid.uuid4().hex
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self.check_space(len(raw.encode()) + 65536)
            if con.execute("SELECT COUNT(*) FROM templates").fetchone()[0] >= 20:
                raise AccessibilityError("TEMPLATE_LIMIT", "등록 동작은 최대 20개입니다.", 409)
            con.execute("INSERT INTO templates VALUES (?,?,?,?,?)", (template_id, label.strip(), raw, source_sha256, time.time()))
        return {"id": template_id, "label": label.strip(), "observation_state": "UNCONFIRMED", "is_ksl_translation": False}

    def list_templates(self, include_sequence=False):
        with self.connect() as con:
            rows = con.execute("SELECT * FROM templates ORDER BY created_at").fetchall()
        items = []
        for row in rows:
            item = dict(row)
            sequence = item.pop("sequence_json")
            item.update({"observation_state": "UNCONFIRMED", "is_ksl_translation": False})
            if include_sequence:
                item["sequence"] = json.loads(sequence)
            items.append(item)
        return items

    def delete_template(self, template_id):
        with self.connect() as con:
            cur = con.execute("DELETE FROM templates WHERE id=?", (template_id,))
            if cur.rowcount != 1:
                raise AccessibilityError("TEMPLATE_NOT_FOUND", "등록 동작을 찾지 못했습니다.", 404)

    def record_exposure(self, video_id, session_id, event, current_time, visible):
        item = self.get_video(video_id)
        if current_time > item["duration"] + 2:
            raise AccessibilityError("INVALID_PLAYBACK_TIME", "영상 재생시간이 올바르지 않습니다.")
        event_id = uuid.uuid4().hex
        with self.connect() as con:
            con.execute("DELETE FROM exposure WHERE received_at<?", (time.time() - 30*86400,))
            con.execute("INSERT INTO exposure VALUES (?,?,?,?,?,?,?,?)", (event_id, video_id, item["sha256"], session_id, event, current_time, int(visible), time.time()))
            con.execute("DELETE FROM exposure WHERE id IN (SELECT id FROM exposure ORDER BY received_at DESC LIMIT -1 OFFSET 10000)")
        return {"id": event_id, "state": "PLAYBACK_EVENT_RECORDED", "visible": visible,
                "understanding_verified": False, "source_sha256": item["sha256"]}

    def exposures(self, session_id):
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM exposure WHERE session_id=? ORDER BY received_at LIMIT 1000", (session_id,))]


def accessibility_root():
    return Path(os.getenv("EDURECOVER_CLASSROOM_STATE_DIR", ".edurecover_classroom_state")) / "accessibility"
