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
import hashlib
import logging
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.services.owner_basic_auth import require_owner
from app.services.structured_materials import (
    StructuredMaterialBundle, affected_assets, bundle_digest, bundle_lesson, publication_issues, example_bundle,
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
CREATE TABLE IF NOT EXISTS response_material_snapshots (
  response_id TEXT PRIMARY KEY,
  segment_json TEXT NOT NULL,
  FOREIGN KEY(response_id) REFERENCES responses(response_id) ON DELETE CASCADE
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def json_load(text: str) -> Any:
    return json.loads(text) if text else None


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


def student_session_view(row: sqlite3.Row) -> dict[str, Any]:
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
    session.pop("join_token", None)
    return session


class Segment(BaseModel):
    segment_id: str
    kind: Literal["EXPLAIN", "VIDEO", "SOCRATIC", "RETEST", "MAP", "DEMONSTRATE", "COMPARE", "PRACTICE"]
    title: str
    text: str = ""
    media_url: Optional[str] = None
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


class LessonReplace(BaseModel):
    lesson: list[Segment]


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


def create_session_record(req: SessionCreate, *, product_prefix: str) -> dict[str, str]:
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
        conn.execute(
            """INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
def create_teacher_product_session(req: SessionCreate, _: None = Depends(require_owner)):
    return create_session_record(req, product_prefix="/classroom")


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
            return student_session_view(row) if display_only else row_to_session(row)
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
        if state.get("structured_materials"):
            raise HTTPException(409, "구조화 교수자료의 검토·확정 기능을 사용해 주세요.")
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
        issues = publication_issues(bundle)
        if issues:
            raise HTTPException(423, {"message": "자료 검증이 완료되지 않았습니다.", "issues": issues})
        lesson = bundle_lesson(bundle)
        state.update({"active_material_revision": req.revision_id, "playback": "PAUSED",
                      "current_segment_id": lesson[0]["segment_id"], "last_command_at": now_iso()})
        conn.execute("UPDATE material_revisions SET reviewed_at=? WHERE session_id=? AND revision_id=?",
                     (now_iso(), session_id, req.revision_id))
        conn.execute("UPDATE sessions SET state_json=?,lesson_json=?,updated_at=? WHERE session_id=?",
                     (json.dumps(state, ensure_ascii=False), json.dumps(lesson, ensure_ascii=False), now_iso(), session_id))
        emit(conn, session_id, "MATERIAL_PUBLISHED", {"revision_id": req.revision_id,
             "digest": req.expected_digest, "review_basis": "TEACHER_ATTESTATION_AND_STRUCTURAL_CHECKS"})
    return {"ok": True, "active_revision": req.revision_id, "segment_count": len(lesson)}


def require_legacy_lesson_edit(row: sqlite3.Row) -> None:
    if json_load(row["state_json"]).get("structured_materials"):
        raise HTTPException(409, "구조화 교수자료는 교수자료 초안에서 수정하고 다시 확정해 주세요.")
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
        return {"ok": True, "event_id": event_id, "state": state}


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


def board_html(
    session_id: str,
    teacher_token: str,
    product_prefix: str = "/tutor",
) -> str:
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no'><title>EduRecover Board</title><style>{COMMON_CSS}</style></head>
<body><div class='top'><div><b>EduRecover 전자칠판</b> <span class='badge' id='conn'>연결중</span></div><div><span class='badge' id='mode'></span> <button class='btn' onclick='goFull()'>전체화면</button></div></div>
<div class='grid'><main class='stage'><div class='card'><div class='muted' id='kind'></div><div class='big' id='title'>불러오는 중...</div><p id='text' style='font-size:clamp(18px,2.1vw,32px)'></p><div id='structure'></div><div id='media'></div><div id='choices'></div></div><div class='card' id='whiteWrap' style='display:none'><canvas id='whiteboard' class='board'></canvas></div></main>
<aside class='side'><div class='card'><b>교사 제어</b><div class='controls'><button class='primary' onclick="cmd('PLAY')">재생/TTS</button><button onclick="cmd('PAUSE')">일시정지</button><button onclick="cmd('PREV')">이전</button><button onclick="cmd('NEXT')">다음</button><button onclick='toggleBoard()'>판서 전환</button><button class='danger' onclick="cmd('STOP')">전체중지</button></div></div>
<div class='card'><b>학생 참가</b><div id='qr' style='margin-top:10px'></div><div class='muted' id='joinText'></div></div>
<div class='card'><b>응답 현황</b><div id='results'>아직 응답 없음</div></div>
<div class='card'><b>장치 capability</b><pre id='caps' style='white-space:pre-wrap'></pre></div></aside></div>
<script src='https://cdn.jsdelivr.net/npm/qrcodejs@1.0.0/qrcode.min.js'></script><script>{COMMON_JS}
const SID={json.dumps(session_id)}, TT={json.dumps(teacher_token)}, PFX={json.dumps(product_prefix)};let S=null,lastEvent=0,videoPausedOnce=false,lastRenderKey="",lastPlayback="";
async function refresh(){{try{{S=await api(`/tutor/api/sessions/${{SID}}?teacher_token=${{encodeURIComponent(TT)}}&display_only=true`);q('#conn').textContent='연결됨';q('#mode').textContent=S.launch_mode+' / '+S.connection_method;render();await loadResults();}}catch(e){{S=null;lastRenderKey='';lastPlayback='';if('speechSynthesis' in window)speechSynthesis.cancel();q('#vid')?.pause();q('#media').innerHTML='';q('#structure').innerHTML='';q('#choices').innerHTML='';q('#title').textContent='자료 제공 대기';q('#text').textContent='교사 검증 또는 연결 상태를 확인해 주세요.';q('#conn').textContent='대기';}}}}
function seg(){{return (S?.lesson||[]).find(x=>x.segment_id===S?.state?.current_segment_id)||null}}
function render(){{let x=seg();if(!x)return;let key=x.segment_id+':'+(x.structured_bundle_revision||'');let changed=key!==lastRenderKey;if(changed){{lastRenderKey=key;q('#structure').innerHTML=materialStructure(x);q('#kind').textContent=x.kind+(x.misconception_path?' · 오답원인: '+x.misconception_path:'');q('#title').textContent=x.title;q('#text').textContent=x.text||'';let m=q('#media');m.innerHTML='';videoPausedOnce=false;if(x.kind==='VIDEO'&&x.media_url){{let yt=youtubeEmbed(x.media_url);if(yt){{m.innerHTML=`<iframe title='수업 영상' src="${{esc(yt)}}" style='width:100%;height:min(56vw,520px);border:0;border-radius:12px' allow='autoplay; encrypted-media; picture-in-picture' allowfullscreen></iframe>`;}}else{{m.innerHTML=`<video id='vid' controls playsinline src="${{esc(x.media_url)}}"></video>`;let v=q('#vid');if(x.pause_at_sec) v.addEventListener('timeupdate',()=>{{if(!videoPausedOnce&&v.currentTime>=x.pause_at_sec){{videoPausedOnce=true;v.pause();q('#choices').innerHTML=`<div class='card'><b>생각해 볼 질문</b><p>${{esc(x.text)}}</p></div>`;}}}});}}}}
let c=q('#choices');c.innerHTML='';if(['SOCRATIC','RETEST'].includes(x.kind)){{c.innerHTML=`<div class='muted'>학생은 자신의 기기에서 응답합니다.</div>`;}}
}}if(changed||lastPlayback!==S.state.playback){{lastPlayback=S.state.playback;if(S.state.playback==='PLAYING'){{if(q('#vid'))q('#vid').play().catch(()=>{{}});else if(x.audio_text||x.text)speak(x.audio_text||x.text);}} else {{q('#vid')?.pause();if('speechSynthesis' in window)speechSynthesis.cancel();}}}}
}}
async function cmd(a){{await api(`/tutor/api/sessions/${{SID}}/command?teacher_token=${{encodeURIComponent(TT)}}`,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{action:a}})}});refresh();}}
async function loadResults(){{let x=seg();if(!x)return;if(x.independent_followup){{q('#results').textContent='독립 응답 중에는 다른 학생의 답을 표시하지 않습니다.';return;}}let r=await api(`/tutor/api/sessions/${{SID}}/results?teacher_token=${{encodeURIComponent(TT)}}&segment_id=${{encodeURIComponent(x.segment_id)}}`);let total=(r.totals[0]?.n)||0;if(!total){{q('#results').textContent='아직 응답 없음';return;}}q('#results').innerHTML=r.counts.map(z=>`<div style='margin:8px 0'><div>${{esc(z.answer)}} · ${{z.n}}명</div><div class='bar'><i style='width:${{Math.round(z.n/total*100)}}%'></i></div></div>`).join('');}}
function goFull(){{document.documentElement.requestFullscreen?.();}}
function toggleBoard(){{let w=q('#whiteWrap');w.style.display=w.style.display==='none'?'block':'none';}}
q('#caps').textContent=JSON.stringify(capabilities(),null,2);
const joinUrl=`${{location.origin}}/tutor/join/${{SID}}?token=${{encodeURIComponent(SID)}}`; // fallback text replaced after session fetch
async function makeQR(){{let s=await api(`/tutor/api/sessions/${{SID}}?teacher_token=${{encodeURIComponent(TT)}}`);let url=`${{location.origin}}${{PFX}}/join/${{SID}}?token=${{encodeURIComponent(s.join_token)}}`;q('#joinText').innerHTML=`참가 주소: <span class='muted'>${{esc(url)}}</span>`;try{{new QRCode(q('#qr'),{{text:url,width:190,height:190}});}}catch(e){{q('#qr').textContent='QR 라이브러리 로드 실패 — 위 주소 또는 참가코드를 사용하세요.';}}}}
// pointer-based whiteboard
let cv=q('#whiteboard'),ctx=cv.getContext('2d'),drawing=false,scale=1;function sizeCanvas(){{let r=cv.getBoundingClientRect();let d=devicePixelRatio||1;cv.width=Math.max(1,Math.floor(r.width*d));cv.height=Math.max(1,Math.floor(r.height*d));ctx.setTransform(d,0,0,d,0,0);ctx.lineCap='round';ctx.lineWidth=3;ctx.strokeStyle='#111';}};window.addEventListener('resize',sizeCanvas);setTimeout(sizeCanvas,100);
cv.addEventListener('pointerdown',e=>{{drawing=true;cv.setPointerCapture(e.pointerId);ctx.beginPath();let r=cv.getBoundingClientRect();ctx.moveTo(e.clientX-r.left,e.clientY-r.top);}});cv.addEventListener('pointermove',e=>{{if(!drawing)return;let r=cv.getBoundingClientRect();ctx.lineTo(e.clientX-r.left,e.clientY-r.top);ctx.stroke();}});['pointerup','pointercancel','pointerleave'].forEach(ev=>cv.addEventListener(ev,()=>drawing=false));
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
async function load(){{try{{S=await api(`/tutor/api/sessions/${{SID}}?join_token=${{encodeURIComponent(JT)}}`);let x=seg();if(!x)return;q('#kind').textContent=x.kind;q('#title').textContent=x.title;q('#text').textContent=x.kind==='VIDEO'?'전자칠판 설명을 보고 질문이 나오면 답하세요.':x.text;if(lastSeg!==x.segment_id+':'+(x.structured_bundle_revision||'')){{lastSeg=x.segment_id+':'+(x.structured_bundle_revision||'');q('#msg').textContent='';q('#choices').innerHTML=(x.choices||[]).map(c=>`<button class='choice' data-choice='${{esc(c)}}' onclick='send(this.dataset.choice)'>${{esc(c)}}</button>`).join('')||`<div class='muted'>현재는 응답 단계가 아닙니다.</div>`;}}}}catch(e){{S=null;lastSeg='';q('#title').textContent='자료 제공 대기';q('#text').textContent='';q('#choices').innerHTML='';q('#msg').textContent='교사 검증 또는 연결 상태를 확인 중입니다.';}}}}
async function send(ans){{let a=q('#alias').value.trim();if(!a){{q('#msg').textContent='가명/번호를 먼저 입력하세요.';return;}}let x=seg();let body={{student_alias:a,segment_id:x.segment_id,answer:ans,join_token:JT,material_revision_id:x.structured_bundle_revision||null,confidence:q('#conf').value?Number(q('#conf').value):null,reason_text:q('#reason').value||null}};try{{let r=await api(`/tutor/api/sessions/${{SID}}/respond`,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(body)}});q('#msg').textContent='응답을 보냈습니다.'+(r.correct===true?' ✓':r.correct===false?' — 다시 생각해 보세요.':'');}}catch(e){{q('#msg').textContent='응답 전송 실패';}}}}
load();setInterval(load,1500);</script></body></html>"""


PRODUCT_HTML_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def teacher_product_html(session_id: str, teacher_token: str) -> str:
    """Teacher console for the standalone product, without owner training controls."""
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>EduRecover Classroom Board 교사 콘솔</title><style>{COMMON_CSS}</style></head><body>
<div class='top'><div><b>EduRecover Classroom Board</b> <span class='badge'>교사 콘솔</span></div><span class='badge'>{session_id}</span></div>
<main style='max-width:980px;margin:auto;padding:18px'>
<div class='card'><h2 style='margin-top:0'>전자칠판 수업 운영</h2><p class='muted'>전자칠판 자체 브라우저나 OPS에서는 전자칠판 화면을 직접 열고, HDMI·USB-C·무선 캐스트 환경에서는 해당 화면을 미러링합니다.</p><a class='btn primary' style='display:inline-block;text-decoration:none' target='_blank' href='/classroom/board/{session_id}?teacher_token={teacher_token}'>전자칠판 화면 열기</a></div>
<div class='grid' style='grid-template-columns:1fr 1fr;min-height:auto'>
<div class='card'><b>수업자료 검토</b><p>문항·정답·설명을 확인한 뒤 학생에게 제공해 주세요.</p><button class='btn primary' onclick='publishLesson()'>검토한 수업자료 확정</button><div id='releaseStatus' role='status'></div></div><div class='card'><b>수업 단계</b><div id='segments' style='margin-top:10px'></div></div>
<div class='card'><b>실시간 학생 응답</b><div id='res' class='muted' style='margin-top:10px'>응답 대기 중</div></div>
</div>
<div class='card'><b>개인정보 원칙</b><p class='muted'>학생 실명 대신 가명 또는 번호만 사용합니다. 교사 화면에는 모델학습 관리나 내부 운영정보를 표시하지 않습니다.</p></div>
</main><script>{COMMON_JS}
const SID={json.dumps(session_id)},TT={json.dumps(teacher_token)};let S;
async function load(){{
  try{{
    const s=await api(`/tutor/api/sessions/${{SID}}?teacher_token=${{encodeURIComponent(TT)}}`);S=s;
    q('#segments').innerHTML=(s.lesson||[]).map(x=>`<button class='choice' data-segment='${{esc(x.segment_id)}}' onclick="go(this.dataset.segment)"><b>${{esc(x.title)}}</b><br><span class='muted'>${{esc(x.kind)}} · ${{esc(x.text)}}<br>선택지: ${{esc((x.choices||[]).join(' / '))}}<br>정답: ${{esc(x.correct_answer||'채점 없음')}}</span></button>`).join('')||'<p class="muted">수업 단계가 없습니다.</p>';
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
    return HTMLResponse(board_html(session_id, teacher_token), headers=PRODUCT_HTML_HEADERS)


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
        headers=PRODUCT_HTML_HEADERS,
    )


@teacher_classroom_router.get("/teacher/{session_id}", response_class=HTMLResponse)
def teacher_product_console(session_id: str, teacher_token: str):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
    return HTMLResponse(
        teacher_product_html(session_id, teacher_token),
        headers=PRODUCT_HTML_HEADERS,
    )


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
  try{{let r=await api('/tutor/api/sessions',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(body)}});let join=location.origin+r.join_url;q('#out').innerHTML=`<div class='card' style='margin-top:12px'><a class='btn primary' target='_blank' href='${{r.board_url}}'>전자칠판 열기</a> <a class='btn' target='_blank' href='${{r.teacher_url}}'>교사 콘솔</a><p>먼저 교사 콘솔에서 내용을 검토하고 수업자료를 확정해 주세요.</p><p class='muted'>학생 참가: <a href='${{r.join_url}}' target='_blank'>${{esc(join)}}</a></p></div>`;}}catch(e){{q('#out').innerHTML=`<p style='color:#fb7185'>수업 생성 실패: ${{esc(e.message)}}</p>`;}}
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
<details><summary>자료와 조건 편집</summary><textarea id='materialJson' rows='18' aria-label='교수자료 편집'></textarea></details>
<button class='btn' onclick='createMaterialDraft()'>초안 저장·검토</button>
<button class='btn primary' id='publishMaterial' onclick='reviewAndPublishMaterial()' disabled>내용 검토 완료·수업 확정</button>
<div id='materialMessage' role='status'></div></section>
"""

MATERIAL_EDITOR_SCRIPT = r"""
let materialSession=null;
function materialPreview(b){
 q('#materialPreview').innerHTML=materialStructure({learning_map:b.concepts,logic_links:b.links})+
 b.scenarios.map(s=>{const assets=b.representations.filter(x=>x.scenario_id===s.scenario_id);return `<article class='card'><h3>${esc(s.label)}</h3>${materialStructure({kind:'DEMONSTRATE',conditions:s.conditions})}${assets.map(a=>`<p><b>${esc(({SCENE:'설명 장면',AUDIO:'음성 원고',QUESTION:'연습문항',ANSWER:'정답'})[a.kind])}</b> ${esc(a.content)}</p>`).join('')}</article>`;}).join('')+
 `<div class='card'><b>논리 연결 검증 기록</b>${b.validations.map(v=>`<p>${esc(v.scenario_id)} / ${esc(v.link_id)}: ${esc(v.evidence)} <span class='muted'>${esc(v.status)}</span></p>`).join('')}</div>`+
 (b.independent_assessment?`<p><b>별도 독립문항</b> ${esc(b.independent_assessment.question)} / 정답 ${esc(b.independent_assessment.correct_answer)}</p>`:'');
}
async function loadMaterialExample(){try{const b=await api('/tutor/api/structured-materials/example');q('#materialJson').value=JSON.stringify(b,null,2);materialPreview(b);q('#materialMessage').textContent='예시 초안입니다. 조건과 풀이 근거를 확인해 주세요.';}catch(e){q('#materialMessage').textContent=e.message;}}
async function createMaterialDraft(){try{
 const b=JSON.parse(q('#materialJson').value);materialPreview(b);
 if(!materialSession)materialSession=await api('/tutor/api/sessions',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({title:q('#title').value,room_alias:q('#room').value,launch_mode:q('#launch').value,connection_method:q('#conn').value})});
 const base=`/tutor/api/sessions/${materialSession.session_id}/structured-materials?teacher_token=${encodeURIComponent(materialSession.teacher_token)}`;
 const r=await api(base,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify(b)});
 q('#publishMaterial').disabled=false;q('#materialMessage').textContent=`초안을 저장했습니다. 검토 항목 ${r.publication_issues.length}개. 학생 제공은 대기 상태입니다.`;
 }catch(e){q('#materialMessage').textContent=e.message;}}
async function reviewAndPublishMaterial(){try{
 if(!materialSession)return;
 const b=JSON.parse(q('#materialJson').value);materialPreview(b);
 if(!confirm('모든 조건·추론·그림·음성 원고·문항·정답과 검증 근거를 직접 확인했습니까? 이 확인은 교사의 내용 검토 기록으로 남습니다.'))return;
 b.validations=b.validations.map(v=>({...v,status:'PASSED'}));b.revision_id=b.revision_id+'-review-'+Date.now();
 const base=`/tutor/api/sessions/${materialSession.session_id}/structured-materials`,query=`?teacher_token=${encodeURIComponent(materialSession.teacher_token)}`;
 const draft=await api(base+query,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify(b)});q('#materialJson').value=JSON.stringify(b,null,2);
 await api(base+'/publish'+query,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({revision_id:b.revision_id,expected_digest:draft.digest,teacher_verified_content:true})});
 q('#materialMessage').innerHTML=`<p>검토한 자료 묶음이 확정되었습니다.</p><a class='btn primary' target='_blank' href='${esc(materialSession.board_url)}'>전자칠판 열기</a> <a class='btn' target='_blank' href='${esc(materialSession.teacher_url)}'>교사 콘솔</a> <a class='btn' target='_blank' href='${esc(materialSession.join_url)}'>학생 참가</a>`;
 }catch(e){q('#materialMessage').textContent=e.message;}}
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
)


@classroom_tutor_router.get("", response_class=HTMLResponse, include_in_schema=False)
def home(_: None = Depends(require_owner)):
    return HTMLResponse(HOME_HTML, headers={"Cache-Control": "no-store", "Referrer-Policy": "same-origin"})


@teacher_classroom_router.get("", response_class=HTMLResponse, include_in_schema=False)
def teacher_product_home(_: None = Depends(require_owner)):
    return HTMLResponse(TEACHER_PRODUCT_HOME_HTML, headers=PRODUCT_HTML_HEADERS)
