"""
EduRecover Classroom Video Tutor + Electronic Whiteboard Prototype v0.1

목표
- 학교/학원 전자칠판에서 웹 브라우저로 바로 실행
- OPS(전자칠판 내장 Windows PC), 일반 PC/노트북, 태블릿에서도 동일 UI 사용
- 무선 캐스트/HDMI/USB-C 환경에서는 표시용 화면으로 동작
- 터치/펜 가능 장치에서는 Pointer Events 기반 판서
- 터치백 미지원 환경에서는 교사기기 + 학생 QR/코드 참가로 상호작용 유지
- 학습지도 -> 기초 설명/시범 -> 성립조건/추론 -> 심화 적용 (산파식 질문은 선택)
- 학생 실명 대신 alias(가명)만 저장

중요
- 이 파일은 동작 가능한 로컬/사설망 프로토타입입니다.
- Miracast/AirPlay/Google Cast/HDMI 자체 연결을 브라우저가 강제로 설정하지는 않습니다.
  해당 연결은 OS/전자칠판 기능을 이용하고, 본 앱은 연결 후 표시/상호작용을 담당합니다.
"""

from __future__ import annotations

import json
import base64
import binascii
import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from app.services.owner_basic_auth import require_owner, _credentials, _matches, _valid_session, _COOKIE, _same_origin
from app.services.structured_materials import (
    StructuredMaterialBundle, affected_assets, bundle_digest, bundle_lesson, publication_issues, example_bundle,
    issue_teacher_review_receipts,
)
from app.services.learning_path import (
    LearningPath, example_learning_path, learning_map, path_digest, path_lesson,
    publication_issues as learning_path_issues,
)
from app.training.tutor_learning import ContentTrustState
from app.training.tutor_ledger import TutorLearningLedger

APP_VERSION = "0.2.0"
STATE_DIR = Path(os.environ.get("EDURECOVER_CLASSROOM_STATE_DIR", ".edurecover_classroom_state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = STATE_DIR / "classroom_tutor.sqlite3"
TRAINING_DB_PATH = STATE_DIR / "ai_tutor_training.sqlite3"
TRAINING_LEDGER = TutorLearningLedger(TRAINING_DB_PATH)
logger = logging.getLogger(__name__)

classroom_tutor_router = APIRouter(prefix="/tutor", tags=["classroom-tutor"])
teacher_classroom_router = APIRouter(prefix="/classroom", tags=["teacher-classroom-product"])
classroom_basic = HTTPBasic(auto_error=False)
TEACHER_COOKIE = "__Host-edurecover_teacher"
TEACHER_SESSION_SECONDS = 8 * 3600
MIGRATION_LOCK = threading.Lock()

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  title TEXT NOT NULL,
  room_alias TEXT NOT NULL,
  institution_type TEXT NOT NULL,
  launch_mode TEXT NOT NULL,
  connection_method TEXT NOT NULL,
  privacy_mode TEXT NOT NULL,
  teacher_token TEXT NOT NULL,
  join_token TEXT NOT NULL,
  state_json TEXT NOT NULL,
  lesson_json TEXT NOT NULL,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS responses (
  response_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  student_alias TEXT NOT NULL,
  segment_id TEXT NOT NULL,
  answer TEXT NOT NULL,
  confidence INTEGER,
  reason_text TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_responses_session_segment ON responses(session_id, segment_id);
CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(session_id)
);
CREATE TABLE IF NOT EXISTS material_revisions (
  session_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  digest TEXT NOT NULL,
  bundle_json TEXT NOT NULL,
  reviewed_at TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(session_id, revision_id),
  FOREIGN KEY(session_id) REFERENCES sessions(session_id)
);
CREATE TABLE IF NOT EXISTS learning_path_revisions (
  session_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  digest TEXT NOT NULL,
  path_json TEXT NOT NULL,
  reviewed_at TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(session_id, revision_id),
  FOREIGN KEY(session_id) REFERENCES sessions(session_id)
);
CREATE TABLE IF NOT EXISTS response_material_snapshots (
  response_id TEXT PRIMARY KEY,
  segment_json TEXT NOT NULL,
  FOREIGN KEY(response_id) REFERENCES responses(response_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS board_strokes (
  session_id TEXT NOT NULL,
  stroke_id TEXT NOT NULL,
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  points_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(session_id, stroke_id),
  FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_board_strokes_session ON board_strokes(session_id, sequence);
CREATE TABLE IF NOT EXISTS classroom_media (
  asset_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  asset_token TEXT NOT NULL,
  media_type TEXT NOT NULL,
  file_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  file_size INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS classroom_teachers (
  username TEXT PRIMARY KEY,
  password_salt TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS classroom_teacher_sessions (
  session_hash TEXT PRIMARY KEY,
  username TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  FOREIGN KEY(username) REFERENCES classroom_teachers(username) ON DELETE CASCADE
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    with MIGRATION_LOCK:
        if "owner_teacher" not in {x["name"] for x in conn.execute("PRAGMA table_info(sessions)").fetchall()}:
            backup = DB_PATH.with_name("classroom_tutor.pre_teacher_migration.sqlite3")
            if not backup.exists() and conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]:
                temporary = backup.with_suffix(".tmp")
                try:
                    with sqlite3.connect(temporary) as destination:
                        conn.backup(destination)
                    os.replace(temporary, backup)
                finally:
                    temporary.unlink(missing_ok=True)
            conn.execute("ALTER TABLE sessions ADD COLUMN owner_teacher TEXT")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def json_load(text: str) -> Any:
    return json.loads(text) if text else None


def _teacher_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 260_000).hex()


def require_classroom_user(
    request: Request, credentials: HTTPBasicCredentials | None = Depends(classroom_basic),
) -> str:
    users, owner_password = _credentials()
    if _valid_session(request.cookies.get(_COOKIE), users, owner_password):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not _same_origin(request):
            raise HTTPException(403, "Same-origin request required.")
        return "__OWNER__"
    if credentials and _matches(credentials.username, credentials.password, users, owner_password):
        return "__OWNER__"
    cookie = request.cookies.get(TEACHER_COOKIE, "")
    username = None
    if cookie and len(cookie) <= 120:
        with db() as conn:
            row = conn.execute(
                """SELECT t.username FROM classroom_teacher_sessions s
                   JOIN classroom_teachers t ON t.username=s.username
                   WHERE s.session_hash=? AND s.expires_at>? AND t.active=1""",
                (hashlib.sha256(cookie.encode()).hexdigest(), int(datetime.now(timezone.utc).timestamp())),
            ).fetchone()
            username = row["username"] if row else None
        if username and request.method not in {"GET", "HEAD", "OPTIONS"} and not _same_origin(request):
            raise HTTPException(403, "Same-origin request required.")
    if not username and credentials:
        with db() as conn:
            row = conn.execute("SELECT * FROM classroom_teachers WHERE username=? AND active=1",
                               (credentials.username,)).fetchone()
        if row and hmac.compare_digest(_teacher_password(credentials.password, row["password_salt"]),
                                       row["password_hash"]):
            username = row["username"]
    if username:
        return username
    if request.method in {"GET", "HEAD"} and request.url.path == "/classroom":
        raise HTTPException(303, "교사 로그인 필요", headers={"Location": "/classroom/login"})
    raise HTTPException(401, "교사 로그인 필요", headers={"WWW-Authenticate": "Basic"})


class ClassroomTeacherCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=60)
    password: str = Field(min_length=12, max_length=256)


@teacher_classroom_router.post("/api/teachers")
def create_classroom_teacher(req: ClassroomTeacherCredentials, _: None = Depends(require_owner)):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{3,60}", req.username):
        raise HTTPException(400, "교사 아이디는 영문·숫자·밑줄·하이픈을 사용해 주세요.")
    salt = secrets.token_hex(16)
    with db() as conn:
        try:
            conn.execute("INSERT INTO classroom_teachers VALUES(?,?,?,?,?)",
                         (req.username, salt, _teacher_password(req.password, salt), 1, now_iso()))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "이미 등록된 교사 아이디입니다.") from exc
    return {"username": req.username, "created": True}


@teacher_classroom_router.post("/api/login")
def classroom_teacher_login(req: ClassroomTeacherCredentials, response: Response):
    with db() as conn:
        row = conn.execute("SELECT * FROM classroom_teachers WHERE username=? AND active=1",
                           (req.username,)).fetchone()
        valid = bool(row and hmac.compare_digest(_teacher_password(req.password, row["password_salt"]),
                                                row["password_hash"]))
        if not valid:
            raise HTTPException(401, "아이디 또는 비밀번호가 일치하지 않습니다.")
        token = secrets.token_urlsafe(32)
        expiry = int(datetime.now(timezone.utc).timestamp()) + TEACHER_SESSION_SECONDS
        conn.execute("DELETE FROM classroom_teacher_sessions WHERE expires_at<?", (expiry - TEACHER_SESSION_SECONDS,))
        conn.execute("INSERT INTO classroom_teacher_sessions VALUES(?,?,?)",
                     (hashlib.sha256(token.encode()).hexdigest(), req.username, expiry))
    response.set_cookie(TEACHER_COOKIE, token, max_age=TEACHER_SESSION_SECONDS,
                        httponly=True, secure=True, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


@teacher_classroom_router.post("/api/logout")
def classroom_teacher_logout(request: Request, response: Response):
    token = request.cookies.get(TEACHER_COOKIE, "")
    if not _same_origin(request):
        raise HTTPException(403, "Same-origin request required.")
    if token:
        with db() as conn:
            conn.execute("DELETE FROM classroom_teacher_sessions WHERE session_hash=?",
                         (hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie(TEACHER_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return {"ok": True}


def row_to_session(row: sqlite3.Row) -> dict[str, Any]:
    result = {
        "session_id": row["session_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "title": row["title"],
        "room_alias": row["room_alias"],
        "institution_type": row["institution_type"],
        "launch_mode": row["launch_mode"],
        "connection_method": row["connection_method"],
        "privacy_mode": row["privacy_mode"],
        "join_token": row["join_token"],
        "state": json_load(row["state_json"]),
        "lesson": json_load(row["lesson_json"]),
        "status": row["status"],
    }
    result["lesson_digest"] = lesson_digest(result["lesson"])
    return result


def student_session_view(row: sqlite3.Row, *, teacher_display: bool = False) -> dict[str, Any]:
    """Return only the lesson fields a participating student may receive."""
    session = row_to_session(row)
    ensure_material_release(session)
    hidden_fields = {
        "correct_answer",
        "branch_map",
        "misconception_path",
        "content_revision_id",
        "source_integrity_verified",
        "content_trust",
        "tutor_action_content_revision",
        "answer_disclosed",
    }
    # Do not expose future assessment or previous support through the JSON API.
    session["lesson"] = [x for x in session["lesson"]
                         if x["segment_id"] == session["state"].get("current_segment_id")]
    session["lesson"] = [
        {key: value for key, value in segment.items() if key not in hidden_fields}
        for segment in session["lesson"]
    ]
    # STOP keeps the board's static current step visible to its teacher, while
    # students must not receive a media capability the file endpoint has revoked.
    if session["state"].get("playback") == "STOPPED" and not teacher_display:
        for segment in session["lesson"]:
            for field in ("media_url", "media_asset_id", "media_sha256", "media_type"):
                segment.pop(field, None)
    session.pop("join_token", None)
    return session


class Segment(BaseModel):
    segment_id: str
    kind: Literal["EXPLAIN", "VIDEO", "SOCRATIC", "RETEST", "MAP", "DEMONSTRATE", "COMPARE", "PRACTICE"]
    title: str
    text: str = ""
    media_url: Optional[str] = None
    media_type: Optional[Literal["image/png", "image/jpeg", "audio/mpeg", "audio/wav", "video/mp4", "video/webm"]] = None
    pause_at_sec: Optional[float] = None
    choices: list[str] = Field(default_factory=list)
    correct_answer: Optional[str] = None
    misconception_path: Optional[str] = None
    branch_map: dict[str, str] = Field(default_factory=dict)
    concept_key: str = "UNSPECIFIED"
    content_revision_id: Optional[str] = None
    source_integrity_verified: bool = False
    content_trust: ContentTrustState = ContentTrustState.UNVERIFIED
    feedback_phase: Optional[Literal["PRE_FEEDBACK", "POST_FEEDBACK"]] = None
    independent_followup: bool = False
    delayed_hours: float = Field(default=0, ge=0)
    hint_level: int = Field(default=0, ge=0, le=10)
    answer_disclosed: bool = False
    tutor_action_content_revision: Optional[str] = None


class SessionCreate(BaseModel):
    title: str = "EduRecover 수업"
    room_alias: str = "CLASS-A"
    institution_type: Literal["SCHOOL", "ACADEMY", "OTHER"] = "SCHOOL"
    launch_mode: Literal[
        "DIRECT_BOARD_WEB", "WINDOWS_OPS_WEB", "CAST", "WIRED_MIRROR", "TEACHER_DEVICE"
    ] = "DIRECT_BOARD_WEB"
    connection_method: Literal[
        "BROWSER", "MIRACAST", "AIRPLAY", "GOOGLE_CAST", "HDMI", "USB_C", "VENDOR_CAST", "OTHER"
    ] = "BROWSER"
    privacy_mode: Literal["AGGREGATE_ONLY", "ALIAS_ONLY"] = "AGGREGATE_ONLY"
    lesson: list[Segment] = Field(default_factory=list)
    source_items: list[dict[str, Any]] | None = Field(default=None, min_length=1, max_length=50)
    source_item_index: int = Field(default=0, ge=0, le=49)
    origin_proof: str | None = Field(default=None, max_length=12000)
    verification_proof: str | None = Field(default=None, max_length=12000)


class Command(BaseModel):
    action: Literal[
        "PLAY", "PAUSE", "NEXT", "PREV", "GOTO", "STOP", "SET_SEGMENT", "SET_BOARD_MODE", "RESET_RESPONSES"
    ]
    segment_id: Optional[str] = None
    board_mode: Optional[str] = None


class StudentResponse(BaseModel):
    student_alias: str = Field(min_length=1, max_length=40)
    segment_id: str
    answer: str
    confidence: Optional[int] = Field(default=None, ge=0, le=100)
    reason_text: Optional[str] = Field(default=None, max_length=1000)
    join_token: str
    material_revision_id: Optional[str] = None
    learning_path_revision_id: Optional[str] = None


class LessonReplace(BaseModel):
    lesson: list[Segment]

class BoardStroke(BaseModel):
    stroke_id: str = Field(min_length=1, max_length=64)
    points: list[tuple[float, float]] = Field(min_length=2, max_length=1500)


class MediaUpload(BaseModel):
    media_type: Literal["image/png", "image/jpeg", "audio/mpeg", "audio/wav", "video/mp4", "video/webm"]
    data_base64: str = Field(min_length=8, max_length=12_000_000)


class EduRecoverQuestionBundle(BaseModel):
    """
    EduRecover 내부 DB에서 전자칠판 튜터로 넘기는 최소 작업문맥.
    출제원리/전체 해설DB/정답생성 로직 같은 영업비밀은 포함하지 않는다.
    """
    question_id: str
    stem: str
    choices: dict[str, str]
    selected_choice: Optional[str] = None
    correct_answer: Optional[str] = None
    misconception_path: Optional[str] = None
    concise_explanation: Optional[str] = None
    socratic_questions: list[str] = Field(default_factory=list)
    retest_choices: list[str] = Field(default_factory=list)
    retest_correct_answer: Optional[str] = None
    video_url: Optional[str] = None
    source_scope: str = "TUTOR_MINIMAL_CONTEXT"
    concept_key: str = "UNSPECIFIED"
    content_revision_id: Optional[str] = None
    source_integrity_verified: bool = False
    use_socratic: bool = False
    retest_stem: Optional[str] = None


class MaterialPublication(BaseModel):
    revision_id: str
    expected_digest: str
    teacher_verified_content: Literal[True]


class LearningPathPublication(BaseModel):
    revision_id: str
    expected_digest: str
    teacher_verified_content: Literal[True]


class LessonPublication(BaseModel):
    expected_digest: str
    teacher_verified_content: Literal[True]


def lesson_digest(lesson: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(lesson, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def ensure_material_release(session: dict[str, Any]) -> None:
    state = session["state"]
    if state.get("structured_materials"):
        active = state.get("active_material_revision")
        if not active or active != state.get("draft_material_revision"):
            raise HTTPException(423, "수정한 교수자료는 교사 검증과 확정 후 제공됩니다.")
        if any(x.get("structured_bundle_revision") != active for x in session["lesson"]):
            raise HTTPException(423, "교수자료 버전이 일치하지 않습니다.")
        released_digest = state.get("released_lesson_digest")
        if not isinstance(released_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", released_digest) \
                or not secrets.compare_digest(released_digest, lesson_digest(session["lesson"])):
            raise HTTPException(423, "확정한 교수자료의 내용이 변경되었습니다.")
    elif state.get("learning_path_mode"):
        active = state.get("active_learning_path_revision")
        if not active or active != state.get("draft_learning_path_revision"):
            raise HTTPException(423, "수정한 학습경로는 교사 검토 후 제공됩니다.")
        if not session["lesson"] or any(x.get("learning_path_revision") != active for x in session["lesson"]):
            raise HTTPException(423, "학습경로의 공개 버전이 일치하지 않습니다.")
        digest = state.get("released_lesson_digest")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) \
                or not secrets.compare_digest(digest, lesson_digest(session["lesson"])):
            raise HTTPException(423, "학습경로에서 검토한 자료가 변경되었습니다.")
    elif state.get("released_lesson_digest") != lesson_digest(session["lesson"]):
        raise HTTPException(423, "교사가 수업자료를 검토하고 확정한 뒤 제공됩니다.")


class TrainingReviewRequest(BaseModel):
    response_id: str = Field(min_length=1)
    teacher_validated: Literal[True]
    source_integrity_verified: bool = True
    content_trust: ContentTrustState = ContentTrustState.VERIFIED
    hint_or_explanation_confirmed: bool = False
    notes: Optional[str] = Field(default=None, max_length=1000)


class ContentDefectRequest(BaseModel):
    content_revision_id: str = Field(min_length=1)
    notes: Optional[str] = Field(default=None, max_length=1000)


class ActualTrainingUseRequest(BaseModel):
    eligibility_revision_id: str = Field(min_length=1)
    training_job_id: str = Field(min_length=1)
    tutor_model_version: str = Field(min_length=1)
    sample_hash: str = Field(min_length=64, max_length=64)
    confirmed_actual_use: Literal[True]



DEFAULT_LESSON = [
    Segment(segment_id="seg-1", kind="MAP", title="기초에서 목표까지",
            text="기울기 → 좌우 변화율 → 절댓값의 조건 → 매개변수 문제를 연결합니다."),
    Segment(segment_id="seg-2", kind="EXPLAIN", title="기초 개념과 시범",
            text="미분계수는 한 점에서의 변화율입니다. 절댓값을 취하면 음수 부분이 위로 반사됩니다. 좌우 변화율이 같은지 따로 확인합니다.", hint_level=1),
    Segment(segment_id="seg-3", kind="COMPARE", title="조건을 바꿔 비교",
            text="|x|는 원점에서 좌우 변화율이 -1과 1로 다릅니다. |x³|는 원점에서 좌우 변화율이 모두 0입니다. 축을 통과한다는 사실만으로 미분불가능하다고 할 수 없습니다.", hint_level=1),
    Segment(segment_id="seg-4", kind="PRACTICE", title="학습 정리",
            text="함수의 모양만 외우지 않고 좌우 차분몫과 성립조건을 확인합니다. 독립 적용 여부는 교사가 준비한 새로운 문항에서 따로 확인합니다."),
]


def build_lesson_from_bundle(bundle: EduRecoverQuestionBundle) -> list[dict[str, Any]]:
    """Explain and demonstrate as needed; Socratic questions are optional.

    A repeat of the original question remains practice, even with a new ID.
    """
    qid = bundle.question_id
    choices = [f"{k}. {v}" for k, v in sorted(bundle.choices.items())]
    original_answer = (f"{bundle.correct_answer}. {bundle.choices[bundle.correct_answer]}"
                       if bundle.correct_answer in bundle.choices else bundle.correct_answer)
    base_revision = bundle.content_revision_id or f"{qid}:runtime-v1"
    common = dict(concept_key=bundle.concept_key,
                  source_integrity_verified=bundle.source_integrity_verified,
                  content_trust=ContentTrustState.VERIFIED if bundle.source_integrity_verified else ContentTrustState.UNVERIFIED)
    independent = bool(bundle.retest_stem and bundle.retest_stem.strip() != bundle.stem.strip()
                       and bundle.retest_choices and bundle.retest_correct_answer)
    stages = [
        Segment(**common, segment_id=f"{qid}-problem", kind="EXPLAIN", title=f"목표 문제 {qid}",
                text=bundle.stem, choices=choices, correct_answer=original_answer,
                content_revision_id=f"{base_revision}:problem", feedback_phase="PRE_FEEDBACK"),
        Segment(**common, segment_id=f"{qid}-video", kind="VIDEO", title="기초 설명과 풀이 시범",
                text=bundle.concise_explanation or "필요한 기초 개념, 적용조건과 추론 단계를 교사가 설명합니다.",
                media_url=bundle.video_url or "", pause_at_sec=8, hint_level=1,
                content_revision_id=f"{base_revision}:hint"),
    ]
    if bundle.use_socratic:
        for index, question in enumerate(bundle.socratic_questions):
            stages.append(Segment(**common, segment_id=f"{qid}-socratic-{index+1}", kind="SOCRATIC",
                title="선택적 확인 질문", text=question,
                choices=["근거를 설명할 수 있다", "설명이 더 필요하다"],
                content_revision_id=f"{base_revision}:probe-{index+1}", feedback_phase="POST_FEEDBACK",
                hint_level=1, tutor_action_content_revision=f"{base_revision}:hint"))
    stages.append(Segment(**common, segment_id=f"{qid}-retest", kind="RETEST" if independent else "PRACTICE",
        title="새로운 문항에 독립 적용" if independent else "설명 후 연습",
        text=bundle.retest_stem or bundle.stem, choices=bundle.retest_choices or choices,
        correct_answer=bundle.retest_correct_answer or original_answer,
        content_revision_id=f"{base_revision}:transfer" if independent else f"{base_revision}:problem",
        independent_followup=independent, hint_level=0 if independent else 1,
        feedback_phase="PRE_FEEDBACK" if independent else "POST_FEEDBACK",
        tutor_action_content_revision=f"{base_revision}:hint"))
    return [stage.model_dump() for stage in stages]


def session_state(first_segment_id: str | None) -> dict[str, Any]:
    return {
        "current_segment_id": first_segment_id,
        "playback": "PAUSED",
        "board_mode": "LESSON",
        "teacher_override": False,
        "last_command_at": now_iso(),
    }


def emit(conn: sqlite3.Connection, session_id: str, event_type: str, payload: dict[str, Any]) -> int:
    cur = conn.execute(
        "INSERT INTO events(session_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
        (session_id, event_type, json.dumps(payload, ensure_ascii=False), now_iso()),
    )
    return int(cur.lastrowid)


def get_session_or_404(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(404, "session not found")
    return row


def require_teacher(row: sqlite3.Row, token: str | None):
    if not token or not secrets.compare_digest(token, row["teacher_token"]):
        raise HTTPException(403, "teacher token required")


@classroom_tutor_router.get("/api/health")
def health():
    return {"ok": True, "version": APP_VERSION, "db": str(DB_PATH)}


@classroom_tutor_router.get("/api/structured-materials/example")
def material_example(_: None = Depends(require_owner)):
    return example_bundle().model_dump()


@teacher_classroom_router.get("/api/structured-materials/example")
def teacher_material_example(_teacher: str = Depends(require_classroom_user)):
    """Give a signed-in teacher the same editable example as the owner."""
    return example_bundle().model_dump()


@classroom_tutor_router.get("/api/learning-path/example")
def owner_learning_path_example(_: None = Depends(require_owner)):
    return example_learning_path().model_dump()


@teacher_classroom_router.get("/api/learning-path/example")
def teacher_learning_path_example(_teacher: str = Depends(require_classroom_user)):
    return example_learning_path().model_dump()


def create_session_record(req: SessionCreate, *, product_prefix: str, owner_teacher: str | None = None) -> dict[str, str]:
    session_id = f"cls_{uuid.uuid4().hex[:10]}"
    teacher_token = uuid.uuid4().hex
    join_token = uuid.uuid4().hex[:12]
    lesson = [x.model_dump() for x in (req.lesson or DEFAULT_LESSON)]
    state = session_state(lesson[0]["segment_id"] if lesson else None)
    state["source_kind"] = "TEACHER_AUTHORED"
    state["released_lesson_digest"] = None
    if req.source_items is not None:
        from app.services.generation_provenance import verify_origin_proof, verify_verification_proof, ProvenanceError
        try:
            origin = verify_origin_proof(req.origin_proof or "", problems=req.source_items)
            verify_verification_proof(req.verification_proof or "", problems=req.source_items, origin_claims=origin)
        except ProvenanceError as exc:
            raise HTTPException(423, "현재 문항 전체에 대한 독립 교차검증 PASS 기록이 필요합니다.") from exc
        if req.source_item_index >= len(req.source_items):
            raise HTTPException(400, "unknown source_item_index")
        item = req.source_items[req.source_item_index]
        raw_choices = item.get("choices") or []
        if not isinstance(raw_choices, list) or not 2 <= len(raw_choices) <= 20:
            raise HTTPException(400, "튜터 연결에는 선택지 2개 이상이 필요합니다.")
        choices = {chr(65+i): str(value) for i, value in enumerate(raw_choices)}
        answer = str(item.get("answer") or "").strip()
        if answer in raw_choices:
            answer = chr(65+raw_choices.index(answer))
        elif answer.isdigit() and 1 <= int(answer) <= len(raw_choices):
            answer = chr(64+int(answer))
        elif answer in "①②③④⑤" and len(answer) == 1:
            answer = chr(65+"①②③④⑤".index(answer))
        if answer not in choices and answer not in [f"{k}. {v}" for k,v in choices.items()]:
            raise HTTPException(400, "출제 작업실에서 정답을 선택지와 일치하게 수정하고 재검증해 주세요.")
        bundle = EduRecoverQuestionBundle(
            question_id=f"verified-{session_id}",
            stem="\n\n".join(str(item.get(k) or "") for k in ("passage", "question")),
            choices=choices, correct_answer=answer, concise_explanation=str(item.get("explanation") or ""),
            concept_key=str(item.get("concept_key") or f"QUESTION:{session_id}"),
            content_revision_id=f"verified:{session_id}", source_integrity_verified=False,
        )
        lesson = build_lesson_from_bundle(bundle)
        state.update({"source_kind": "AI_GENERATED", "ai_release_proof_validated": True,
                      "current_segment_id": lesson[0]["segment_id"]})
    t = now_iso()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if owner_teacher and conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE owner_teacher=?", (owner_teacher,),
        ).fetchone()[0] >= 100:
            raise HTTPException(429, "교사 수업 저장 한도에 도달했습니다.")
        conn.execute(
            """INSERT INTO sessions
               (session_id,created_at,updated_at,title,room_alias,institution_type,
                launch_mode,connection_method,privacy_mode,teacher_token,join_token,
                state_json,lesson_json,status,owner_teacher)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id,
                t,
                t,
                req.title,
                req.room_alias,
                req.institution_type,
                req.launch_mode,
                req.connection_method,
                req.privacy_mode,
                teacher_token,
                join_token,
                json.dumps(state, ensure_ascii=False),
                json.dumps(lesson, ensure_ascii=False),
                "ACTIVE",
                owner_teacher,
            ),
        )
        emit(conn, session_id, "SESSION_CREATED", {"state": state})
    return {
        "session_id": session_id,
        "teacher_token": teacher_token,
        "join_token": join_token,
        "board_url": f"{product_prefix}/board/{session_id}?teacher_token={teacher_token}",
        "teacher_url": f"{product_prefix}/teacher/{session_id}?teacher_token={teacher_token}",
        "join_url": f"{product_prefix}/join/{session_id}?token={join_token}",
    }


@classroom_tutor_router.post("/api/sessions")
def create_session(req: SessionCreate, _: None = Depends(require_owner)):
    return create_session_record(req, product_prefix="/tutor")


@teacher_classroom_router.post("/api/sessions")
def create_teacher_product_session(req: SessionCreate, teacher: str = Depends(require_classroom_user)):
    return create_session_record(req, product_prefix="/classroom",
                                 owner_teacher=None if teacher == "__OWNER__" else teacher)


@classroom_tutor_router.get("/api/sessions/{session_id}")
def get_session(
    session_id: str,
    response: Response,
    teacher_token: str | None = None,
    join_token: str | None = None,
    display_only: bool = False,
):
    response.headers["Cache-Control"] = "no-store"
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        if teacher_token and secrets.compare_digest(teacher_token, row["teacher_token"]):
            return student_session_view(row, teacher_display=True) if display_only else row_to_session(row)
        if join_token and secrets.compare_digest(join_token, row["join_token"]):
            return student_session_view(row)
        raise HTTPException(403, "session token required")


@classroom_tutor_router.post("/api/sessions/{session_id}/publish-lesson")
def publish_legacy_lesson(session_id: str, req: LessonPublication, teacher_token: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
        session = row_to_session(row)
        state = session["state"]
        if state.get("structured_materials") or state.get("learning_path_mode"):
            raise HTTPException(409, "이 수업은 전용 교수자료 또는 학습경로 검토 화면에서 확정해 주세요.")
        digest = lesson_digest(session["lesson"])
        if not secrets.compare_digest(digest, req.expected_digest):
            raise HTTPException(409, "검토한 수업자료가 바뀌었습니다. 다시 확인해 주세요.")
        if not session["lesson"]:
            raise HTTPException(423, "확정할 수업자료가 없습니다.")
        if state.get("source_kind") == "AI_GENERATED" and not state.get("ai_release_proof_validated"):
            raise HTTPException(423, "변경된 AI 문항의 독립 교차검증이 필요합니다.")
        state["released_lesson_digest"] = digest
        state["playback"] = "PAUSED"
        conn.execute("UPDATE sessions SET state_json=?,updated_at=? WHERE session_id=?",
                     (json.dumps(state, ensure_ascii=False), now_iso(), session_id))
        emit(conn, session_id, "LESSON_PUBLISHED", {"lesson_digest": digest, "teacher_verified_content": True,
             "source_kind": state.get("source_kind", "LEGACY_TEACHER_REVIEW")})
    return {"ok": True, "lesson_digest": digest}


@classroom_tutor_router.put("/api/sessions/{session_id}/structured-materials")
def save_material_draft(session_id: str, req: StructuredMaterialBundle, teacher_token: str):
    digest = bundle_digest(req)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
        state = json_load(row["state_json"])
        if state.get("learning_path_mode"):
            raise HTTPException(409, "학습경로 수업은 학습경로 편집 화면에서 수정해 주세요.")
        existing = conn.execute(
            "SELECT digest FROM material_revisions WHERE session_id=? AND revision_id=?",
            (session_id, req.revision_id),
        ).fetchone()
        if existing and existing["digest"] != digest:
            raise HTTPException(409, "수정한 자료에는 새로운 revision_id가 필요합니다.")
        previous_row = conn.execute(
            "SELECT bundle_json FROM material_revisions WHERE session_id=? AND revision_id=?",
            (session_id, state.get("draft_material_revision")),
        ).fetchone()
        previous = StructuredMaterialBundle.model_validate_json(previous_row["bundle_json"]) if previous_row else None
        # A condition revision is immutable even across bundle revisions.
        history = conn.execute("SELECT bundle_json FROM material_revisions WHERE session_id=?", (session_id,)).fetchall()
        historic_conditions = {}
        for old in history:
            for scenario in json_load(old["bundle_json"])["scenarios"]:
                historic_conditions[scenario["condition_revision_id"]] = scenario["conditions"]
        if any(s.condition_revision_id in historic_conditions and
               historic_conditions[s.condition_revision_id] != s.conditions for s in req.scenarios):
            raise HTTPException(409, "조건을 바꾸면 condition_revision_id도 새로 지정해야 합니다.")
        conn.execute(
            "INSERT OR IGNORE INTO material_revisions VALUES(?,?,?,?,?,?)",
            (session_id, req.revision_id, digest, req.model_dump_json(), None, now_iso()),
        )
        changed = affected_assets(previous, req)
        state.update({"structured_materials": True, "draft_material_revision": req.revision_id,
                      "active_material_revision": None, "playback": "STOPPED",
                      "current_segment_id": None, "last_command_at": now_iso()})
        conn.execute("UPDATE sessions SET state_json=?,updated_at=? WHERE session_id=?",
                     (json.dumps(state, ensure_ascii=False), now_iso(), session_id))
        emit(conn, session_id, "MATERIAL_DRAFT_SAVED", {"revision_id": req.revision_id,
             "affected_assets": changed, "previous_release_withdrawn": True})
    return {"revision_id": req.revision_id, "digest": digest, "affected_assets": changed,
            "publication_issues": publication_issues(req), "student_release": "BLOCKED"}


@classroom_tutor_router.get("/api/sessions/{session_id}/structured-materials")
def get_material_draft(session_id: str, teacher_token: str):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
        state = json_load(row["state_json"])
        material = conn.execute("SELECT * FROM material_revisions WHERE session_id=? AND revision_id=?",
                                (session_id, state.get("draft_material_revision"))).fetchone()
        if not material:
            raise HTTPException(404, "교수자료 초안이 없습니다.")
        bundle = StructuredMaterialBundle.model_validate_json(material["bundle_json"])
        return {"bundle": bundle.model_dump(), "digest": material["digest"],
                "publication_issues": publication_issues(bundle),
                "active_revision": state.get("active_material_revision"),
                 "preview_lesson": bundle_lesson(bundle) if not publication_issues(bundle) else []}


MEDIA_REPRESENTATION_KINDS = frozenset({"IMAGE", "VIDEO", "UPLOADED_AUDIO"})


def _verified_material_media(
    conn: sqlite3.Connection, session_id: str, asset_id: str, expected_sha256: str, expected_type: str,
) -> tuple[str, bytes]:
    """Bind a draft reference to this session's immutable uploaded file bytes."""
    if not re.fullmatch(r"[0-9a-f]{32}", asset_id or "") or not re.fullmatch(
        r"[0-9a-f]{64}", expected_sha256 or ""
    ) or expected_type not in MEDIA_SIGNATURES:
        raise HTTPException(423, "수업 매체의 식별값을 확인할 수 없습니다.")
    item = conn.execute(
        "SELECT asset_token,media_type,file_path,sha256,file_size FROM classroom_media "
        "WHERE session_id=? AND asset_id=?", (session_id, asset_id),
    ).fetchone()
    if not item or item["media_type"] != expected_type or not secrets.compare_digest(
        item["sha256"], expected_sha256
    ):
        raise HTTPException(423, "이 수업에 등록된 매체와 검토한 매체가 일치하지 않습니다.")
    path = Path(item["file_path"])
    expected_path = STATE_DIR / "classroom_media" / (asset_id + MEDIA_SIGNATURES[expected_type][1])
    if path != expected_path or path.is_symlink() or path.parent.is_symlink() or STATE_DIR.is_symlink():
        raise HTTPException(423, "매체 파일의 저장 경로가 안전하지 않습니다.")
    if not isinstance(item["file_size"], int) or not 0 < item["file_size"] <= 8_000_000:
        raise HTTPException(423, "매체 파일 크기를 확인할 수 없습니다.")
    try:
        with path.open("rb") as source:
            data = source.read(8_000_001)
    except OSError as exc:
        raise HTTPException(423, "검토한 매체 파일을 읽을 수 없습니다.") from exc
    if len(data) != item["file_size"] or not secrets.compare_digest(
        hashlib.sha256(data).hexdigest(), expected_sha256
    ):
        raise HTTPException(423, "검토한 매체 파일이 변경되었습니다.")
    url = f"/tutor/api/sessions/{session_id}/media/{asset_id}?asset_token={item['asset_token']}"
    return url, data


def _require_learning_path_teacher(row: sqlite3.Row, teacher_token: str, teacher: str) -> None:
    """A path is editable only by its active account, not a stale URL alone."""
    require_teacher(row, teacher_token)
    if teacher != "__OWNER__" and row["owner_teacher"] != teacher:
        raise HTTPException(403, "이 교사의 수업이 아닙니다.")
    if teacher == "__OWNER__" and row["owner_teacher"] is not None:
        raise HTTPException(403, "해당 교사 계정에서 수업을 검토해 주세요.")


@teacher_classroom_router.put("/api/sessions/{session_id}/learning-path")
def save_learning_path(session_id: str, path: LearningPath, teacher_token: str,
                       teacher: str = Depends(require_classroom_user)):
    digest = path_digest(path)
    path_json = path.model_dump_json()
    if len(path_json.encode("utf-8")) > 750_000:
        raise HTTPException(413, "학습경로 초안의 크기 한도를 넘었습니다.")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_session_or_404(conn, session_id)
        _require_learning_path_teacher(row, teacher_token, teacher)
        state = json_load(row["state_json"])
        if state.get("structured_materials"):
            raise HTTPException(409, "구조화 교수자료 수업은 별도 새 수업에서 학습경로를 만드세요.")
        existing = conn.execute(
            "SELECT digest FROM learning_path_revisions WHERE session_id=? AND revision_id=?",
            (session_id, path.revision_id),
        ).fetchone()
        if existing and not secrets.compare_digest(existing["digest"], digest):
            raise HTTPException(409, "경로를 수정하면 새로운 revision_id를 지정해 주세요.")
        if not existing and conn.execute("SELECT COUNT(*) FROM learning_path_revisions WHERE session_id=?",
                                         (session_id,)).fetchone()[0] >= 100:
            raise HTTPException(429, "이 수업의 학습경로 버전 저장 한도에 도달했습니다.")
        conn.execute("INSERT OR IGNORE INTO learning_path_revisions VALUES(?,?,?,?,?,?)",
                     (session_id, path.revision_id, digest, path_json, None, now_iso()))
        state.update({"learning_path_mode": True, "draft_learning_path_revision": path.revision_id,
                      "active_learning_path_revision": None, "playback": "STOPPED",
                      "current_segment_id": None, "last_command_at": now_iso()})
        conn.execute("UPDATE sessions SET state_json=?,updated_at=? WHERE session_id=?",
                     (json.dumps(state, ensure_ascii=False), now_iso(), session_id))
        emit(conn, session_id, "LEARNING_PATH_DRAFT_SAVED",
             {"revision_id": path.revision_id, "previous_release_withdrawn": True})
    return {"revision_id": path.revision_id, "digest": digest,
            "learning_map": learning_map(path), "publication_issues": learning_path_issues(path),
            "student_release": "BLOCKED"}


@teacher_classroom_router.get("/api/sessions/{session_id}/learning-path")
def get_learning_path(session_id: str, teacher_token: str,
                      teacher: str = Depends(require_classroom_user)):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        _require_learning_path_teacher(row, teacher_token, teacher)
        state = json_load(row["state_json"])
        saved = conn.execute(
            "SELECT * FROM learning_path_revisions WHERE session_id=? AND revision_id=?",
            (session_id, state.get("draft_learning_path_revision")),
        ).fetchone()
        if not saved:
            raise HTTPException(404, "저장된 학습경로가 없습니다.")
        path = LearningPath.model_validate_json(saved["path_json"])
    return {"path": path.model_dump(), "digest": saved["digest"],
            "learning_map": learning_map(path),
            "publication_issues": learning_path_issues(
                path, teacher_reviewed_digest=saved["digest"] if saved["reviewed_at"] and
                state.get("active_learning_path_revision") == path.revision_id else None),
            "active_revision": state.get("active_learning_path_revision")}


@teacher_classroom_router.post("/api/sessions/{session_id}/learning-path/publish")
def publish_learning_path(session_id: str, req: LearningPathPublication, teacher_token: str,
                          teacher: str = Depends(require_classroom_user)):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_session_or_404(conn, session_id)
        _require_learning_path_teacher(row, teacher_token, teacher)
        state = json_load(row["state_json"])
        if not state.get("learning_path_mode") or state.get("draft_learning_path_revision") != req.revision_id:
            raise HTTPException(409, "최신 학습경로 초안을 다시 불러와 주세요.")
        saved = conn.execute(
            "SELECT * FROM learning_path_revisions WHERE session_id=? AND revision_id=?",
            (session_id, req.revision_id),
        ).fetchone()
        if not saved or not secrets.compare_digest(saved["digest"], req.expected_digest):
            raise HTTPException(409, "검토한 학습경로와 저장된 초안이 다릅니다.")
        path = LearningPath.model_validate_json(saved["path_json"])
        issues = learning_path_issues(path, teacher_reviewed_digest=saved["digest"])
        if issues:
            raise HTTPException(423, {"message": "학습경로의 검토 조건이 충족되지 않았습니다.",
                                      "issues": issues})
        media_urls: dict[str, str] = {}
        media_types: dict[str, str] = {}
        media_hashes: dict[str, str] = {}
        for asset in path.assets:
            if not asset.media_id:
                continue
            item = conn.execute("SELECT media_type,sha256 FROM classroom_media WHERE session_id=? AND asset_id=?",
                                (session_id, asset.media_id)).fetchone()
            if not item or (asset.kind == "IMAGE" and not item["media_type"].startswith("image/")) or \
                    (asset.kind == "VIDEO" and not item["media_type"].startswith("video/")) or \
                    (asset.kind == "AUDIO" and not item["media_type"].startswith("audio/")):
                raise HTTPException(423, "학습경로의 자료 유형 또는 수업별 소유권이 일치하지 않습니다.")
            media_urls[asset.asset_id] = _verified_material_media(
                conn, session_id, asset.media_id, item["sha256"], item["media_type"],
            )[0]
            media_types[asset.asset_id] = item["media_type"]
            media_hashes[asset.asset_id] = item["sha256"]
        lesson = path_lesson(path, media_urls=media_urls, media_types=media_types,
                             media_hashes=media_hashes)
        state.update({"active_learning_path_revision": path.revision_id, "playback": "PAUSED",
                      "current_segment_id": lesson[0]["segment_id"], "last_command_at": now_iso(),
                      "source_kind": "TEACHER_REVIEWED_LEARNING_PATH",
                      "released_lesson_digest": lesson_digest(lesson)})
        conn.execute("UPDATE learning_path_revisions SET reviewed_at=? WHERE session_id=? AND revision_id=?",
                     (now_iso(), session_id, req.revision_id))
        conn.execute("UPDATE sessions SET state_json=?,lesson_json=?,updated_at=? WHERE session_id=?",
                     (json.dumps(state, ensure_ascii=False), json.dumps(lesson, ensure_ascii=False),
                      now_iso(), session_id))
        emit(conn, session_id, "LEARNING_PATH_PUBLISHED",
             {"revision_id": path.revision_id, "digest": saved["digest"],
              "lesson_digest": state["released_lesson_digest"],
              "session_media_sha256": media_hashes,
              "review_basis": "TEACHER_ATTESTED_PATH_AND_SESSION_MEDIA"})
    return {"ok": True, "active_revision": path.revision_id, "segment_count": len(lesson)}


@classroom_tutor_router.post("/api/sessions/{session_id}/structured-materials/publish")
def publish_materials(session_id: str, req: MaterialPublication, teacher_token: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
        state = json_load(row["state_json"])
        if state.get("draft_material_revision") != req.revision_id:
            raise HTTPException(409, "최신 초안이 아닙니다. 다시 검토해 주세요.")
        material = conn.execute("SELECT * FROM material_revisions WHERE session_id=? AND revision_id=?",
                                (session_id, req.revision_id)).fetchone()
        if not material or not secrets.compare_digest(material["digest"], req.expected_digest):
            raise HTTPException(409, "검토한 자료와 현재 자료가 다릅니다.")
        bundle = StructuredMaterialBundle.model_validate_json(material["bundle_json"])
        media_urls = {}
        for asset in bundle.representations:
            if asset.kind in MEDIA_REPRESENTATION_KINDS:
                media_urls[asset.asset_id] = _verified_material_media(
                    conn, session_id, asset.media_asset_id or "", asset.media_sha256 or "",
                    asset.media_type or "",
                )[0]
        try:
            reviewed = issue_teacher_review_receipts(bundle)
        except ValueError as exc:
            raise HTTPException(423, {"message": "자료 검증이 완료되지 않았습니다.",
                                      "issues": publication_issues(bundle)}) from exc
        lesson = bundle_lesson(reviewed)
        for step in lesson:
            if step.get("media_asset_id"):
                step["media_url"] = media_urls[step["segment_id"]]
        reviewed_digest = bundle_digest(reviewed)
        state.update({"active_material_revision": req.revision_id, "playback": "PAUSED",
                      "current_segment_id": lesson[0]["segment_id"], "last_command_at": now_iso(),
                      "released_lesson_digest": lesson_digest(lesson)})
        conn.execute("UPDATE material_revisions SET bundle_json=?,digest=?,reviewed_at=? "
                     "WHERE session_id=? AND revision_id=?",
                     (reviewed.model_dump_json(), reviewed_digest, now_iso(), session_id, req.revision_id))
        conn.execute("UPDATE sessions SET state_json=?,lesson_json=?,updated_at=? WHERE session_id=?",
                     (json.dumps(state, ensure_ascii=False), json.dumps(lesson, ensure_ascii=False), now_iso(), session_id))
        emit(conn, session_id, "MATERIAL_PUBLISHED", {"revision_id": req.revision_id,
             "digest": reviewed_digest, "review_basis": "TEACHER_REVIEWED_MANIFEST_AND_FILES"})
    return {"ok": True, "active_revision": req.revision_id, "segment_count": len(lesson)}


def require_legacy_lesson_edit(row: sqlite3.Row) -> None:
    if json_load(row["state_json"]).get("structured_materials"):
        raise HTTPException(409, "구조화 교수자료는 교수자료 초안에서 수정하고 다시 확정해 주세요.")
    if json_load(row["state_json"]).get("learning_path_mode"):
        raise HTTPException(409, "학습경로 수업은 학습경로 편집 화면에서 수정하고 다시 확정해 주세요.")
    if json_load(row["state_json"]).get("source_kind") == "AI_GENERATED":
        raise HTTPException(409, "AI 문항을 출제 작업실에서 수정·재검증한 뒤 새 수업으로 연결해 주세요.")



@classroom_tutor_router.post("/api/sessions/{session_id}/load-edurecover-question")
def load_edurecover_question(session_id: str, req: EduRecoverQuestionBundle, teacher_token: str):
    """
    EduRecover QUESTION_DB/CHOICE_DB/해설DB에서 허용된 최소 조각만 받아 수업 단계로 변환한다.
    전체 내부 DB나 출제원리 원문은 이 API로 받지 않는다.
    """
    if req.source_scope != "TUTOR_MINIMAL_CONTEXT":
        raise HTTPException(400, "source_scope must be TUTOR_MINIMAL_CONTEXT")
    lesson = build_lesson_from_bundle(req)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
        require_legacy_lesson_edit(row)
        state = json_load(row["state_json"])
        state["current_segment_id"] = lesson[0]["segment_id"] if lesson else None
        state["playback"] = "PAUSED"
        state["last_command_at"] = now_iso()
        conn.execute(
            "UPDATE sessions SET lesson_json=?,state_json=?,updated_at=? WHERE session_id=?",
            (json.dumps(lesson, ensure_ascii=False), json.dumps(state, ensure_ascii=False), now_iso(), session_id),
        )
        emit(conn, session_id, "EDURECOVER_CONTEXT_LOADED", {
            "question_id": req.question_id,
            "selected_choice": req.selected_choice,
            "source_scope": req.source_scope,
            "segment_count": len(lesson),
        })
    return {"ok": True, "question_id": req.question_id, "segment_count": len(lesson), "state": state}

@classroom_tutor_router.put("/api/sessions/{session_id}/lesson")
def replace_lesson(session_id: str, req: LessonReplace, teacher_token: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
        require_legacy_lesson_edit(row)
        lesson = [x.model_dump() for x in req.lesson]
        state = json_load(row["state_json"])
        state["current_segment_id"] = lesson[0]["segment_id"] if lesson else None
        state["playback"] = "PAUSED"
        state["last_command_at"] = now_iso()
        conn.execute(
            "UPDATE sessions SET lesson_json=?,state_json=?,updated_at=? WHERE session_id=?",
            (json.dumps(lesson, ensure_ascii=False), json.dumps(state, ensure_ascii=False), now_iso(), session_id),
        )
        emit(conn, session_id, "LESSON_REPLACED", {"segment_count": len(lesson)})
        return {"ok": True, "segment_count": len(lesson)}


@classroom_tutor_router.post("/api/sessions/{session_id}/command")
def command(session_id: str, req: Command, teacher_token: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
        if req.action not in {"STOP", "PAUSE"}:
            ensure_material_release(row_to_session(row))
        state = json_load(row["state_json"])
        lesson = json_load(row["lesson_json"]) or []
        ids = [x["segment_id"] for x in lesson]
        current = state.get("current_segment_id")
        idx = ids.index(current) if current in ids else 0

        if req.action == "PLAY":
            state["playback"] = "PLAYING"
            state["play_command_revision"] = int(state.get("play_command_revision", 0)) + 1
        elif req.action == "PAUSE":
            state["playback"] = "PAUSED"
        elif req.action == "STOP":
            state["playback"] = "STOPPED"
        elif req.action == "NEXT" and ids:
            state["current_segment_id"] = ids[min(idx + 1, len(ids) - 1)]
            state["playback"] = "PAUSED"
        elif req.action == "PREV" and ids:
            state["current_segment_id"] = ids[max(idx - 1, 0)]
            state["playback"] = "PAUSED"
        elif req.action in {"GOTO", "SET_SEGMENT"}:
            if req.segment_id not in ids:
                raise HTTPException(400, "unknown segment_id")
            state["current_segment_id"] = req.segment_id
            state["playback"] = "PAUSED"
        elif req.action == "SET_BOARD_MODE":
            state["board_mode"] = req.board_mode or "LESSON"
        elif req.action == "RESET_RESPONSES":
            conn.execute("DELETE FROM responses WHERE session_id=?", (session_id,))

        state["teacher_override"] = True
        state["last_command_at"] = now_iso()
        conn.execute(
            "UPDATE sessions SET state_json=?,updated_at=? WHERE session_id=?",
            (json.dumps(state, ensure_ascii=False), now_iso(), session_id),
        )
        event_id = emit(conn, session_id, "TEACHER_COMMAND", {"action": req.action, "state": state})
    training_batch = None
    if req.action == "STOP":
        try:
            training_batch = TRAINING_LEDGER.refresh_lesson_batch(session_id, create=True)
        except Exception as exc:
            logger.exception("tutor_lesson_batch_failed session_id=%s error_type=%s", session_id, type(exc).__name__)
            training_batch = {"session_id": session_id, "status": "QUEUE_FAILED_HOLD", "gpu_started": False}
    return {"ok": True, "event_id": event_id, "state": state, "training_batch": training_batch}


@classroom_tutor_router.get("/api/sessions/{session_id}/board-strokes")
def list_board_strokes(session_id: str, teacher_token: str):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
        strokes = conn.execute(
            "SELECT stroke_id,points_json FROM board_strokes WHERE session_id=? ORDER BY sequence",
            (session_id,),
        ).fetchall()
    return {"strokes": [{"stroke_id": x["stroke_id"], "points": json_load(x["points_json"])}
                        for x in strokes]}


@classroom_tutor_router.post("/api/sessions/{session_id}/board-strokes")
def save_board_stroke(session_id: str, stroke: BoardStroke, teacher_token: str):
    if any(not (0 <= x <= 1 and 0 <= y <= 1) for x, y in stroke.points):
        raise HTTPException(400, "판서 좌표가 범위를 벗어났습니다.")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
        count = conn.execute("SELECT COUNT(*) FROM board_strokes WHERE session_id=?", (session_id,)).fetchone()[0]
        if count >= 2000:
            raise HTTPException(413, "판서 저장 한도에 도달했습니다. 새 수업을 만들어 주세요.")
        conn.execute(
            "INSERT OR IGNORE INTO board_strokes(session_id,stroke_id,points_json,created_at) VALUES(?,?,?,?)",
            (session_id, stroke.stroke_id, json.dumps(stroke.points), now_iso()),
        )
    return {"ok": True}


@classroom_tutor_router.delete("/api/sessions/{session_id}/board-strokes")
def clear_board_strokes(session_id: str, teacher_token: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
        conn.execute("DELETE FROM board_strokes WHERE session_id=?", (session_id,))
        emit(conn, session_id, "BOARD_CLEARED", {})
    return {"ok": True}


MEDIA_SIGNATURES = {
    "image/png": (b"\x89PNG\r\n\x1a\n", ".png"),
    "image/jpeg": (b"\xff\xd8\xff", ".jpg"),
    "audio/mpeg": (b"ID3", ".mp3"),
    "audio/wav": (b"RIFF", ".wav"),
    "video/mp4": (b"", ".mp4"),
    "video/webm": (b"\x1a\x45\xdf\xa3", ".webm"),
}


@classroom_tutor_router.post("/api/sessions/{session_id}/media")
def upload_classroom_media(session_id: str, req: MediaUpload, teacher_token: str):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
    try:
        raw = base64.b64decode(req.data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, "올바른 자료 파일이 아닙니다.") from exc
    if not raw or len(raw) > 8_000_000:
        raise HTTPException(413, "자료 파일은 8MB 이하로 등록해 주세요.")
    signature, extension = MEDIA_SIGNATURES[req.media_type]
    valid = raw.startswith(signature)
    if req.media_type == "video/mp4":
        valid = len(raw) > 12 and raw[4:8] == b"ftyp"
    if req.media_type == "audio/mpeg":
        valid = valid or (len(raw) > 2 and raw[0] == 0xff and raw[1] & 0xe0 == 0xe0)
    if req.media_type == "audio/wav":
        valid = valid and raw[8:12] == b"WAVE"
    if not valid:
        raise HTTPException(400, "선택한 자료 형식과 파일 내용이 다릅니다.")
    with db() as conn:
        session_usage = conn.execute(
            "SELECT COALESCE(SUM(file_size),0),COUNT(*) FROM classroom_media WHERE session_id=?",
            (session_id,),
        ).fetchone()
        total_usage = conn.execute("SELECT COALESCE(SUM(file_size),0) FROM classroom_media").fetchone()[0]
    if session_usage[1] >= 12 or session_usage[0] + len(raw) > 50_000_000:
        raise HTTPException(413, "수업당 자료 12개·50MB 한도를 넘었습니다.")
    if total_usage + len(raw) > 350_000_000:
        raise HTTPException(507, "자료 저장 공간 한도에 도달했습니다.")
    asset_id, asset_token = uuid.uuid4().hex, secrets.token_urlsafe(24)
    media_dir = STATE_DIR / "classroom_media"
    media_dir.mkdir(parents=True, exist_ok=True)
    path = media_dir / (asset_id + extension)
    temporary = media_dir / (asset_id + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)
    try:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = get_session_or_404(conn, session_id)
            require_teacher(row, teacher_token)
            conn.execute(
                "INSERT INTO classroom_media VALUES(?,?,?,?,?,?,?,?)",
                (asset_id, session_id, asset_token, req.media_type, str(path),
                 hashlib.sha256(raw).hexdigest(), len(raw), now_iso()),
            )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"media_url": f"/tutor/api/sessions/{session_id}/media/{asset_id}?asset_token={asset_token}",
            "media_type": req.media_type, "sha256": hashlib.sha256(raw).hexdigest()}


@classroom_tutor_router.get("/api/sessions/{session_id}/media-library")
def classroom_media_library(session_id: str, teacher_token: str):
    """Show a teacher the actual uploaded files for review before publication."""
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
        items = conn.execute(
            "SELECT asset_id,sha256,media_type,file_size FROM classroom_media "
            "WHERE session_id=? ORDER BY created_at,asset_id LIMIT 12", (session_id,),
        ).fetchall()
        assets = []
        for item in items:
            try:
                url, _ = _verified_material_media(conn, session_id, item["asset_id"],
                                                   item["sha256"], item["media_type"])
            except HTTPException:
                continue
            assets.append({"asset_id": item["asset_id"], "sha256": item["sha256"],
                           "media_type": item["media_type"], "file_size": item["file_size"],
                           "media_url": url + "&teacher_token=" + teacher_token})
    return {"assets": assets}


@classroom_tutor_router.get("/api/sessions/{session_id}/media/{asset_id}")
def read_classroom_media(session_id: str, asset_id: str, asset_token: str,
                         teacher_token: str | None = None):
    with db() as conn:
        item = conn.execute(
            "SELECT * FROM classroom_media WHERE session_id=? AND asset_id=?",
            (session_id, asset_id),
        ).fetchone()
        if not item or not secrets.compare_digest(asset_token, item["asset_token"]):
            raise HTTPException(403, "자료 접근 권한이 없습니다.")
        row = get_session_or_404(conn, session_id)
        session = row_to_session(row)
        if not (teacher_token and secrets.compare_digest(teacher_token, row["teacher_token"])):
            # An upload URL is a capability for this file, not a publication
            # receipt. Only the current, still-active lesson step may consume it.
            ensure_material_release(session)
            state = session["state"]
            if state.get("playback") == "STOPPED":
                raise HTTPException(423, "중지된 수업의 매체는 제공하지 않습니다.")
            published_url = (f"/tutor/api/sessions/{session_id}/media/{asset_id}"
                             f"?asset_token={item['asset_token']}")
            current_segment_id = state.get("current_segment_id")
            if not any(
                (step.get("media_asset_id") == asset_id and
                 step.get("media_type") == item["media_type"] and
                 step.get("media_sha256") == item["sha256"]) or
                (step.get("media_url") == published_url and
                 step.get("media_type") == item["media_type"])
                for step in session["lesson"]
                if current_segment_id is not None and step.get("segment_id") == current_segment_id
            ):
                raise HTTPException(423, "현재 수업 단계에 공개되지 않은 매체입니다.")
        _, data = _verified_material_media(
            conn, session_id, asset_id, item["sha256"], item["media_type"],
        )
    return Response(content=data, media_type=item["media_type"], headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline",
    })


@classroom_tutor_router.post("/api/sessions/{session_id}/respond")
def respond(session_id: str, req: StudentResponse):
    training_payload: dict[str, Any] | None = None
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_session_or_404(conn, session_id)
        if req.join_token != row["join_token"]:
            raise HTTPException(403, "invalid join token")
        session = row_to_session(row)
        ensure_material_release(session)
        lesson = json_load(row["lesson_json"]) or []
        segment = next((x for x in lesson if x["segment_id"] == req.segment_id), None)
        if not segment:
            raise HTTPException(400, "unknown segment")
        if session["state"].get("playback") == "STOPPED":
            raise HTTPException(423, "교사가 수업을 중지했습니다.")
        if req.segment_id != session["state"].get("current_segment_id"):
            raise HTTPException(409, "현재 제공된 문항에 응답해 주세요.")
        if session["state"].get("structured_materials"):
            if req.material_revision_id != session["state"].get("active_material_revision"):
                raise HTTPException(409, "응답한 교수자료 버전이 바뀌었습니다. 새 문항을 확인해 주세요.")
            if not segment.get("choices"):
                raise HTTPException(400, "현재는 응답 단계가 아닙니다.")
        if session["state"].get("learning_path_mode"):
            if req.learning_path_revision_id != session["state"].get("active_learning_path_revision"):
                raise HTTPException(409, "응답한 학습경로의 조건 버전이 바뀌었습니다.")
            if not segment.get("choices"):
                raise HTTPException(400, "현재는 응답 단계가 아닙니다.")
        # Retrying after feedback is assisted evidence, never a new independent test.
        repeated = conn.execute(
            "SELECT 1 FROM responses WHERE session_id=? AND student_alias=? AND segment_id=? LIMIT 1",
            (session_id, req.student_alias, req.segment_id),
        ).fetchone()
        if repeated and segment.get("independent_followup"):
            segment = {**segment, "independent_followup": False, "feedback_phase": "POST_FEEDBACK"}
        rid = f"rsp_{uuid.uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO responses VALUES(?,?,?,?,?,?,?,?)",
            (
                rid,
                session_id,
                req.student_alias,
                req.segment_id,
                req.answer,
                req.confidence,
                req.reason_text,
                now_iso(),
            ),
        )
        conn.execute("INSERT INTO response_material_snapshots VALUES(?,?)",
                     (rid, json.dumps(segment, ensure_ascii=False)))
        event_id = emit(
            conn,
            session_id,
            "STUDENT_RESPONSE",
            {"response_id": rid, "segment_id": req.segment_id, "answer": req.answer},
        )
        branch_to = (segment.get("branch_map") or {}).get(req.answer)
        correct = None if segment.get("correct_answer") is None else req.answer == segment.get("correct_answer")
    try:
        training_payload = TRAINING_LEDGER.capture_response(
            session_id=session_id,
            student_alias=req.student_alias,
            segment=segment,
            response_ref=rid,
            correct=correct,
            reason_present=bool((req.reason_text or "").strip()),
            confidence=req.confidence,
        )
    except Exception as exc:
        logger.exception("tutor_training_capture_failed session_id=%s error_type=%s", session_id, type(exc).__name__)
        training_payload = {"status": "CAPTURE_FAILED_HOLD"}
    student_visible_correct = None if segment.get("feedback_phase") == "PRE_FEEDBACK" else correct
    return {
        "ok": True,
        "event_id": event_id,
        "response_id": rid,
        "branch_candidate": branch_to,
        "correct": student_visible_correct,
        "training": training_payload,
    }


@classroom_tutor_router.get("/api/sessions/{session_id}/results")
def results(session_id: str, teacher_token: str, segment_id: Optional[str] = None):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
        args: list[Any] = [session_id]
        where = "session_id=?"
        if segment_id:
            where += " AND segment_id=?"
            args.append(segment_id)
        rows = conn.execute(
            f"SELECT segment_id,answer,COUNT(*) AS n FROM responses WHERE {where} GROUP BY segment_id,answer ORDER BY segment_id,n DESC",
            args,
        ).fetchall()
        totals = conn.execute(
            f"SELECT segment_id,COUNT(*) AS n FROM responses WHERE {where} GROUP BY segment_id",
            args,
        ).fetchall()
        return {
            "counts": [dict(x) for x in rows],
            "totals": [dict(x) for x in totals],
        }


@classroom_tutor_router.get("/api/sessions/{session_id}/events")
def events(session_id: str, teacher_token: str, after: int = 0):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
        rows = conn.execute(
            "SELECT event_id,event_type,payload_json,created_at FROM events WHERE session_id=? AND event_id>? ORDER BY event_id LIMIT 200",
            (session_id, after),
        ).fetchall()
        return [
            {
                "event_id": r["event_id"],
                "event_type": r["event_type"],
                "payload": json_load(r["payload_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]


@classroom_tutor_router.get("/api/sessions/{session_id}/training-status")
def training_status(session_id: str, teacher_token: str):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
    return TRAINING_LEDGER.session_status(session_id)


@classroom_tutor_router.get("/api/training-batches")
def training_batches(status: str | None = None, _: None = Depends(require_owner)):
    allowed = {None, "EMPTY", "REVIEW_PENDING", "READY_FOR_EXPORT"}
    if status not in allowed:
        raise HTTPException(400, "unknown training batch status")
    return {
        "batches": TRAINING_LEDGER.list_lesson_batches(status),
        "automatic_gpu_rental": False,
        "policy": "ONE_OFFLINE_BATCH_PER_STOPPED_LESSON",
    }


@classroom_tutor_router.get("/api/sessions/{session_id}/training-review-candidates")
def training_review_candidates(session_id: str, teacher_token: str):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
        responses = conn.execute(
            """SELECT response_id,student_alias,segment_id,answer,confidence,reason_text,created_at
               FROM responses WHERE session_id=? ORDER BY created_at DESC LIMIT 100""",
            (session_id,),
        ).fetchall()
        snapshots = {r["response_id"]: json_load(r["segment_json"]) for r in conn.execute(
            "SELECT snapshot.* FROM response_material_snapshots snapshot JOIN responses r ON r.response_id=snapshot.response_id WHERE r.session_id=?",
            (session_id,),
        ).fetchall()}
    candidates = []
    for response in responses:
        item = dict(response)
        segment = snapshots.get(item["response_id"], {})
        correct_answer = segment.get("correct_answer")
        item.update({
            "segment_title": segment.get("title"),
            "segment_kind": segment.get("kind"),
            "concept_key": segment.get("concept_key") or "UNSPECIFIED",
            "content_revision_id": segment.get("content_revision_id") or f"{session_id}:{item['segment_id']}:v1",
            "correct": None if correct_answer is None else item["answer"] == correct_answer,
            "independent_followup": bool(segment.get("independent_followup", False)),
            "snapshot_available": bool(segment),
        })
        candidates.append(item)
    return {"candidates": candidates}


@classroom_tutor_router.post("/api/sessions/{session_id}/training-review")
def training_review(
    session_id: str,
    req: TrainingReviewRequest,
    teacher_token: str,
):
    with db() as conn:
        session = get_session_or_404(conn, session_id)
        require_teacher(session, teacher_token)
        response = conn.execute(
            "SELECT * FROM responses WHERE session_id=? AND response_id=?",
            (session_id, req.response_id),
        ).fetchone()
        if not response:
            raise HTTPException(404, "response not found")
        snapshot = conn.execute("SELECT segment_json FROM response_material_snapshots WHERE response_id=?",
                                (response["response_id"],)).fetchone()
        if not snapshot:
            raise HTTPException(409, "response segment revision is unavailable")
        segment = json_load(snapshot["segment_json"])
        correct_answer = segment.get("correct_answer")
        correct = None if correct_answer is None else response["answer"] == correct_answer
    try:
        result = TRAINING_LEDGER.review_response(
            session_id=session_id,
            student_alias=response["student_alias"],
            response_ref=response["response_id"],
            segment=segment,
            correct=correct,
            reason_present=bool((response["reason_text"] or "").strip()),
            confidence=response["confidence"],
            source_integrity_verified=req.source_integrity_verified,
            content_trust=req.content_trust,
            hint_or_explanation_confirmed=req.hint_or_explanation_confirmed,
            notes=req.notes,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    TRAINING_LEDGER.refresh_lesson_batch(session_id)
    return {**result, "training_status": TRAINING_LEDGER.session_status(session_id)}


@classroom_tutor_router.post("/api/sessions/{session_id}/training-content-defect")
def training_content_defect(
    session_id: str,
    req: ContentDefectRequest,
    teacher_token: str,
    _: None = Depends(require_owner),
):
    with db() as conn:
        session = get_session_or_404(conn, session_id)
        require_teacher(session, teacher_token)
    affected = TRAINING_LEDGER.mark_content_defect(
        session_id=session_id,
        content_revision_id=req.content_revision_id,
        notes=req.notes,
    )
    return {"ok": True, "affected_learner_concepts": affected, "successor_revisions_created": affected > 0}


@classroom_tutor_router.get("/api/training/status")
def owner_training_status(_: None = Depends(require_owner)):
    snapshot = TRAINING_LEDGER.dataset_snapshot()
    return {
        "connected_to_ai_tutor": True,
        "training_plane": "OWNER_CONTROLLED",
        "automatic_live_reinforcement": False,
        "actual_gpu_training_started": False,
        "pseudonym_salt_configured": TRAINING_LEDGER.pseudonym_salt_configured,
        "dataset_snapshot": snapshot,
        "promotion_gate": "FAIL_CLOSED",
        "training_blockers": (["NO_ELIGIBLE_EVIDENCE"] if not snapshot["record_count"] else []) + [
            "MATERIALIZED_REVIEWED_TRAINING_DATA_REQUIRED", "GPU_TRAINING_JOB_NOT_CONNECTED"],
        "available_training_runner": "SFT_LORA_CHECK_ONLY_SUPPORTED",
        "reinforcement_learning_worker_connected": False,
    }


@classroom_tutor_router.get("/api/training/dataset-snapshot")
def owner_training_snapshot(_: None = Depends(require_owner)):
    return TRAINING_LEDGER.dataset_snapshot()


@classroom_tutor_router.post("/api/training/actual-use-receipt")
def owner_actual_training_use(
    req: ActualTrainingUseRequest,
    _: None = Depends(require_owner),
):
    try:
        return TRAINING_LEDGER.create_actual_use_receipt(
            eligibility_revision_id=req.eligibility_revision_id,
            training_job_id=req.training_job_id,
            tutor_model_version=req.tutor_model_version,
            sample_hash=req.sample_hash,
        )
    except ValueError as exc:
        raise HTTPException(423, str(exc)) from exc


COMMON_CSS = """
:root{--bg:#0b1020;--panel:#111827;--card:#172033;--line:#2b3955;--text:#f8fafc;--muted:#a5b4c7;--accent:#38bdf8;--ok:#34d399;--warn:#fbbf24;--bad:#fb7185}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,'Noto Sans KR',sans-serif}
button,input,select,textarea{font:inherit} button{cursor:pointer}
.top{display:flex;gap:12px;align-items:center;justify-content:space-between;padding:12px 18px;background:#0f172a;border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
.badge{display:inline-flex;padding:5px 9px;border:1px solid var(--line);border-radius:999px;font-size:13px;color:#dbeafe}
.grid{display:grid;grid-template-columns:1fr 340px;min-height:calc(100vh - 58px)}
.stage{padding:20px;min-width:0}.side{border-left:1px solid var(--line);background:#0f172a;padding:14px;overflow:auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:12px}
.big{font-size:clamp(26px,4vw,54px);line-height:1.22}.muted{color:var(--muted)}
.controls{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}.controls button,.btn{border:1px solid var(--line);border-radius:12px;padding:12px;background:#1e293b;color:white}
.controls button.primary,.btn.primary{background:#0369a1}.controls button.danger{background:#881337}
.choice{display:block;width:100%;text-align:left;padding:14px;margin:8px 0;background:#1e293b;color:#fff;border:1px solid #334155;border-radius:12px}
.choice:hover{border-color:var(--accent)}
video{width:100%;max-height:55vh;background:#000;border-radius:12px}
canvas.board{width:100%;height:55vh;background:white;border-radius:14px;touch-action:none;display:block}
input,select,textarea{width:100%;background:#0f172a;color:white;border:1px solid var(--line);border-radius:10px;padding:10px;margin:5px 0}
#qr canvas,#qr img{background:#fff;padding:8px;border-radius:8px;max-width:220px}
.bar{height:12px;background:#273449;border-radius:10px;overflow:hidden}.bar>i{display:block;height:100%;background:var(--accent)}
@media(max-width:900px){.grid{grid-template-columns:1fr}.side{border-left:none;border-top:1px solid var(--line)}.top{position:static}}
"""

COMMON_JS = r"""
function esc(s){return String(s??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
async function api(url,opt={}){let r=await fetch(url,opt); if(!r.ok)throw new Error(await r.text()); return r.json();}
function q(sel){return document.querySelector(sel)}
function capabilities(){return {touch:navigator.maxTouchPoints>0,pointer:'PointerEvent' in window,fullscreen:!!document.documentElement.requestFullscreen,speech:'speechSynthesis' in window,screen:`${screen.width}x${screen.height}`,ua:navigator.userAgent};}
function speak(text){if(!('speechSynthesis' in window))return; speechSynthesis.cancel(); let u=new SpeechSynthesisUtterance(text);u.lang='ko-KR';u.rate=.95;speechSynthesis.speak(u);}
function youtubeEmbed(url){try{let u=new URL(url);if(u.hostname==='youtu.be')return 'https://www.youtube-nocookie.com/embed/'+encodeURIComponent(u.pathname.slice(1));if(u.hostname==='youtube.com'||u.hostname.endsWith('.youtube.com')){let id=u.searchParams.get('v');if(id)return 'https://www.youtube-nocookie.com/embed/'+encodeURIComponent(id);}}catch(e){}return null;}
function teacherMediaUrl(raw,sessionId,teacherToken){
 try{const media=new URL(raw,location.origin),prefix=`/tutor/api/sessions/${sessionId}/media/`;
   if(media.origin===location.origin&&media.pathname.startsWith(prefix)&&
      /^[0-9a-f]{32}$/.test(media.pathname.slice(prefix.length))){
     media.searchParams.set('teacher_token',teacherToken);return media.href;
   }
 }catch(e){}return raw;
}
function materialStructure(x){
 let out='';
 if(x.learning_map)out+=`<div style='display:flex;gap:12px;flex-wrap:wrap'>${x.learning_map.map(c=>`<div class='card'><small>${esc(({BASIC:'기초',CONDITION:'성립조건',INFERENCE:'추론',ADVANCED:'심화'})[c.stage]||c.stage)}</small><p>${esc(c.label)}</p></div>`).join('<span aria-hidden="true">→</span>')}</div>`;
 if(x.conditions)out+=`<p>${Object.entries(x.conditions).map(([k,v])=>`<span class='badge'>${esc(k)}: ${esc(v)}</span>`).join(' ')}</p>`;
 if(x.logic_links)out+=x.logic_links.map(l=>`<details class='card'><summary>${esc(l.premise)} → ${esc(l.conclusion)}</summary><p>적용조건: ${esc(l.condition_keys.join(', '))}</p><p>규칙: ${esc(l.rule)}</p></details>`).join('');
 if(x.comparison){let c=x.comparison;out+=`<table style='width:100%;text-align:left'><thead><tr><th>조건</th><th>기준</th><th>변경</th></tr></thead><tbody>${Object.keys(c.baseline).map(k=>`<tr style='color:${c.changed_keys.includes(k)?'var(--warn)':'inherit'}'><td>${esc(k)}</td><td>${esc(c.baseline[k])}</td><td>${esc(c.altered[k])}</td></tr>`).join('')}</tbody></table>`;}
 if(x.kind==='DEMONSTRATE'&&['x','x³'].includes(x.conditions?.['함수'])){
  const cube=x.conditions['함수']==='x³',path=Array.from({length:101},(_,i)=>{const v=(i-50)/50;return `${i?'L':'M'}${30+i*3},${190-150*(cube?Math.abs(v)**3:Math.abs(v))}`;}).join(' ');
  out+=`<svg viewBox='0 0 360 215' role='img' aria-label='${cube?'절댓값 x 세제곱':'절댓값 x'} 그래프' style='max-width:500px;width:100%;background:white;border-radius:12px'><path d='M20 190H345 M180 10V205' stroke='#64748b'/><path d='${path}' fill='none' stroke='#0369a1' stroke-width='4'/><text x='190' y='210' font-size='14'>0</text></svg>`;
 }
 return out;
}
"""

BOARD_SYNC_JS = r"""
function setupSharedBoard(canvas){
 const context=canvas.getContext('2d');let strokes=[],active=null,signature='',saving=false;
 function size(){const d=window.devicePixelRatio||1,r=canvas.getBoundingClientRect();
   if(!r.width||!r.height)return;canvas.width=Math.round(r.width*d);canvas.height=Math.round(r.height*d);
   context.setTransform(d,0,0,d,0,0);context.strokeStyle='#111';context.lineWidth=3;context.lineCap='round';redraw();}
 function paint(points){if(points.length<2)return;context.beginPath();
   points.forEach(([x,y],i)=>i?context.lineTo(x*canvas.clientWidth,y*canvas.clientHeight):context.moveTo(x*canvas.clientWidth,y*canvas.clientHeight));
   context.stroke();}
 function redraw(){context.clearRect(0,0,canvas.clientWidth,canvas.clientHeight);
   strokes.forEach(x=>paint(x.points));if(active)paint(active.points);}
 function coordinate(e){let r=canvas.getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),
   Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))];}
 canvas.addEventListener('pointerdown',e=>{if(saving)return;canvas.setPointerCapture(e.pointerId);
   active={stroke_id:crypto.randomUUID(),points:[coordinate(e)]};});
 canvas.addEventListener('pointermove',e=>{if(!active)return;if(active.points.length>=1500)return;
   active.points.push(coordinate(e));redraw();});
 async function finish(e){if(!active)return;let stroke=active;active=null;
   if(stroke.points.length<2)stroke.points.push(coordinate(e));
   saving=true;try{await api('/tutor/api/sessions/'+SID+'/board-strokes?teacher_token='+encodeURIComponent(TT),
     {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(stroke)});
     strokes.push(stroke);signature=JSON.stringify(strokes);}
   catch(error){alert('판서를 저장하지 못했습니다. 연결을 확인해 주세요.');}
   finally{saving=false;redraw();}}
 canvas.addEventListener('pointerup',finish);canvas.addEventListener('pointercancel',finish);
 async function sync(){if(saving||active)return;try{let result=await api('/tutor/api/sessions/'+SID+
   '/board-strokes?teacher_token='+encodeURIComponent(TT));let next=JSON.stringify(result.strokes);
   if(next!==signature){strokes=result.strokes;signature=next;redraw();}}catch(error){}}
 window.addEventListener('resize',size);setTimeout(size,100);sync();setInterval(sync,1200);
 return {size,clear:async()=>{await api('/tutor/api/sessions/'+SID+'/board-strokes?teacher_token='+encodeURIComponent(TT),
   {method:'DELETE'});strokes=[];signature='';redraw();}};
}
"""


def board_html(
    session_id: str,
    teacher_token: str,
    product_prefix: str = "/tutor",
) -> str:
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no'><title>EduRecover Board</title><style>{COMMON_CSS}</style></head>
<body><div class='top'><div><b>EduRecover 전자칠판</b> <span class='badge' id='conn'>연결중</span></div><div><span class='badge' id='mode'></span> <button class='btn' onclick='goFull()'>전체화면</button></div></div>
<div class='grid'><main class='stage'><div class='card'><div class='muted' id='kind'></div><div class='big' id='title'>불러오는 중...</div><p id='text' style='font-size:clamp(18px,2.1vw,32px)'></p><div id='structure'></div><div id='media'></div><p id='playbackHelp' class='muted' role='status'></p><button class='btn' id='boardPlayFallback' style='display:none' onclick='resumeBoardPlayback()'>이 화면에서 자료 재생</button><div id='choices'></div></div><div class='card' id='whiteWrap' style='display:none'><canvas id='whiteboard' class='board'></canvas></div></main>
<aside class='side'><div class='card'><b>교사 제어</b><div class='controls'><button class='primary' onclick="cmd('PLAY')">재생/TTS</button><button onclick="cmd('PAUSE')">일시정지</button><button onclick="cmd('PREV')">이전</button><button onclick="cmd('NEXT')">다음</button><button onclick='toggleBoard()'>판서 전환</button><button class='danger' onclick="cmd('STOP')">전체중지</button><button onclick='clearSharedBoard()'>판서 지우기</button></div></div>
<div class='card'><b>학생 참가</b><div id='qr' style='margin-top:10px'></div><div class='muted' id='joinText'></div></div>
<div class='card'><b>응답 현황</b><div id='results'>아직 응답 없음</div></div>
<div class='card'><b>장치 capability</b><pre id='caps' style='white-space:pre-wrap'></pre></div></aside></div>
<script src='https://cdn.jsdelivr.net/npm/qrcodejs@1.0.0/qrcode.min.js'></script><script>{COMMON_JS}{BOARD_SYNC_JS}
const SID={json.dumps(session_id)}, TT={json.dumps(teacher_token)}, PFX={json.dumps(product_prefix)};let S=null,lastEvent=0,videoPausedOnce=false,lastRenderKey="",lastPlayback="",lastPlayRevision=0;
let youtubePlayer=null,youtubeReady=false,youtubeEpoch=0,youtubePauseTimer=null,youtubeApiPromise=null;
function resetYouTube(){{youtubeEpoch++;if(youtubePauseTimer)clearInterval(youtubePauseTimer);youtubePauseTimer=null;
 if(youtubePlayer){{try{{youtubePlayer.destroy();}}catch(e){{}}}}youtubePlayer=null;youtubeReady=false;}}
function loadYouTubeApi(){{if(window.YT?.Player)return Promise.resolve(window.YT);
 if(youtubeApiPromise)return youtubeApiPromise;
 youtubeApiPromise=new Promise((resolve,reject)=>{{const previous=window.onYouTubeIframeAPIReady;
   window.onYouTubeIframeAPIReady=()=>{{if(typeof previous==='function')previous();resolve(window.YT);}};
   const script=document.createElement('script');script.src='https://www.youtube.com/iframe_api';
   script.onerror=()=>{{youtubeApiPromise=null;reject(new Error('YouTube API unavailable'));}};
   document.head.appendChild(script);}});return youtubeApiPromise;}}
function playbackHelp(message,allowManual=false){{q('#playbackHelp').textContent=message;q('#boardPlayFallback').style.display=allowManual?'inline-block':'none';}}
function resumeBoardPlayback(){{if(S?.state?.playback!=='PLAYING')return;let v=q('#vid');
 if(v){{Promise.resolve(v.play()).then(()=>{{if(v===q('#vid')&&S?.state?.playback==='PLAYING')playbackHelp('');}})
   .catch(()=>{{if(v===q('#vid')&&S?.state?.playback==='PLAYING')
     playbackHelp('브라우저가 자료 재생을 제한했습니다. 화면의 재생 버튼을 눌러 주세요.',true);}});return;}}
 if(youtubeReady){{youtubePlayer.playVideo();playbackHelp('');}}}}
function applyPlayback(){{let x=seg();if(!S||!x)return;let playing=S.state.playback==='PLAYING',v=q('#vid');
 if(v){{if(playing)Promise.resolve(v.play()).then(()=>{{if(v===q('#vid')&&S?.state?.playback==='PLAYING')playbackHelp('');}})
   .catch(()=>{{if(v===q('#vid')&&S?.state?.playback==='PLAYING')
     playbackHelp('브라우저가 자료 재생을 제한했습니다. 화면의 재생 버튼을 눌러 주세요.',true);}});
   else{{v.pause();playbackHelp('');}}}}
 else if(q('#youtubePlayer')){{if(!youtubeReady)return;
   if(playing)youtubePlayer.playVideo();else if(S.state.playback==='STOPPED')youtubePlayer.stopVideo();else youtubePlayer.pauseVideo();
   if(!playing)playbackHelp('');}}
 else if(playing){{if(x.audio_text||x.text)speak(x.audio_text||x.text);}}
 if(!playing&&'speechSynthesis' in window)speechSynthesis.cancel();}}
function setupYouTubePlayer(embed,x){{let epoch=++youtubeEpoch,url=new URL(embed);
 url.searchParams.set('enablejsapi','1');url.searchParams.set('origin',location.origin);
 q('#media').innerHTML=`<iframe id='youtubePlayer' title='수업 영상' src="${{esc(url.toString())}}" style='width:100%;height:min(56vw,520px);border:0;border-radius:12px' allow='autoplay; encrypted-media; picture-in-picture' allowfullscreen></iframe>`;
 loadYouTubeApi().then(YT=>{{if(epoch!==youtubeEpoch||!q('#youtubePlayer'))return;
   youtubePlayer=new YT.Player(q('#youtubePlayer'),{{events:{{onReady:()=>{{if(epoch!==youtubeEpoch)return;youtubeReady=true;applyPlayback();
     youtubePauseTimer=setInterval(()=>{{if(epoch!==youtubeEpoch||!youtubeReady||videoPausedOnce||
       S?.state?.playback!=='PLAYING'||!x.pause_at_sec)return;
       if(youtubePlayer.getCurrentTime()>=Number(x.pause_at_sec)){{videoPausedOnce=true;youtubePlayer.pauseVideo();
         q('#choices').innerHTML=`<div class='card'><b>생각해 볼 질문</b><p>${{esc(x.text)}}</p></div>`;}}}},250);}},
     onAutoplayBlocked:()=>{{if(epoch===youtubeEpoch&&S?.state?.playback==='PLAYING')
       playbackHelp('브라우저가 자동 재생을 제한했습니다. 전자칠판에서 영상 재생을 눌러 주세요.',true);}}}}}});
 }}).catch(()=>{{if(epoch!==youtubeEpoch)return;resetYouTube();q('#media').innerHTML='';
   playbackHelp('YouTube 영상 제어를 불러오지 못했습니다. 연결을 확인해 주세요.');}});}}
async function refresh(){{try{{S=await api(`/tutor/api/sessions/${{SID}}?teacher_token=${{encodeURIComponent(TT)}}&display_only=true`);q('#conn').textContent='연결됨';q('#mode').textContent=S.launch_mode+' / '+S.connection_method;let visible=S.state.board_mode==='WHITEBOARD';if(q('#whiteWrap').style.display!==(visible?'block':'none')){{q('#whiteWrap').style.display=visible?'block':'none';if(visible)sharedBoard.size();}}render();await loadResults();}}catch(e){{S=null;lastRenderKey='';lastPlayback='';lastPlayRevision=0;resetYouTube();playbackHelp('');if('speechSynthesis' in window)speechSynthesis.cancel();q('#vid')?.pause();q('#media').innerHTML='';q('#structure').innerHTML='';q('#choices').innerHTML='';q('#title').textContent='자료 제공 대기';q('#text').textContent='교사 검증 또는 연결 상태를 확인해 주세요.';q('#conn').textContent='대기';}}}}
function seg(){{return (S?.lesson||[]).find(x=>x.segment_id===S?.state?.current_segment_id)||null}}
function render(){{let x=seg();if(!x)return;let key=x.segment_id+':'+(x.structured_bundle_revision||'');let changed=key!==lastRenderKey;
 if(changed){{resetYouTube();playbackHelp('');lastRenderKey=key;q('#structure').innerHTML=materialStructure(x);
 q('#kind').textContent=x.kind+(x.misconception_path?' · 오답원인: '+x.misconception_path:'');
 q('#title').textContent=x.title;q('#text').textContent=x.text||'';let m=q('#media');m.innerHTML='';videoPausedOnce=false;
 if(x.media_url){{let yt=youtubeEmbed(x.media_url),url=esc(teacherMediaUrl(x.media_url,SID,TT));
   if(x.media_type?.startsWith('image/'))m.innerHTML=`<img alt='수업 자료' src="${{url}}" style='max-width:100%;max-height:55vh'>`;
   else if(x.media_type?.startsWith('audio/'))m.innerHTML=`<audio id='vid' controls src="${{url}}"></audio>`;
   else if(yt)setupYouTubePlayer(yt,x);
   else m.innerHTML=`<video id='vid' controls playsinline src="${{url}}"></video>`;
   let v=q('#vid');if(v&&x.pause_at_sec)v.addEventListener('timeupdate',()=>{{if(!videoPausedOnce&&v.currentTime>=x.pause_at_sec){{videoPausedOnce=true;v.pause();q('#choices').innerHTML=`<div class='card'><b>생각해 볼 질문</b><p>${{esc(x.text)}}</p></div>`;}}}});}}
let c=q('#choices');c.innerHTML='';if(['SOCRATIC','RETEST'].includes(x.kind)){{c.innerHTML=`<div class='muted'>학생은 자신의 기기에서 응답합니다.</div>`;}}
}}let playRevision=Number(S.state.play_command_revision||0);
 if(changed||lastPlayback!==S.state.playback||playRevision!==lastPlayRevision){{
   lastPlayback=S.state.playback;lastPlayRevision=playRevision;applyPlayback();}}
}}
async function cmd(a){{await api(`/tutor/api/sessions/${{SID}}/command?teacher_token=${{encodeURIComponent(TT)}}`,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{action:a}})}});refresh();}}
async function loadResults(){{let x=seg();if(!x)return;if(x.independent_followup){{q('#results').textContent='독립 응답 중에는 다른 학생의 답을 표시하지 않습니다.';return;}}let r=await api(`/tutor/api/sessions/${{SID}}/results?teacher_token=${{encodeURIComponent(TT)}}&segment_id=${{encodeURIComponent(x.segment_id)}}`);let total=(r.totals[0]?.n)||0;if(!total){{q('#results').textContent='아직 응답 없음';return;}}q('#results').innerHTML=r.counts.map(z=>`<div style='margin:8px 0'><div>${{esc(z.answer)}} · ${{z.n}}명</div><div class='bar'><i style='width:${{Math.round(z.n/total*100)}}%'></i></div></div>`).join('');}}
function goFull(){{document.documentElement.requestFullscreen?.();}}
async function toggleBoard(){{await api(`/tutor/api/sessions/${{SID}}/command?teacher_token=${{encodeURIComponent(TT)}}`,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{action:'SET_BOARD_MODE',board_mode:S?.state.board_mode==='WHITEBOARD'?'LESSON':'WHITEBOARD'}})}});refresh();}}
async function clearSharedBoard(){{if(confirm('이 수업의 판서를 모두 지울까요?'))await sharedBoard.clear();}}
q('#caps').textContent=JSON.stringify(capabilities(),null,2);
const joinUrl=`${{location.origin}}/tutor/join/${{SID}}?token=${{encodeURIComponent(SID)}}`; // fallback text replaced after session fetch
async function makeQR(){{let s=await api(`/tutor/api/sessions/${{SID}}?teacher_token=${{encodeURIComponent(TT)}}`);let url=`${{location.origin}}${{PFX}}/join/${{SID}}?token=${{encodeURIComponent(s.join_token)}}`;q('#joinText').innerHTML=`참가 주소: <span class='muted'>${{esc(url)}}</span>`;try{{new QRCode(q('#qr'),{{text:url,width:190,height:190}});}}catch(e){{q('#qr').textContent='QR 라이브러리 로드 실패 — 위 주소 또는 참가코드를 사용하세요.';}}}}
const sharedBoard=setupSharedBoard(q('#whiteboard'));
makeQR();refresh();setInterval(refresh,1500);
</script></body></html>"""


def teacher_html(session_id: str, teacher_token: str) -> str:
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>EduRecover Teacher</title><style>{COMMON_CSS}</style></head><body>
<div class='top'><b>EduRecover 교사 콘솔</b><span class='badge'>{session_id}</span></div><main style='max-width:960px;margin:auto;padding:18px'><div class='card'><b>전자칠판 열기</b><p class='muted'>전자칠판 자체 브라우저/OPS에서는 아래 주소를 직접 열고, 캐스트/HDMI 환경에서는 이 화면을 전자칠판으로 미러링합니다.</p><a class='btn primary' style='display:inline-block;text-decoration:none' target='_blank' href='/tutor/board/{session_id}?teacher_token={teacher_token}'>전자칠판 화면 열기</a></div>
<div class='card'><b>수업자료 검토</b><p>문항·정답·설명을 확인한 뒤 학생에게 제공해 주세요.</p><button class='btn primary' onclick='publishLesson()'>검토한 수업자료 확정</button><div id='releaseStatus' role='status'></div></div><div class='card'><b>수업 단계</b><div id='segments'></div></div><div class='card'><b>실시간 응답</b><div id='res'></div></div>
<div class='card'><b>강화학습 연결 상태</b><p class='muted'>학생상태와 AI튜터 학습자격은 별도 객체입니다. 응답은 즉시 모델 가중치에 반영되지 않으며, 교사검증·독립 후속문항·출처무결성 게이트를 통과해야 학습후보가 됩니다.</p><div id='training'>불러오는 중...</div><div id='review'></div></div></main><script>{COMMON_JS}
const SID={json.dumps(session_id)},TT={json.dumps(teacher_token)};let S;
async function load(){{S=await api(`/tutor/api/sessions/${{SID}}?teacher_token=${{encodeURIComponent(TT)}}`);q('#segments').innerHTML=(S.lesson||[]).map(x=>`<button class='choice' data-segment='${{esc(x.segment_id)}}' onclick="go(this.dataset.segment)"><b>${{esc(x.title)}}</b><br><span class='muted'>${{esc(x.kind)}} · ${{esc(x.text)}}<br>선택지: ${{esc((x.choices||[]).join(' / '))}}<br>정답: ${{esc(x.correct_answer||'채점 없음')}}</span></button>`).join('');let r=await api(`/tutor/api/sessions/${{SID}}/results?teacher_token=${{encodeURIComponent(TT)}}`);q('#res').innerHTML=(r.counts.length?r.counts.map(x=>`<div>${{esc(x.segment_id)}} / ${{esc(x.answer)}} : ${{x.n}}명</div>`).join(''):'아직 응답 없음');await loadTraining();}}
async function go(id){{await api(`/tutor/api/sessions/${{SID}}/command?teacher_token=${{encodeURIComponent(TT)}}`,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{action:'GOTO',segment_id:id}})}});load();}}
async function loadTraining(){{let s=await api(`/tutor/api/sessions/${{SID}}/training-status?teacher_token=${{encodeURIComponent(TT)}}`);q('#training').innerHTML=`<div><span class='badge'>연결됨</span> 노출 ${{s.exposure_count}} · 증거 revision ${{s.evidence_revision_count}}</div><div class='muted'>ELIGIBLE ${{s.eligibility_counts.ELIGIBLE||0}} · HOLD ${{s.eligibility_counts.HOLD||0}} · INELIGIBLE ${{s.eligibility_counts.INELIGIBLE||0}} · 자동 가중치 갱신: 사용 안 함</div>`;let c=await api(`/tutor/api/sessions/${{SID}}/training-review-candidates?teacher_token=${{encodeURIComponent(TT)}}`);q('#review').innerHTML=(c.candidates||[]).slice(0,12).map(x=>`<div class='card' style='margin-top:10px'><b>${{esc(x.student_alias)}} · ${{esc(x.segment_title||x.segment_id)}}</b><div class='muted'>응답 ${{esc(x.answer)}} · 이유 ${{esc(x.reason_text||'없음')}} · ${{x.independent_followup?'독립 후속문항':'일반 응답'}}</div><label><input id='hint-${{x.response_id}}' type='checkbox' style='width:auto'> 이 응답 전 힌트·설명 제공을 실제로 확인함</label><button class='btn primary' onclick="reviewEvidence('${{x.response_id}}')">이 응답 교사검증</button></div>`).join('')||`<p class='muted'>검증할 응답이 없습니다.</p>`;}}
async function reviewEvidence(id){{let hint=q('#hint-'+id)?.checked===true;if(!confirm('이 응답의 근거와 해당 수업자료 revision을 교사가 확인했습니까?'))return;await api(`/tutor/api/sessions/${{SID}}/training-review?teacher_token=${{encodeURIComponent(TT)}}`,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{response_id:id,teacher_validated:true,source_integrity_verified:true,content_trust:'VERIFIED',hint_or_explanation_confirmed:hint}})}});await loadTraining();}}
load();setInterval(load,2000);{LEGACY_RELEASE_JS}</script></body></html>"""


def student_html(session_id: str, token: str) -> str:
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>EduRecover Student</title><style>{COMMON_CSS}</style></head><body>
<div class='top'><b>EduRecover 학생 응답</b><span class='badge'>{session_id}</span></div><main style='max-width:680px;margin:auto;padding:16px'><div class='card'><label>가명 또는 번호</label><input id='alias' placeholder='예: S12' maxlength='40'></div><div class='card'><div class='muted' id='kind'></div><h1 id='title'>수업 연결중...</h1><p id='text' style='font-size:20px'></p><div id='choices'></div><label>확신도(0~100, 선택)</label><input id='conf' type='number' min='0' max='100' placeholder='예: 70'><label>왜 그렇게 생각했는지(선택)</label><textarea id='reason' rows='3'></textarea><div id='msg' class='muted'></div></div></main><script>{COMMON_JS}
const SID={json.dumps(session_id)}, JT={json.dumps(token)};let S=null,lastSeg='';
function seg(){{return (S?.lesson||[]).find(x=>x.segment_id===S?.state?.current_segment_id)||null}}
async function load(){{try{{S=await api(`/tutor/api/sessions/${{SID}}?join_token=${{encodeURIComponent(JT)}}`);let x=seg();
 if(S.state.playback==='STOPPED'||!x){{lastSeg='';q('#kind').textContent='';q('#title').textContent='수업 중지';q('#text').textContent='';q('#choices').innerHTML='';q('#msg').textContent='교사가 수업을 다시 시작하면 응답할 수 있습니다.';return;}}
 q('#kind').textContent=x.kind;q('#title').textContent=x.title;q('#text').textContent=x.kind==='VIDEO'?'전자칠판 설명을 보고 질문이 나오면 답하세요.':x.text;if(lastSeg!==x.segment_id+':'+(x.structured_bundle_revision||'')){{lastSeg=x.segment_id+':'+(x.structured_bundle_revision||'');q('#msg').textContent='';q('#choices').innerHTML=(x.choices||[]).map(c=>`<button class='choice' data-choice='${{esc(c)}}' onclick='send(this.dataset.choice)'>${{esc(c)}}</button>`).join('')||`<div class='muted'>현재는 응답 단계가 아닙니다.</div>`;}}}}catch(e){{S=null;lastSeg='';q('#title').textContent='자료 제공 대기';q('#text').textContent='';q('#choices').innerHTML='';q('#msg').textContent='교사 검증 또는 연결 상태를 확인 중입니다.';}}}}
async function send(ans){{let a=q('#alias').value.trim();if(!a){{q('#msg').textContent='가명/번호를 먼저 입력하세요.';return;}}let x=seg();let body={{student_alias:a,segment_id:x.segment_id,answer:ans,join_token:JT,material_revision_id:x.structured_bundle_revision||null,learning_path_revision_id:x.learning_path_revision||null,confidence:q('#conf').value?Number(q('#conf').value):null,reason_text:q('#reason').value||null}};try{{let r=await api(`/tutor/api/sessions/${{SID}}/respond`,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(body)}});q('#msg').textContent='응답을 보냈습니다.'+(r.correct===true?' ✓':r.correct===false?' — 다시 생각해 보세요.':'');}}catch(e){{q('#msg').textContent='응답 전송 실패';}}}}
load();setInterval(load,1500);</script></body></html>"""


PRODUCT_HTML_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
BOARD_HTML_HEADERS = {**PRODUCT_HTML_HEADERS, "Referrer-Policy": "origin"}


def teacher_product_html(session_id: str, teacher_token: str) -> str:
    """Teacher console for the standalone product, without owner training controls."""
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>EduRecover Classroom Board 교사 콘솔</title><style>{COMMON_CSS}</style></head><body>
<div class='top'><div><b>EduRecover Classroom Board</b> <span class='badge'>교사 콘솔</span></div><span class='badge'>{session_id}</span></div>
<main style='max-width:980px;margin:auto;padding:18px'>
<div class='card'><h2 style='margin-top:0'>전자칠판 수업 운영</h2><p class='muted'>전자칠판 자체 브라우저나 OPS에서는 전자칠판 화면을 직접 열고, HDMI·USB-C·무선 캐스트 환경에서는 해당 화면을 미러링합니다.</p><a class='btn primary' style='display:inline-block;text-decoration:none' target='_blank' href='/classroom/board/{session_id}?teacher_token={teacher_token}'>전자칠판 화면 열기</a> <a class='btn' style='display:inline-block;text-decoration:none' href='/classroom/learning-path/{session_id}?teacher_token={teacher_token}'>거시·미시 학습경로 편집</a></div>
<div class='grid' style='grid-template-columns:1fr 1fr;min-height:auto'>
<div class='card'><b>수업자료 검토</b><p>문항·정답·설명을 확인한 뒤 학생에게 제공해 주세요.</p><button class='btn primary' onclick='publishLesson()'>검토한 수업자료 확정</button><div id='releaseStatus' role='status'></div></div><div class='card'><b>수업 단계</b><div id='segments' style='margin-top:10px'></div></div>
<div class='card'><b>교사 전자칠판 제어</b><p>재생·단계 이동과 판서를 칠판 화면에 함께 반영합니다.</p><div class='controls'><button onclick="control('PLAY')">재생/TTS</button><button onclick="control('PAUSE')">일시정지</button><button onclick="control('PREV')">이전</button><button onclick="control('NEXT')">다음</button><button onclick="control('SET_BOARD_MODE','WHITEBOARD')">판서 표시</button><button onclick="control('SET_BOARD_MODE','LESSON')">수업 표시</button><button onclick="control('STOP')">중지</button><button onclick='clearTeacherBoard()'>판서 지우기</button></div><canvas id='whiteboard' class='board' aria-label='교사 판서 영역'></canvas></div>
<div class='card'><b>실시간 학생 응답</b><div id='res' class='muted' style='margin-top:10px'>응답 대기 중</div></div>
</div>
<div class='card'><b>개인정보 원칙</b><p class='muted'>학생 실명 대신 가명 또는 번호만 사용합니다. 교사 화면에는 모델학습 관리나 내부 운영정보를 표시하지 않습니다.</p></div>
</main><script>{COMMON_JS}{BOARD_SYNC_JS}
const SID={json.dumps(session_id)},TT={json.dumps(teacher_token)};let S,lastPreviewDigest='';
const teacherBoard=setupSharedBoard(q('#whiteboard'));
async function control(action,board_mode){{try{{await api(`/tutor/api/sessions/${{SID}}/command?teacher_token=${{encodeURIComponent(TT)}}`,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{action,board_mode}})}});await load();}}catch(e){{alert('수업 조작 실패: '+e.message);}}}}
async function clearTeacherBoard(){{if(confirm('이 수업의 판서를 모두 지울까요?'))await teacherBoard.clear();}}
function previewMedia(x){{if(!x.media_url)return '';const url=esc(teacherMediaUrl(x.media_url,SID,TT)),yt=youtubeEmbed(x.media_url);
  if(x.media_type?.startsWith('image/'))return `<img alt="검토할 수업 그림" src="${{url}}" style="max-width:100%;max-height:260px">`;
  if(x.media_type?.startsWith('audio/'))return `<audio controls src="${{url}}"></audio>`;
  if(yt)return `<iframe title="검토할 수업 영상" src="${{esc(yt)}}" style="width:100%;height:220px;border:0" allowfullscreen></iframe>`;
  return `<video controls playsinline src="${{url}}" style="width:100%;max-height:260px"></video>`;
}}
async function load(){{
  try{{
    const s=await api(`/tutor/api/sessions/${{SID}}?teacher_token=${{encodeURIComponent(TT)}}`);S=s;
    if(s.lesson_digest!==lastPreviewDigest){{
      q('#segments').innerHTML=(s.lesson||[]).map(x=>`<div class='card'><button class='choice' data-segment='${{esc(x.segment_id)}}' onclick="go(this.dataset.segment)"><b>${{esc(x.title)}}</b><br><span class='muted'>${{esc(x.kind)}} · ${{esc(x.text)}}<br>선택지: ${{esc((x.choices||[]).join(' / '))}}<br>정답: ${{esc(x.correct_answer||'채점 없음')}}</span></button>${{previewMedia(x)}}</div>`).join('')||'<p class="muted">수업 단계가 없습니다.</p>';
      lastPreviewDigest=s.lesson_digest;
    }}
    const r=await api(`/tutor/api/sessions/${{SID}}/results?teacher_token=${{encodeURIComponent(TT)}}`);
    q('#res').innerHTML=(r.counts||[]).length?r.counts.map(x=>`<div style='margin:8px 0'>${{esc(x.segment_id)}} / ${{esc(x.answer)}} · ${{x.n}}명</div>`).join(''):'아직 응답 없음';
  }}catch(e){{q('#res').textContent='연결을 확인해 주세요.';}}
}}
async function go(id){{await api(`/tutor/api/sessions/${{SID}}/command?teacher_token=${{encodeURIComponent(TT)}}`,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{action:'GOTO',segment_id:id}})}});load();}}
load();setInterval(load,2000);
{LEGACY_RELEASE_JS}</script></body></html>"""


@classroom_tutor_router.get("/board/{session_id}", response_class=HTMLResponse)
def board(session_id: str, teacher_token: str):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
    return HTMLResponse(board_html(session_id, teacher_token), headers=BOARD_HTML_HEADERS)


@classroom_tutor_router.get("/teacher/{session_id}", response_class=HTMLResponse)
def teacher(session_id: str, teacher_token: str):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
    return HTMLResponse(teacher_html(session_id, teacher_token), headers=PRODUCT_HTML_HEADERS)


@classroom_tutor_router.get("/join/{session_id}", response_class=HTMLResponse)
def join(session_id: str, token: str):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        if token != row["join_token"]:
            raise HTTPException(403, "invalid join token")
    return HTMLResponse(student_html(session_id, token), headers=PRODUCT_HTML_HEADERS)


@teacher_classroom_router.get("/board/{session_id}", response_class=HTMLResponse)
def teacher_product_board(session_id: str, teacher_token: str):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
    return HTMLResponse(
        board_html(session_id, teacher_token, product_prefix="/classroom"),
        headers=BOARD_HTML_HEADERS,
    )


@teacher_classroom_router.get("/teacher/{session_id}", response_class=HTMLResponse)
def teacher_product_console(session_id: str, teacher_token: str):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
    return HTMLResponse(
        teacher_product_html(session_id, teacher_token),
        headers=BOARD_HTML_HEADERS,
    )


@teacher_classroom_router.get("/learning-path/{session_id}", response_class=HTMLResponse)
def learning_path_editor_page(session_id: str, teacher_token: str,
                              account: str = Depends(require_classroom_user)):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        _require_learning_path_teacher(row, teacher_token, account)
    return HTMLResponse(learning_path_editor_html(session_id, teacher_token), headers=BOARD_HTML_HEADERS)


@teacher_classroom_router.get("/join/{session_id}", response_class=HTMLResponse)
def teacher_product_join(session_id: str, token: str):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        if not secrets.compare_digest(token, row["join_token"]):
            raise HTTPException(403, "invalid join token")
    return HTMLResponse(student_html(session_id, token), headers=PRODUCT_HTML_HEADERS)


HOME_HTML = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>EduRecover AI 튜터</title><style>{COMMON_CSS}</style></head><body>
<div class='top'><div><b>EduRecover 구조화 AI 교수자료·시청각 수업</b> <span class='badge'>v{APP_VERSION}</span></div><a class='btn' href='/owner' style='text-decoration:none'>운영 플랫폼으로</a></div>
<main style='max-width:980px;margin:auto;padding:18px'>
<div class='card'><h2>새 튜터 수업 만들기</h2><p class='muted'>전체 학습지도 → 기초 설명·시범 → 성립조건·추론 → 심화 적용 순서로 연결합니다. 산파식 질문은 선택사항입니다.</p>
<label>수업 제목</label><input id='title' value='기초에서 심화로 연결하는 수업'><label>교실 가명</label><input id='room' value='CLASS-A'>
<div style='display:grid;grid-template-columns:1fr 1fr;gap:10px'><div><label>전자칠판 실행 방식</label><select id='launch'><option>DIRECT_BOARD_WEB</option><option>WINDOWS_OPS_WEB</option><option>CAST</option><option>WIRED_MIRROR</option><option>TEACHER_DEVICE</option></select></div><div><label>연결 방식</label><select id='conn'><option>BROWSER</option><option>MIRACAST</option><option>AIRPLAY</option><option>GOOGLE_CAST</option><option>HDMI</option><option>USB_C</option><option>VENDOR_CAST</option><option>OTHER</option></select></div></div></div>
<div class='card'><h2>문항·시청각 자료</h2>
<label>문항</label><textarea id='question' rows='3'>다음 조건을 자기 말로 다시 설명해 보세요.</textarea>
<label>선택지 — 한 줄에 하나</label><textarea id='choices' rows='4'>A. 첫 번째 선택지\nB. 두 번째 선택지\nC. 세 번째 선택지\nD. 네 번째 선택지</textarea>
<label>정답</label><input id='answer' value='B. 두 번째 선택지'>
<label>오답 원인</label><input id='misconception' value='문제에 없는 조건을 스스로 덧붙여 해석함'>
<label>짧은 설명·TTS 원고</label><textarea id='explanation' rows='3'>필요한 기초 개념과 적용조건을 설명하고 풀이를 시범 보입니다. 같은 원리를 새로운 문제에 적용할 때는 지원을 줄입니다.</textarea>
<label>영상 주소 — 선택사항</label><input id='videoUrl' placeholder='직접 재생 가능한 MP4·WebM 또는 YouTube 주소'><label>자동 멈춤 시점(초)</label><input id='pauseAt' type='number' min='0' step='0.5' value='8'>
<label>교사 시청각 파일 — 이미지·음성·영상(각 8MB 이하, 여러 개 선택)</label><input id='fileMedia' type='file' multiple accept='image/png,image/jpeg,audio/mpeg,audio/wav,video/mp4,video/webm'><p class='muted'>등록한 파일은 수업별 서버 저장소에 보관됩니다. 수업자료 확정 전 교사 콘솔에서 순서를 검토해 주세요.</p>
<label><input id='useSocratic' type='checkbox' style='width:auto'> 산파식 질문 사용(선택)</label><label>확인 질문</label><textarea id='socratic' rows='2'>문제에서 실제로 주어진 조건은 무엇이고, 내가 추가로 생각한 조건은 무엇인가요?</textarea>
<label>독립 적용용 새 문항 — 선택사항</label><textarea id='transferQuestion' rows='2'></textarea><label>새 문항 선택지 — 한 줄에 하나</label><textarea id='transferChoices' rows='3'></textarea><label>새 문항 정답</label><input id='transferAnswer'><button class='btn primary' onclick='createSession()'>AI 튜터·시청각 수업 만들기</button><div id='out'></div></div>
<div class='card'><b>개인정보 원칙</b><p class='muted'>학생 실명 대신 가명 또는 번호만 사용합니다. 전자칠판이 표시 전용이어도 학생은 참가 주소나 QR로 응답할 수 있습니다. 영상 주소를 비워 두면 브라우저의 한국어 음성합성으로 설명합니다.</p></div>
</main><script>{COMMON_JS}
async function createSession(){{
  const choices=q('#choices').value.split(/\\n+/).map(x=>x.trim()).filter(Boolean), answer=q('#answer').value.trim();
  const misconception=q('#misconception').value.trim(), explanation=q('#explanation').value.trim(), question=q('#question').value.trim(), socratic=q('#socratic').value.trim();
  let lesson=[
    {{segment_id:'problem',kind:'EXPLAIN',title:'문제와 조건 확인',text:question+'\\n'+choices.join(' | '),choices:choices,correct_answer:answer,misconception_path:misconception,concept_key:'OWNER:CURRENT_LESSON',content_revision_id:'owner-current:problem:v1',feedback_phase:'PRE_FEEDBACK'}},
    {{segment_id:'media',kind:'VIDEO',title:'오답원인 짧은 설명',text:explanation,media_url:q('#videoUrl').value.trim()||null,pause_at_sec:Number(q('#pauseAt').value||0)||null,misconception_path:misconception,concept_key:'OWNER:CURRENT_LESSON',content_revision_id:'owner-current:hint:v1',hint_level:1}},
    {{segment_id:'socratic',kind:'SOCRATIC',title:'스스로 확인하기',text:socratic,choices:['조건을 다시 확인했다','아직 헷갈린다'],correct_answer:'조건을 다시 확인했다',branch_map:{{'조건을 다시 확인했다':'retest','아직 헷갈린다':'media'}},misconception_path:misconception,concept_key:'OWNER:CURRENT_LESSON',content_revision_id:'owner-current:probe:v1',feedback_phase:'POST_FEEDBACK',tutor_action_content_revision:'owner-current:hint:v1'}},
    {{segment_id:'retest',kind:'RETEST',title:'전이 재시험',text:question,choices:choices,correct_answer:answer,branch_map:{{}},misconception_path:misconception,concept_key:'OWNER:CURRENT_LESSON',content_revision_id:'owner-current:transfer:v1',independent_followup:false,hint_level:1,tutor_action_content_revision:'owner-current:hint:v1'}}
  ];
  if(!q('#useSocratic').checked)lesson=lesson.filter(x=>x.kind!=='SOCRATIC');
  const transfer=q('#transferQuestion').value.trim(), transferChoices=q('#transferChoices').value.split(/\\n+/).map(x=>x.trim()).filter(Boolean), transferAnswer=q('#transferAnswer').value.trim();
  if((transfer||transferChoices.length||transferAnswer)&&!(transfer&&transfer!==question&&transferChoices.length>1&&transferChoices.includes(transferAnswer))){{q('#out').textContent='독립 적용에는 기존 문제와 다른 문항, 선택지 2개 이상, 선택지와 일치하는 정답을 입력해 주세요.';return;}}
  const last=lesson[lesson.length-1];last.kind='PRACTICE';last.title='설명 후 연습';
  if(transfer&&transfer!==question&&transferChoices.length>1&&transferChoices.includes(transferAnswer)){{Object.assign(last,{{kind:'RETEST',title:'새로운 문항에 독립 적용',text:transfer,choices:transferChoices,correct_answer:transferAnswer,independent_followup:true,hint_level:0,feedback_phase:'PRE_FEEDBACK'}});}}
  const body={{title:q('#title').value,room_alias:q('#room').value,launch_mode:q('#launch').value,connection_method:q('#conn').value,institution_type:'SCHOOL',privacy_mode:'ALIAS_ONLY',lesson}};
  q('#out').innerHTML="<p class='muted'>수업을 만드는 중입니다...</p>";
  try{{let r=await api('/tutor/api/sessions',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(body)}});
    const files=Array.from(q('#fileMedia').files||[]);for(const file of files){{
      if(file.size>8000000)throw new Error(file.name+' 파일이 8MB를 넘습니다.');
      const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
      const asset=await api('/tutor/api/sessions/'+r.session_id+'/media?teacher_token='+encodeURIComponent(r.teacher_token),
        {{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{media_type:file.type,data_base64:btoa(binary)}})}});
      lesson.splice(Math.max(0,lesson.length-1),0,{{segment_id:'uploaded-'+crypto.randomUUID(),kind:'DEMONSTRATE',title:file.name.slice(0,100),text:'교사가 검토한 시청각 자료',media_url:asset.media_url,media_type:asset.media_type,concept_key:'TEACHER_PRODUCT:CURRENT_LESSON',content_revision_id:'teacher-media:'+asset.sha256}});
    }}
    if(files.length)await api('/tutor/api/sessions/'+r.session_id+'/lesson?teacher_token='+encodeURIComponent(r.teacher_token),
      {{method:'PUT',headers:{{'content-type':'application/json'}},body:JSON.stringify({{lesson}})}});
    let join=location.origin+r.join_url;q('#out').innerHTML=`<div class='card' style='margin-top:12px'><a class='btn primary' target='_blank' href='${{r.board_url}}'>전자칠판 열기</a> <a class='btn' target='_blank' href='${{r.teacher_url}}'>교사 콘솔</a><p>먼저 교사 콘솔에서 내용을 검토하고 수업자료를 확정해 주세요.</p><p class='muted'>학생 참가: <a href='${{r.join_url}}' target='_blank'>${{esc(join)}}</a></p></div>`;}}catch(e){{q('#out').innerHTML=`<p style='color:#fb7185'>수업 또는 자료 등록 실패: ${{esc(e.message)}}</p>`;}}
}}
</script></body></html>"""


LEGACY_RELEASE_JS = r"""
async function publishLesson(){try{
 const s=S;if(!s){q('#releaseStatus').textContent='수업자료를 먼저 불러와 주세요.';return;}
 if(s.state.structured_materials){q('#releaseStatus').textContent='구조화 교수자료의 초안 검토 화면에서 확정해 주세요.';return;}
 if(!confirm('현재 수업의 모든 문항·정답·설명과 성립조건을 직접 검토했습니까?'))return;
 await api(`/tutor/api/sessions/${SID}/publish-lesson?teacher_token=${encodeURIComponent(TT)}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({expected_digest:s.lesson_digest,teacher_verified_content:true})});
 q('#releaseStatus').textContent='검토한 수업자료를 학생에게 제공할 수 있습니다.';
 }catch(e){q('#releaseStatus').textContent=e.message;}}
"""

MATERIAL_EDITOR_HTML = """
<section class='card'><h2>조건을 비교하는 구조화 교수자료</h2>
<p class='muted'>전체 학습지도, 성립조건, 그림·음성 설명, 연습문항을 함께 검토합니다. 초안은 학생에게 제공되지 않습니다.</p>
<button class='btn' onclick='loadMaterialExample()'>절댓값·미분가능성 예시 불러오기</button>
<div id='materialPreview'></div>
<div class='card'><b>조건별 시청각 자료</b><p class='muted'>등록할 조건 장면을 고르고 이미지·음성·영상 파일을 넣습니다. 자료를 실제로 재생해 확인한 뒤 수업을 확정해 주세요.</p>
<label>조건 장면<select id='materialScenario'></select></label>
<label>파일(각 8MB 이하)<input id='materialFiles' type='file' multiple accept='image/png,image/jpeg,audio/mpeg,audio/wav,video/mp4,video/webm'></label>
<button class='btn' onclick='attachMaterialMedia()'>파일을 선택한 장면에 등록</button><div id='materialMediaPreview'></div></div>
<details><summary>자료와 조건 편집</summary><textarea id='materialJson' rows='18' aria-label='교수자료 편집'></textarea></details>
<button class='btn' onclick='createMaterialDraft()'>초안 저장·검토</button>
<button class='btn primary' id='publishMaterial' onclick='reviewAndPublishMaterial()' disabled>내용 검토 완료·수업 확정</button>
<div id='materialMessage' role='status'></div></section>
"""

MATERIAL_EDITOR_SCRIPT = r"""
let materialSession=null,materialPreviewedSignature='';
function materialPreview(b){
 q('#materialPreview').innerHTML=materialStructure({learning_map:b.concepts,logic_links:b.links})+
 b.scenarios.map(s=>{const assets=b.representations.filter(x=>x.scenario_id===s.scenario_id);return `<article class='card'><h3>${esc(s.label)}</h3>${materialStructure({kind:'DEMONSTRATE',conditions:s.conditions})}${assets.map(a=>`<p><b>${esc(({SCENE:'설명 장면',AUDIO:'음성 원고',QUESTION:'연습문항',ANSWER:'정답',IMAGE:'그림',VIDEO:'영상',UPLOADED_AUDIO:'음성 파일',CAPTION:'자막'})[a.kind]||a.kind)}</b> ${esc(a.content)}</p>`).join('')}</article>`;}).join('')+
 `<div class='card'><b>논리 연결 검증 기록</b>${b.validations.map(v=>`<p>${esc(v.scenario_id)} / ${esc(v.link_id)}: ${esc(v.evidence)} <span class='muted'>${esc(v.status)}</span></p>`).join('')}</div>`+
 (b.independent_assessment?`<p><b>별도 독립문항</b> ${esc(b.independent_assessment.question)} / 정답 ${esc(b.independent_assessment.correct_answer)}</p>`:'');
 const scenarioSelect=q('#materialScenario'),current=scenarioSelect.value;
 scenarioSelect.innerHTML=b.scenarios.map(s=>`<option value="${esc(s.scenario_id)}">${esc(s.label)}</option>`).join('');
 if(b.scenarios.some(s=>s.scenario_id===current))scenarioSelect.value=current;
}
async function loadMaterialExample(){try{const b=await api('/tutor/api/structured-materials/example');q('#materialJson').value=JSON.stringify(b,null,2);materialPreview(b);q('#materialMessage').textContent='예시 초안입니다. 조건과 풀이 근거를 확인해 주세요.';}catch(e){q('#materialMessage').textContent=e.message;}}
async function ensureMaterialSession(){
 if(!materialSession)materialSession=await api('/tutor/api/sessions',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({title:q('#title').value,room_alias:q('#room').value,launch_mode:q('#launch').value,connection_method:q('#conn').value})});
 return materialSession;
}
async function showMaterialMediaPreviews(b){
 const refs=b.representations.filter(a=>['IMAGE','VIDEO','UPLOADED_AUDIO'].includes(a.kind));
 if(!refs.length){q('#materialMediaPreview').textContent='연결된 파일이 없습니다.';materialPreviewedSignature=JSON.stringify(b);return;}
 if(!materialSession)throw new Error('수업을 먼저 만든 뒤 매체를 검토해 주세요.');
 const library=await api(`/tutor/api/sessions/${materialSession.session_id}/media-library?teacher_token=${encodeURIComponent(materialSession.teacher_token)}`);
 const byId=new Map(library.assets.map(a=>[a.asset_id,a]));
 let html='';for(const ref of refs){const file=byId.get(ref.media_asset_id);
  if(!file||file.sha256!==ref.media_sha256||file.media_type!==ref.media_type)throw new Error(ref.content+' 자료 원본이 없거나 변경되었습니다.');
  const url=esc(file.media_url),label=esc(ref.content);
  html+=`<div class='card'><b>${label}</b><p class='muted'>${esc(ref.media_type)} · ${file.file_size}바이트</p>`+
    (ref.kind==='IMAGE'?`<img alt='${label}' src='${url}' style='max-width:100%;max-height:300px'>`:ref.kind==='VIDEO'?`<video controls playsinline preload='metadata' src='${url}' style='max-width:100%'></video>`:`<audio controls src='${url}'></audio>`)+`</div>`;
 }
 materialPreviewedSignature='';q('#materialMediaPreview').innerHTML=html;
 const media=q('#materialMediaPreview').querySelectorAll('img,video,audio');
 await Promise.all(Array.from(media,element=>new Promise((resolve,reject)=>{
   const isImage=element.tagName==='IMG',ok=()=>isImage?element.complete&&element.naturalWidth>0:element.readyState>=1;
   if(ok()){resolve();return;}
   const timer=setTimeout(()=>{cleanup();reject(new Error('교사 미리보기 자료 로드가 완료되지 않았습니다.'));},15000);
   function cleanup(){clearTimeout(timer);element.removeEventListener(isImage?'load':'loadedmetadata',loaded);element.removeEventListener('error',failed);}
   function loaded(){cleanup();resolve();}function failed(){cleanup();reject(new Error('교사 미리보기 자료를 읽을 수 없습니다.'));}
   element.addEventListener(isImage?'load':'loadedmetadata',loaded,{once:true});element.addEventListener('error',failed,{once:true});
 })));
 materialPreviewedSignature=JSON.stringify(b);
}
async function attachMaterialMedia(){try{
 const b=JSON.parse(q('#materialJson').value),files=Array.from(q('#materialFiles').files||[]);
 if(!files.length)throw new Error('자료 파일을 선택해 주세요.');
 const scenario=b.scenarios.find(s=>s.scenario_id===q('#materialScenario').value);
 if(!scenario||!b.links.length)throw new Error('조건 장면과 논리 연결을 먼저 확인해 주세요.');
 await ensureMaterialSession();const generation='gen-'+crypto.randomUUID();scenario.generation_id=generation;
 for(const a of b.representations)if(a.scenario_id===scenario.scenario_id)a.generation_id=generation;
 for(const v of b.validations)if(v.scenario_id===scenario.scenario_id){v.status='PENDING';v.generation_id='';v.manifest_digest='';}
 b.revision_id='revision-'+crypto.randomUUID();
 for(const file of files){
  if(file.size>8000000)throw new Error(file.name+' 파일이 8MB를 넘습니다.');
  const kind=file.type.startsWith('image/')?'IMAGE':file.type.startsWith('video/')?'VIDEO':file.type.startsWith('audio/')?'UPLOADED_AUDIO':null;
  if(!kind)throw new Error(file.name+' 파일 형식을 사용할 수 없습니다.');
  const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
  const uploaded=await api(`/tutor/api/sessions/${materialSession.session_id}/media?teacher_token=${encodeURIComponent(materialSession.teacher_token)}`,
    {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({media_type:file.type,data_base64:btoa(binary)})});
  const id=new URL(uploaded.media_url,location.origin).pathname.split('/').pop();
  b.representations.push({asset_id:'media-'+crypto.randomUUID(),kind,scenario_id:scenario.scenario_id,
    condition_revision_id:scenario.condition_revision_id,generation_id:generation,depends_on:[b.links[0].link_id],
    content:file.name.slice(0,100),choices:[],media_asset_id:id,media_sha256:uploaded.sha256,media_type:uploaded.media_type});
 }
 q('#materialJson').value=JSON.stringify(b,null,2);materialPreview(b);await showMaterialMediaPreviews(b);
 q('#materialMessage').textContent='자료를 등록했습니다. 파일을 재생·확인한 뒤 초안을 저장해 주세요.';
 }catch(e){q('#materialMessage').textContent=e.message;}}
async function createMaterialDraft(){try{
 const b=JSON.parse(q('#materialJson').value);materialPreview(b);
 await ensureMaterialSession();
 const base=`/tutor/api/sessions/${materialSession.session_id}/structured-materials?teacher_token=${encodeURIComponent(materialSession.teacher_token)}`;
 const r=await api(base,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify(b)});
 q('#publishMaterial').disabled=false;q('#materialMessage').textContent=`초안을 저장했습니다. 검토 항목 ${r.publication_issues.length}개. 학생 제공은 대기 상태입니다.`;
 }catch(e){q('#materialMessage').textContent=e.message;}}
async function reviewAndPublishMaterial(){try{
 if(!materialSession)return;
 const b=JSON.parse(q('#materialJson').value);materialPreview(b);
 if(b.representations.some(a=>['IMAGE','VIDEO','UPLOADED_AUDIO'].includes(a.kind))&&
    materialPreviewedSignature!==JSON.stringify(b)){
   await showMaterialMediaPreviews(b);
   q('#materialMessage').textContent='아래 자료의 그림·영상·음성을 실제로 확인한 뒤 확정 버튼을 다시 눌러 주세요.';return;
 }
  if(!confirm('모든 조건·추론·그림·음성 원고·문항·정답과 검증 근거를 직접 확인했습니까? 확인 후 서버가 자료 세대와 내용 묶음에 결속한 교사 확인 기록을 생성합니다.'))return;
  b.revision_id=b.revision_id+'-review-'+Date.now();
 const base=`/tutor/api/sessions/${materialSession.session_id}/structured-materials`,query=`?teacher_token=${encodeURIComponent(materialSession.teacher_token)}`;
 const draft=await api(base+query,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify(b)});q('#materialJson').value=JSON.stringify(b,null,2);
 await api(base+'/publish'+query,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({revision_id:b.revision_id,expected_digest:draft.digest,teacher_verified_content:true})});
  q('#materialMessage').innerHTML=`<p>교사가 확인한 자료 묶음의 구조와 세대가 검증되어 확정되었습니다. 내용의 정확성은 교사가 직접 확인한 범위입니다.</p><a class='btn primary' target='_blank' href='${esc(materialSession.board_url)}'>전자칠판 열기</a> <a class='btn' target='_blank' href='${esc(materialSession.teacher_url)}'>교사 콘솔</a> <a class='btn' target='_blank' href='${esc(materialSession.join_url)}'>학생 참가</a>`;
 }catch(e){q('#materialMessage').textContent=e.message;}}
async function populateResumeSessions(){try{
 const result=await api('/classroom/api/my-sessions');
 q('#resumeSession').innerHTML=(result.sessions||[]).map(s=>`<option value="${esc(s.board_url)}">${esc(s.title)} · ${esc(s.created_at)}</option>`).join('');
 if(!result.sessions.length)q('#resumeSession').innerHTML='<option value="">저장된 수업 없음</option>';
}catch(e){q('#resumeSession').innerHTML='<option value="">로그인 상태를 확인해 주세요</option>';}}
async function resumeMaterialSession(){try{
 const boardUrl=q('#resumeSession').value;if(!boardUrl)throw new Error('다시 열 수업을 선택해 주세요.');
 const parsed=new URL(boardUrl,location.origin),sessionId=parsed.pathname.split('/').pop(),teacherToken=parsed.searchParams.get('teacher_token');
 if(!teacherToken)throw new Error('수업 권한 정보가 없습니다.');
 const loaded=await api(`/tutor/api/sessions/${sessionId}/structured-materials?teacher_token=${encodeURIComponent(teacherToken)}`);
 const session=await api(`/tutor/api/sessions/${sessionId}?teacher_token=${encodeURIComponent(teacherToken)}`);
 materialSession={session_id:sessionId,teacher_token:teacherToken,board_url:boardUrl,
    teacher_url:`/classroom/teacher/${encodeURIComponent(sessionId)}?teacher_token=${encodeURIComponent(teacherToken)}`,
    join_url:`/classroom/join/${encodeURIComponent(sessionId)}?token=${encodeURIComponent(session.join_token)}`};
 q('#materialJson').value=JSON.stringify(loaded.bundle,null,2);materialPreview(loaded.bundle);
 q('#publishMaterial').disabled=false;await showMaterialMediaPreviews(loaded.bundle);
 q('#materialMessage').textContent='저장된 교수자료를 다시 열었습니다. 내용과 자료를 검토한 뒤 새 버전으로 확정해 주세요.';
}catch(e){q('#materialMessage').textContent='재개 실패: '+e.message;}}
if(q('#resumeSession'))populateResumeSessions();
"""

HOME_HTML = HOME_HTML.replace("</main><script>", MATERIAL_EDITOR_HTML + "</main><script>", 1).replace(
    "</script></body></html>", MATERIAL_EDITOR_SCRIPT + "</script></body></html>", 1
)


TEACHER_PRODUCT_HOME_HTML = (
    HOME_HTML
    .replace("<title>EduRecover AI 튜터</title>", "<title>EduRecover Classroom Board 교사용</title>")
    .replace("EduRecover 구조화 AI 교수자료·시청각 수업", "EduRecover Classroom Board 교사용")
    .replace("새 튜터 수업 만들기", "새 전자칠판 수업 만들기")
    .replace("AI 튜터·시청각 수업 만들기", "전자칠판 수업 만들기")
    .replace("OWNER:CURRENT_LESSON", "TEACHER_PRODUCT:CURRENT_LESSON")
    .replace("owner-current", "teacher-product-current")
    .replace("api('/tutor/api/sessions'", "api('/classroom/api/sessions'")
    .replace("api('/tutor/api/structured-materials/example')",
             "api('/classroom/api/structured-materials/example')")
    .replace("운영 플랫폼으로</a>", "내 수업으로</a>")
    .replace("href='/owner'", "href='/classroom/sessions'")
    .replace("</main><script>", """<section class='card'><h2>목표 → 기초 → 추론 → 적용 학습경로</h2>
<p class='muted'>교사가 조건과 근거를 검토해 거시·미시 학습경로를 만들고 전자칠판에서 진행합니다.</p>
<button class='btn primary' onclick='createPathSession()'>새 학습경로 수업 만들기</button><div id='pathSessionResult' role='status'></div></section>
<section class='card'><h2>저장된 구조화 교수자료 재개</h2><label>내 수업 <select id='resumeSession'></select></label>
<button class='btn' onclick='resumeMaterialSession()'>저장된 초안 다시 열기</button></section></main><script>""")
    .replace("</script></body></html>", """async function createPathSession(){try{
 const result=await api('/classroom/api/sessions',{method:'POST',headers:{'content-type':'application/json'},
 body:JSON.stringify({title:q('#title').value,room_alias:q('#room').value,launch_mode:q('#launch').value,
 connection_method:q('#conn').value,privacy_mode:'ALIAS_ONLY'})});
 location.href=`/classroom/learning-path/${encodeURIComponent(result.session_id)}?teacher_token=${encodeURIComponent(result.teacher_token)}`;
}catch(e){q('#pathSessionResult').textContent=e.message;}}
</script></body></html>""")
)


LEARNING_PATH_EDITOR_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>거시·미시 학습경로</title>
<style>__COMMON_CSS__</style></head><body>
<div class="top"><b>EduRecover 거시·미시 학습경로</b><a class="btn" href="/classroom/sessions">내 수업</a></div>
<main style="max-width:1000px;margin:auto;padding:18px">
<section class="card"><h1>목표에서 기초 개념까지, 근거를 따라 수업 만들기</h1>
<p class="muted">목표·선행개념·추론의 전제/규칙/결론/예외·조건 버전을 검토하세요. 파일과 문항을 연결한 초안은 공개 전 보류됩니다. 교사가 근거와 내용을 직접 확인해야 확정할 수 있습니다.</p>
<button class="btn" onclick="loadExample()">검토용 예시 불러오기</button>
<button class="btn" onclick="loadSaved()">저장된 초안 다시 열기</button>
<div id="map" role="status"></div></section>
<section class="card"><h2>시청각 자료 연결</h2><p class="muted">해당 수업에 등록되는 이미지·영상·음성을 추론 단계에 결속합니다. 파일을 재생해 내용을 직접 확인하세요.</p>
<label>연결할 추론 단계 <select id="transition"></select></label>
<label>자료 파일 <input id="mediaFile" type="file" accept="image/png,image/jpeg,audio/mpeg,audio/wav,video/mp4,video/webm"></label>
<button class="btn" onclick="attachFile()">파일 등록·경로에 연결</button><div id="mediaPreview"></div></section>
<section class="card"><h2>학습경로 상세 편집</h2><p class="muted">조건을 변경하면 추론 및 자료의 검증 조건 스냅샷을 새로 검토해야 합니다. 독립 적용 질문에는 다른 문항과 정답 선택지를 입력하세요.</p>
<textarea id="pathJson" rows="22" aria-label="학습경로 JSON" spellcheck="false"></textarea>
<div><button class="btn" onclick="previewPath()">구조 미리보기</button>
<button class="btn" onclick="savePath()">초안 저장·공개 보류</button>
<button class="btn primary" onclick="publishPath()">교사 검토 완료·전자칠판 공개</button></div>
<p id="message" role="status"></p><div id="links"></div></section></main>
<script>__COMMON_JS__
const SID=__SESSION_ID__,TT=__TEACHER_TOKEN__,base=`/classroom/api/sessions/${SID}/learning-path`,token=`?teacher_token=${encodeURIComponent(TT)}`;
let savedDigest=null,savedSignature='',previewSignature='';
function currentPath(){return JSON.parse(q('#pathJson').value);}
function setPath(path){q('#pathJson').value=JSON.stringify(path,null,2);renderPath(path);}
function renderPath(path){
 const nodes=new Map((path.nodes||[]).map(n=>[n.node_id,n]));
 q('#map').innerHTML=`<p><b>목표</b> ${esc(nodes.get(path.goal_id)?.label||'미설정')} · <b>현재 단계</b> ${esc(nodes.get(path.current_node_id)?.label||'미설정')}</p>`+
 (path.nodes||[]).map(n=>`<div class="card"><b>${esc(n.stage)} · ${esc(n.label)}</b><p class="muted">선행 개념: ${esc((n.prerequisite_ids||[]).map(id=>nodes.get(id)?.label||id).join(' · ')||'없음')}</p></div>`).join('')+
 (path.transitions||[]).map(t=>`<p><b>${esc(nodes.get(t.from_node)?.label||t.from_node)} → ${esc(nodes.get(t.to_node)?.label||t.to_node)}</b><br>전제 ${esc(t.premise)} / 규칙 ${esc(t.rule)} / 결론 ${esc(t.conclusion)} / 예외 ${esc(t.exception||'없음')}<br><span class="muted">조건 ${esc(JSON.stringify(t.validation_snapshot))} · 근거 ${esc(t.evidence_ref)} · 판정 ${esc(t.validation_status)}</span></p>`).join('');
 q('#transition').innerHTML=(path.transitions||[]).map(t=>`<option value="${esc(t.transition_id)}">${esc(t.transition_id)} · ${esc(nodes.get(t.to_node)?.label||t.to_node)}</option>`).join('');
}
function previewPath(){try{renderPath(currentPath());q('#message').textContent='구조를 미리 봤습니다. 조건·근거와 정답은 교사가 확인해 주세요.';}catch(e){q('#message').textContent='학습경로 JSON 형식을 확인해 주세요.';}}
async function loadExample(){try{const path=await api('/classroom/api/learning-path/example');setPath(path);savedDigest=null;savedSignature='';previewSignature='';q('#mediaPreview').textContent='연결된 파일이 없습니다.';q('#message').textContent='예시입니다. 수업 내용과 근거를 직접 검토해 주세요.';}catch(e){q('#message').textContent=e.message;}}
async function loadSaved(){try{
 const response=await fetch(base+token,{cache:'no-store'});
 if(response.status===404){await loadExample();return;}
 if(!response.ok)throw new Error('저장된 학습경로를 불러올 권한을 확인해 주세요.');
 const result=await response.json();setPath(result.path);savedDigest=result.digest;savedSignature=JSON.stringify(result.path);
 q('#message').textContent=result.active_revision===result.path.revision_id?'공개 중인 경로를 다시 열었습니다.':
    `초안을 다시 열었습니다. 검토할 항목: ${(result.publication_issues||[]).join(', ')||'없음'}`;
 await showPathMedia(result.path);
}catch(e){q('#message').textContent=e.message;}}
async function showPathMedia(path){
 previewSignature='';const assets=(path.assets||[]).filter(a=>a.media_id);
 if(!assets.length){q('#mediaPreview').textContent='연결된 파일이 없습니다.';previewSignature=JSON.stringify(path);return;}
 const library=await api(`/tutor/api/sessions/${SID}/media-library?teacher_token=${encodeURIComponent(TT)}`);
 const byId=new Map(library.assets.map(a=>[a.asset_id,a]));let html='';
 for(const a of assets){const file=byId.get(a.media_id);if(!file)throw new Error(a.asset_id+' 자료가 없거나 변경되었습니다.');
   const src=esc(file.media_url),label=esc(a.content||a.asset_id);
   html+=`<div class="card"><b>${label}</b><p class="muted">${esc(file.media_type)} · ${file.file_size}바이트</p>`+
     (file.media_type.startsWith('image/')?`<img alt="${label}" src="${src}" style="max-width:100%;max-height:320px">`:
      file.media_type.startsWith('audio/')?`<audio controls preload="metadata" src="${src}"></audio>`:
      `<video controls playsinline preload="metadata" src="${src}" style="max-width:100%;max-height:320px"></video>`)+`</div>`;
 }
 q('#mediaPreview').innerHTML=html;
 await Promise.all(Array.from(q('#mediaPreview').querySelectorAll('img,audio,video'),element=>new Promise((resolve,reject)=>{
   const image=element.tagName==='IMG',valid=()=>image?element.complete&&element.naturalWidth>0:element.readyState>=1;
   if(valid()){resolve();return;}
   const timer=setTimeout(()=>{done();reject(new Error('매체 미리보기 로드가 완료되지 않았습니다.'));},15000);
   function done(){clearTimeout(timer);element.removeEventListener(image?'load':'loadedmetadata',loaded);element.removeEventListener('error',failed);}
   function loaded(){done();resolve();}function failed(){done();reject(new Error('매체 미리보기를 읽을 수 없습니다.'));}
   element.addEventListener(image?'load':'loadedmetadata',loaded,{once:true});element.addEventListener('error',failed,{once:true});
 })));
 previewSignature=JSON.stringify(path);
}
async function attachFile(){try{
 const file=q('#mediaFile').files?.[0],path=currentPath();if(!file)throw new Error('파일을 선택해 주세요.');
 if(file.size>8000000)throw new Error('파일은 8MB 이하로 준비해 주세요.');
 const kind=file.type.startsWith('image/')?'IMAGE':file.type.startsWith('video/')?'VIDEO':file.type.startsWith('audio/')?'AUDIO':null;
 if(!kind)throw new Error('지원되지 않는 파일 형식입니다.');
 const t=path.transitions.find(x=>x.transition_id===q('#transition').value);if(!t)throw new Error('연결할 추론 단계를 선택해 주세요.');
 const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
 const result=await api(`/tutor/api/sessions/${SID}/media?teacher_token=${encodeURIComponent(TT)}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({media_type:file.type,data_base64:btoa(binary)})});
 const id=new URL(result.media_url,location.origin).pathname.split('/').pop();
 path.assets.push({asset_id:'file-'+crypto.randomUUID(),kind,depends_on:[t.transition_id],
   condition_snapshot:t.validation_snapshot,content:file.name.slice(0,100),media_id:id});
 path.revision_id='path-'+crypto.randomUUID();setPath(path);savedDigest=null;savedSignature='';
 await showPathMedia(path);q('#message').textContent='경로에 파일을 연결했습니다. 자료 내용을 직접 재생·검토한 뒤 초안을 저장해 주세요.';
}catch(e){q('#message').textContent=e.message;}}
async function savePath(){try{
 const path=currentPath();path.revision_id='path-'+crypto.randomUUID();
 const result=await api(base+token,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify(path)});
 setPath(path);savedDigest=result.digest;savedSignature=JSON.stringify(path);previewSignature='';
 q('#message').textContent='초안 저장 완료. 학생 제공 보류. 검토할 항목: '+(result.publication_issues||[]).join(', ');
 await showPathMedia(path);
}catch(e){q('#message').textContent=e.message;}}
async function publishPath(){try{
 const path=currentPath();if(!savedDigest||JSON.stringify(path)!==savedSignature)throw new Error('현재 편집 내용을 초안으로 먼저 저장해 주세요.');
 if(path.assets.some(a=>a.media_id)&&previewSignature!==savedSignature){await showPathMedia(path);
   q('#message').textContent='파일 로드를 확인했습니다. 자료 내용을 검토한 뒤 다시 확정해 주세요.';return;}
 if(!confirm('목표·조건·추론 근거·문항·정답·시청각자료 내용과 사용권한을 교사가 직접 확인했습니까?'))return;
 const result=await api(base+'/publish'+token,{method:'POST',headers:{'content-type':'application/json'},
   body:JSON.stringify({revision_id:path.revision_id,expected_digest:savedDigest,teacher_verified_content:true})});
 q('#message').textContent=`검토한 학습경로 ${result.segment_count}단계를 전자칠판에 공개했습니다. 정확성은 교사의 검토 범위입니다.`;
 q('#links').innerHTML=`<a class="btn primary" target="_blank" href="/classroom/teacher/${encodeURIComponent(SID)}?teacher_token=${encodeURIComponent(TT)}">교사 콘솔</a> `+
   `<a class="btn" target="_blank" href="/classroom/board/${encodeURIComponent(SID)}?teacher_token=${encodeURIComponent(TT)}">전자칠판</a>`;
}catch(e){q('#message').textContent=e.message;}}
loadSaved();</script></body></html>"""


def learning_path_editor_html(session_id: str, teacher_token: str) -> str:
    return (LEARNING_PATH_EDITOR_HTML.replace("__COMMON_CSS__", COMMON_CSS)
            .replace("__COMMON_JS__", COMMON_JS)
            .replace("__SESSION_ID__", json.dumps(session_id))
            .replace("__TEACHER_TOKEN__", json.dumps(teacher_token)))

TEACHER_LOGIN_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EduRecover 교사 로그인</title><style>
body{font-family:system-ui,sans-serif;background:#f2f5fa;color:#172b45}
main{max-width:420px;margin:8vh auto;background:white;padding:28px;border-radius:18px}
input,button{box-sizing:border-box;display:block;width:100%;padding:14px;margin:12px 0;font-size:17px}
button{background:#185bc4;color:white;border:0;border-radius:8px}
</style></head><body><main><h1>교사 로그인</h1><p>관리자가 발급한 교사 계정으로 접속해 주세요.</p>
<label>교사 아이디<input id="username" autocomplete="username"></label>
<label>비밀번호<input id="password" type="password" autocomplete="current-password"></label>
<button onclick="login()">로그인</button><p id="message" role="alert"></p></main>
<script>async function login(){let r=await fetch('/classroom/api/login',{method:'POST',
headers:{'content-type':'application/json'},body:JSON.stringify({
username:document.querySelector('#username').value,password:document.querySelector('#password').value})});
if(r.ok)location.href='/classroom';else document.querySelector('#message').textContent='로그인 정보를 확인해 주세요.';}
</script></body></html>"""


@teacher_classroom_router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def classroom_login_page():
    return HTMLResponse(TEACHER_LOGIN_HTML, headers=PRODUCT_HTML_HEADERS)


@teacher_classroom_router.get("/api/my-sessions")
def classroom_my_sessions(teacher: str = Depends(require_classroom_user)):
    with db() as conn:
        if teacher == "__OWNER__":
            rows = conn.execute("SELECT * FROM sessions WHERE owner_teacher IS NULL ORDER BY created_at DESC LIMIT 100").fetchall()
        else:
            rows = conn.execute("SELECT * FROM sessions WHERE owner_teacher=? ORDER BY created_at DESC LIMIT 100",
                                (teacher,)).fetchall()
    return {"sessions": [{"title": x["title"], "created_at": x["created_at"],
                          "board_url": f"/classroom/board/{x['session_id']}?teacher_token={x['teacher_token']}",
                          "teacher_url": f"/classroom/teacher/{x['session_id']}?teacher_token={x['teacher_token']}"}
                         for x in rows]}


@teacher_classroom_router.get("/sessions", response_class=HTMLResponse, include_in_schema=False)
def classroom_sessions_page(teacher: str = Depends(require_classroom_user)):
    html = """<!doctype html><html lang="ko"><meta charset="utf-8"><title>내 전자칠판 수업</title>
<body style="font:18px system-ui;max-width:900px;margin:32px auto">
<h1>내 전자칠판 수업</h1><a href="/classroom">새 수업 만들기</a><div id="list">불러오는 중...</div>
<script>fetch('/classroom/api/my-sessions').then(r=>r.json()).then(d=>{
const root=document.querySelector('#list');root.textContent='';
for(const s of d.sessions){let p=document.createElement('p'),name=document.createElement('strong');
name.textContent=s.title+' · '+s.created_at;p.append(name);
for(const [label,url] of [['교사 콘솔',s.teacher_url],['전자칠판',s.board_url]]){
let a=document.createElement('a');a.href=url;a.textContent=label;a.style.marginLeft='18px';p.append(a);}root.append(p);}
if(!d.sessions.length)root.textContent='저장된 수업이 없습니다.';}).catch(()=>document.querySelector('#list').textContent='수업 목록을 불러오지 못했습니다.');</script></body></html>"""
    return HTMLResponse(html, headers=PRODUCT_HTML_HEADERS)


@teacher_classroom_router.get("/accounts", response_class=HTMLResponse, include_in_schema=False)
def classroom_accounts_page(_: None = Depends(require_owner)):
    html = """<!doctype html><html lang="ko"><meta charset="utf-8"><title>교사 계정 발급</title>
<body style="font:18px system-ui;max-width:520px;margin:32px auto"><h1>교사 계정 발급</h1>
<p>교사에게 전달할 아이디와 임시 비밀번호를 직접 정해 입력해 주세요.</p>
<label>아이디<input id="username" autocomplete="off"></label>
<label>임시 비밀번호(12자 이상)<input id="password" type="password" autocomplete="new-password"></label>
<button onclick="createTeacher()">계정 발급</button><p id="result" role="status"></p>
<script>async function createTeacher(){let r=await fetch('/classroom/api/teachers',{method:'POST',
headers:{'content-type':'application/json'},body:JSON.stringify({
username:document.querySelector('#username').value,password:document.querySelector('#password').value})});
document.querySelector('#result').textContent=r.ok?'교사 계정을 발급했습니다. 로그인 주소: /classroom/login':'발급 실패: 아이디 중복 또는 입력값을 확인해 주세요.';}
</script></body></html>"""
    return HTMLResponse(html, headers=PRODUCT_HTML_HEADERS)


@classroom_tutor_router.get("", response_class=HTMLResponse, include_in_schema=False)
def home(_: None = Depends(require_owner)):
    return HTMLResponse(HOME_HTML, headers={"Cache-Control": "no-store", "Referrer-Policy": "same-origin"})


@teacher_classroom_router.get("", response_class=HTMLResponse, include_in_schema=False)
def teacher_product_home(teacher: str = Depends(require_classroom_user)):
    account_link = ('<a class="btn" href="/classroom/accounts">교사 계정 발급</a>'
                    if teacher == "__OWNER__" else "")
    page = TEACHER_PRODUCT_HOME_HTML.replace(
        "<main style='max-width:980px;margin:auto;padding:18px'>",
        "<main style='max-width:980px;margin:auto;padding:18px'>" + account_link, 1,
    )
    return HTMLResponse(page, headers=PRODUCT_HTML_HEADERS)
