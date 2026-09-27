"""
EduRecover Classroom Video Tutor + Electronic Whiteboard Prototype v0.1

목표
- 학교/학원 전자칠판에서 웹 브라우저로 바로 실행
- OPS(전자칠판 내장 Windows PC), 일반 PC/노트북, 태블릿에서도 동일 UI 사용
- 무선 캐스트/HDMI/USB-C 환경에서는 표시용 화면으로 동작
- 터치/펜 가능 장치에서는 Pointer Events 기반 판서
- 터치백 미지원 환경에서는 교사기기 + 학생 QR/코드 참가로 상호작용 유지
- 오답원인(misconception_path) -> 짧은 영상/TTS -> 산파식 질문 -> 분기 -> 재시험 흐름
- 학생 실명 대신 alias(가명)만 저장

중요
- 이 파일은 동작 가능한 로컬/사설망 프로토타입입니다.
- Miracast/AirPlay/Google Cast/HDMI 자체 연결을 브라우저가 강제로 설정하지는 않습니다.
  해당 연결은 OS/전자칠판 기능을 이용하고, 본 앱은 연결 후 표시/상호작용을 담당합니다.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.services.owner_basic_auth import require_owner

APP_VERSION = "0.1.0"
STATE_DIR = Path(os.environ.get("EDURECOVER_CLASSROOM_STATE_DIR", ".edurecover_classroom_state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = STATE_DIR / "classroom_tutor.sqlite3"

classroom_tutor_router = APIRouter(prefix="/tutor", tags=["classroom-tutor"])

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
    return {
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


class Segment(BaseModel):
    segment_id: str
    kind: Literal["EXPLAIN", "VIDEO", "SOCRATIC", "RETEST"]
    title: str
    text: str = ""
    media_url: Optional[str] = None
    pause_at_sec: Optional[float] = None
    choices: list[str] = []
    correct_answer: Optional[str] = None
    misconception_path: Optional[str] = None
    branch_map: dict[str, str] = {}


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
    lesson: list[Segment] = []


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
    socratic_questions: list[str] = []
    retest_choices: list[str] = []
    retest_correct_answer: Optional[str] = None
    video_url: Optional[str] = None
    source_scope: str = "TUTOR_MINIMAL_CONTEXT"



DEFAULT_LESSON = [
    Segment(
        segment_id="seg-1",
        kind="EXPLAIN",
        title="오답 원인 확인",
        text="정답을 바로 말하지 않고, 먼저 학생이 어느 지점에서 조건을 바꾸어 읽었는지 확인합니다.",
        misconception_path="조건의 범위를 전체 상황으로 과도하게 일반화함",
    ),
    Segment(
        segment_id="seg-2",
        kind="VIDEO",
        title="짧은 설명 영상",
        text="영상은 한 번에 한 오개념만 교정합니다. 영상이 없으면 TTS로 같은 내용을 설명합니다.",
        media_url="",
        pause_at_sec=8,
        misconception_path="조건의 범위를 전체 상황으로 과도하게 일반화함",
    ),
    Segment(
        segment_id="seg-3",
        kind="SOCRATIC",
        title="산파식 확인 질문",
        text="문장에서 ‘항상’이라고 말한 부분이 실제로 있었나요, 아니면 특정 조건에서만 성립했나요?",
        choices=["항상 성립", "특정 조건에서만 성립", "잘 모르겠다"],
        correct_answer="특정 조건에서만 성립",
        branch_map={"항상 성립": "seg-2", "특정 조건에서만 성립": "seg-4", "잘 모르겠다": "seg-2"},
    ),
    Segment(
        segment_id="seg-4",
        kind="RETEST",
        title="전이 재시험",
        text="같은 오개념을 다른 표현의 문제에서 다시 확인합니다.",
        choices=["A", "B", "C", "D", "E"],
        correct_answer="B",
    ),
]




def build_lesson_from_bundle(bundle: EduRecoverQuestionBundle) -> list[dict[str, Any]]:
    """
    문제 DB의 최소 조각을 수업 단계로 변환한다.
    selected_choice가 있으면 그 오답 경로를 먼저 확인하고, 정답 전체 해설은 학생에게 즉시 공개하지 않는다.
    """
    qid = bundle.question_id
    choices = [f"{k}. {v}" for k, v in sorted(bundle.choices.items())]
    misconception = bundle.misconception_path or "아직 오답원인이 확정되지 않음"
    explain_text = bundle.concise_explanation or "정답을 바로 주기보다, 이 선택지가 왜 그럴듯해 보였는지부터 확인합니다."
    sq = bundle.socratic_questions or [
        "문제에서 실제로 주어진 조건과 내가 머릿속에서 추가한 조건을 나눠볼까요?",
        "내가 고른 선택지가 맞으려면 어떤 문장이나 계산이 반드시 성립해야 하나요?",
    ]
    retest = bundle.retest_choices or choices
    return [
        Segment(
            segment_id=f"{qid}-problem", kind="EXPLAIN", title=f"문제 {qid}",
            text=bundle.stem + "\n" + " | ".join(choices), misconception_path=misconception
        ).model_dump(),
        Segment(
            segment_id=f"{qid}-video", kind="VIDEO", title="오답원인 짧은 설명",
            text=explain_text, media_url=bundle.video_url or "", pause_at_sec=8, misconception_path=misconception
        ).model_dump(),
        Segment(
            segment_id=f"{qid}-socratic-1", kind="SOCRATIC", title="스스로 확인하기",
            text=sq[0], choices=["조건을 다시 확인했다", "아직 헷갈린다"],
            branch_map={"조건을 다시 확인했다": f"{qid}-socratic-2", "아직 헷갈린다": f"{qid}-video"},
            misconception_path=misconception
        ).model_dump(),
        Segment(
            segment_id=f"{qid}-socratic-2", kind="SOCRATIC", title="한 번 더 확인하기",
            text=sq[1] if len(sq) > 1 else sq[0], choices=["근거를 찾았다", "다시 설명 필요"],
            branch_map={"근거를 찾았다": f"{qid}-retest", "다시 설명 필요": f"{qid}-video"},
            misconception_path=misconception
        ).model_dump(),
        Segment(
            segment_id=f"{qid}-retest", kind="RETEST", title="전이 재시험",
            text="같은 오개념을 다른 표현으로 다시 확인합니다.", choices=retest,
            correct_answer=bundle.retest_correct_answer or bundle.correct_answer, misconception_path=misconception
        ).model_dump(),
    ]

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
    if not token or token != row["teacher_token"]:
        raise HTTPException(403, "teacher token required")


@classroom_tutor_router.get("/api/health")
def health():
    return {"ok": True, "version": APP_VERSION, "db": str(DB_PATH)}


@classroom_tutor_router.post("/api/sessions")
def create_session(req: SessionCreate, _: None = Depends(require_owner)):
    session_id = f"cls_{uuid.uuid4().hex[:10]}"
    teacher_token = uuid.uuid4().hex
    join_token = uuid.uuid4().hex[:12]
    lesson = [x.model_dump() for x in (req.lesson or DEFAULT_LESSON)]
    state = session_state(lesson[0]["segment_id"] if lesson else None)
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
        "board_url": f"/tutor/board/{session_id}?teacher_token={teacher_token}",
        "teacher_url": f"/tutor/teacher/{session_id}?teacher_token={teacher_token}",
        "join_url": f"/tutor/join/{session_id}?token={join_token}",
    }


@classroom_tutor_router.get("/api/sessions/{session_id}")
def get_session(
    session_id: str,
    teacher_token: str | None = None,
    join_token: str | None = None,
):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        if teacher_token != row["teacher_token"] and join_token != row["join_token"]:
            raise HTTPException(403, "session token required")
        return row_to_session(row)




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
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
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
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
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
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
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
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        if req.join_token != row["join_token"]:
            raise HTTPException(403, "invalid join token")
        lesson = json_load(row["lesson_json"]) or []
        segment = next((x for x in lesson if x["segment_id"] == req.segment_id), None)
        if not segment:
            raise HTTPException(400, "unknown segment")
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
        event_id = emit(
            conn,
            session_id,
            "STUDENT_RESPONSE",
            {"response_id": rid, "segment_id": req.segment_id, "answer": req.answer},
        )
        branch_to = (segment.get("branch_map") or {}).get(req.answer)
        return {
            "ok": True,
            "event_id": event_id,
            "response_id": rid,
            "branch_candidate": branch_to,
            "correct": None if segment.get("correct_answer") is None else req.answer == segment.get("correct_answer"),
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
function youtubeEmbed(url){try{let u=new URL(url);if(u.hostname==='youtu.be')return 'https://www.youtube-nocookie.com/embed/'+encodeURIComponent(u.pathname.slice(1));if(u.hostname.endsWith('youtube.com')){let id=u.searchParams.get('v');if(id)return 'https://www.youtube-nocookie.com/embed/'+encodeURIComponent(id);}}catch(e){}return null;}
"""


def board_html(session_id: str, teacher_token: str) -> str:
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no'><title>EduRecover Board</title><style>{COMMON_CSS}</style></head>
<body><div class='top'><div><b>EduRecover 전자칠판</b> <span class='badge' id='conn'>연결중</span></div><div><span class='badge' id='mode'></span> <button class='btn' onclick='goFull()'>전체화면</button></div></div>
<div class='grid'><main class='stage'><div class='card'><div class='muted' id='kind'></div><div class='big' id='title'>불러오는 중...</div><p id='text' style='font-size:clamp(18px,2.1vw,32px)'></p><div id='media'></div><div id='choices'></div></div><div class='card' id='whiteWrap' style='display:none'><canvas id='whiteboard' class='board'></canvas></div></main>
<aside class='side'><div class='card'><b>교사 제어</b><div class='controls'><button class='primary' onclick="cmd('PLAY')">재생/TTS</button><button onclick="cmd('PAUSE')">일시정지</button><button onclick="cmd('PREV')">이전</button><button onclick="cmd('NEXT')">다음</button><button onclick='toggleBoard()'>판서 전환</button><button class='danger' onclick="cmd('STOP')">전체중지</button></div></div>
<div class='card'><b>학생 참가</b><div id='qr' style='margin-top:10px'></div><div class='muted' id='joinText'></div></div>
<div class='card'><b>응답 현황</b><div id='results'>아직 응답 없음</div></div>
<div class='card'><b>장치 capability</b><pre id='caps' style='white-space:pre-wrap'></pre></div></aside></div>
<script src='https://cdn.jsdelivr.net/npm/qrcodejs@1.0.0/qrcode.min.js'></script><script>{COMMON_JS}
const SID={json.dumps(session_id)}, TT={json.dumps(teacher_token)};let S=null,lastEvent=0,videoPausedOnce=false;
async function refresh(){{try{{S=await api(`/tutor/api/sessions/${{SID}}?teacher_token=${{encodeURIComponent(TT)}}`);q('#conn').textContent='연결됨';q('#mode').textContent=S.launch_mode+' / '+S.connection_method;render();await loadResults();}}catch(e){{q('#conn').textContent='오프라인';}}}}
function seg(){{return (S?.lesson||[]).find(x=>x.segment_id===S?.state?.current_segment_id)||null}}
function render(){{let x=seg();if(!x)return;q('#kind').textContent=x.kind+(x.misconception_path?' · 오답원인: '+x.misconception_path:'');q('#title').textContent=x.title;q('#text').textContent=x.text||'';let m=q('#media');m.innerHTML='';videoPausedOnce=false;if(x.kind==='VIDEO'&&x.media_url){{let yt=youtubeEmbed(x.media_url);if(yt){{m.innerHTML=`<iframe title='수업 영상' src="${{esc(yt)}}" style='width:100%;height:min(56vw,520px);border:0;border-radius:12px' allow='autoplay; encrypted-media; picture-in-picture' allowfullscreen></iframe>`;}}else{{m.innerHTML=`<video id='vid' controls playsinline src="${{esc(x.media_url)}}"></video>`;let v=q('#vid');if(x.pause_at_sec) v.addEventListener('timeupdate',()=>{{if(!videoPausedOnce&&v.currentTime>=x.pause_at_sec){{videoPausedOnce=true;v.pause();q('#choices').innerHTML=`<div class='card'><b>생각해 볼 질문</b><p>${{esc(x.text)}}</p></div>`;}}}});}}}}
let c=q('#choices');c.innerHTML='';if(['SOCRATIC','RETEST'].includes(x.kind)){{c.innerHTML=`<div class='muted'>학생은 자신의 기기에서 응답합니다.</div>`;}}
if(S.state.playback==='PLAYING'){{if(q('#vid'))q('#vid').play().catch(()=>{{}});else if(x.text)speak(x.text);}} else if(S.state.playback==='PAUSED'){{q('#vid')?.pause();}}
}}
async function cmd(a){{await api(`/tutor/api/sessions/${{SID}}/command?teacher_token=${{encodeURIComponent(TT)}}`,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{action:a}})}});refresh();}}
async function loadResults(){{let x=seg();if(!x)return;let r=await api(`/tutor/api/sessions/${{SID}}/results?teacher_token=${{encodeURIComponent(TT)}}&segment_id=${{encodeURIComponent(x.segment_id)}}`);let total=(r.totals[0]?.n)||0;if(!total){{q('#results').textContent='아직 응답 없음';return;}}q('#results').innerHTML=r.counts.map(z=>`<div style='margin:8px 0'><div>${{esc(z.answer)}} · ${{z.n}}명</div><div class='bar'><i style='width:${{Math.round(z.n/total*100)}}%'></i></div></div>`).join('');}}
function goFull(){{document.documentElement.requestFullscreen?.();}}
function toggleBoard(){{let w=q('#whiteWrap');w.style.display=w.style.display==='none'?'block':'none';}}
q('#caps').textContent=JSON.stringify(capabilities(),null,2);
const joinUrl=`${{location.origin}}/tutor/join/${{SID}}?token=${{encodeURIComponent(SID)}}`; // fallback text replaced after session fetch
async function makeQR(){{let s=await api(`/tutor/api/sessions/${{SID}}?teacher_token=${{encodeURIComponent(TT)}}`);let url=`${{location.origin}}/tutor/join/${{SID}}?token=${{encodeURIComponent(s.join_token)}}`;q('#joinText').innerHTML=`참가 주소: <span class='muted'>${{esc(url)}}</span>`;try{{new QRCode(q('#qr'),{{text:url,width:190,height:190}});}}catch(e){{q('#qr').textContent='QR 라이브러리 로드 실패 — 위 주소 또는 참가코드를 사용하세요.';}}}}
// pointer-based whiteboard
let cv=q('#whiteboard'),ctx=cv.getContext('2d'),drawing=false,scale=1;function sizeCanvas(){{let r=cv.getBoundingClientRect();let d=devicePixelRatio||1;cv.width=Math.max(1,Math.floor(r.width*d));cv.height=Math.max(1,Math.floor(r.height*d));ctx.setTransform(d,0,0,d,0,0);ctx.lineCap='round';ctx.lineWidth=3;ctx.strokeStyle='#111';}};window.addEventListener('resize',sizeCanvas);setTimeout(sizeCanvas,100);
cv.addEventListener('pointerdown',e=>{{drawing=true;cv.setPointerCapture(e.pointerId);ctx.beginPath();let r=cv.getBoundingClientRect();ctx.moveTo(e.clientX-r.left,e.clientY-r.top);}});cv.addEventListener('pointermove',e=>{{if(!drawing)return;let r=cv.getBoundingClientRect();ctx.lineTo(e.clientX-r.left,e.clientY-r.top);ctx.stroke();}});['pointerup','pointercancel','pointerleave'].forEach(ev=>cv.addEventListener(ev,()=>drawing=false));
makeQR();refresh();setInterval(refresh,1500);
</script></body></html>"""


def teacher_html(session_id: str, teacher_token: str) -> str:
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>EduRecover Teacher</title><style>{COMMON_CSS}</style></head><body>
<div class='top'><b>EduRecover 교사 콘솔</b><span class='badge'>{session_id}</span></div><main style='max-width:960px;margin:auto;padding:18px'><div class='card'><b>전자칠판 열기</b><p class='muted'>전자칠판 자체 브라우저/OPS에서는 아래 주소를 직접 열고, 캐스트/HDMI 환경에서는 이 화면을 전자칠판으로 미러링합니다.</p><a class='btn primary' style='display:inline-block;text-decoration:none' target='_blank' href='/tutor/board/{session_id}?teacher_token={teacher_token}'>전자칠판 화면 열기</a></div>
<div class='card'><b>수업 단계</b><div id='segments'></div></div><div class='card'><b>실시간 응답</b><div id='res'></div></div></main><script>{COMMON_JS}
const SID={json.dumps(session_id)},TT={json.dumps(teacher_token)};let S;
async function load(){{S=await api(`/tutor/api/sessions/${{SID}}?teacher_token=${{encodeURIComponent(TT)}}`);q('#segments').innerHTML=(S.lesson||[]).map(x=>`<button class='choice' onclick="go('${{x.segment_id}}')"><b>${{esc(x.title)}}</b><br><span class='muted'>${{esc(x.kind)}} · ${{esc(x.text)}}</span></button>`).join('');let r=await api(`/tutor/api/sessions/${{SID}}/results?teacher_token=${{encodeURIComponent(TT)}}`);q('#res').innerHTML=(r.counts.length?r.counts.map(x=>`<div>${{esc(x.segment_id)}} / ${{esc(x.answer)}} : ${{x.n}}명</div>`).join(''):'아직 응답 없음');}}
async function go(id){{await api(`/tutor/api/sessions/${{SID}}/command?teacher_token=${{encodeURIComponent(TT)}}`,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{action:'GOTO',segment_id:id}})}});load();}}
load();setInterval(load,2000);</script></body></html>"""


def student_html(session_id: str, token: str) -> str:
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>EduRecover Student</title><style>{COMMON_CSS}</style></head><body>
<div class='top'><b>EduRecover 학생 응답</b><span class='badge'>{session_id}</span></div><main style='max-width:680px;margin:auto;padding:16px'><div class='card'><label>가명 또는 번호</label><input id='alias' placeholder='예: S12' maxlength='40'></div><div class='card'><div class='muted' id='kind'></div><h1 id='title'>수업 연결중...</h1><p id='text' style='font-size:20px'></p><div id='choices'></div><label>확신도(0~100, 선택)</label><input id='conf' type='number' min='0' max='100' placeholder='예: 70'><label>왜 그렇게 생각했는지(선택)</label><textarea id='reason' rows='3'></textarea><div id='msg' class='muted'></div></div></main><script>{COMMON_JS}
const SID={json.dumps(session_id)}, JT={json.dumps(token)};let S=null,lastSeg='';
function seg(){{return (S?.lesson||[]).find(x=>x.segment_id===S?.state?.current_segment_id)||null}}
async function load(){{try{{S=await api(`/tutor/api/sessions/${{SID}}?join_token=${{encodeURIComponent(JT)}}`);let x=seg();if(!x)return;q('#kind').textContent=x.kind;q('#title').textContent=x.title;q('#text').textContent=x.kind==='VIDEO'?'전자칠판 설명을 보고 질문이 나오면 답하세요.':x.text;if(lastSeg!==x.segment_id){{lastSeg=x.segment_id;q('#msg').textContent='';q('#choices').innerHTML=(x.choices||[]).map(c=>`<button class='choice' onclick='send(${{JSON.stringify(c)}})'>${{esc(c)}}</button>`).join('')||`<div class='muted'>현재는 응답 단계가 아닙니다.</div>`;}}}}catch(e){{q('#msg').textContent='연결이 끊겼습니다. 다시 시도 중...';}}}}
async function send(ans){{let a=q('#alias').value.trim();if(!a){{q('#msg').textContent='가명/번호를 먼저 입력하세요.';return;}}let x=seg();let body={{student_alias:a,segment_id:x.segment_id,answer:ans,join_token:JT,confidence:q('#conf').value?Number(q('#conf').value):null,reason_text:q('#reason').value||null}};try{{let r=await api(`/tutor/api/sessions/${{SID}}/respond`,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(body)}});q('#msg').textContent='응답을 보냈습니다.'+(r.correct===true?' ✓':r.correct===false?' — 다시 생각해 보세요.':'');}}catch(e){{q('#msg').textContent='응답 전송 실패';}}}}
load();setInterval(load,1500);</script></body></html>"""


@classroom_tutor_router.get("/board/{session_id}", response_class=HTMLResponse)
def board(session_id: str, teacher_token: str):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
    return board_html(session_id, teacher_token)


@classroom_tutor_router.get("/teacher/{session_id}", response_class=HTMLResponse)
def teacher(session_id: str, teacher_token: str):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        require_teacher(row, teacher_token)
    return teacher_html(session_id, teacher_token)


@classroom_tutor_router.get("/join/{session_id}", response_class=HTMLResponse)
def join(session_id: str, token: str):
    with db() as conn:
        row = get_session_or_404(conn, session_id)
        if token != row["join_token"]:
            raise HTTPException(403, "invalid join token")
    return student_html(session_id, token)


HOME_HTML = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>EduRecover AI 튜터</title><style>{COMMON_CSS}</style></head><body>
<div class='top'><div><b>EduRecover 산파식 AI 튜터·시청각 수업</b> <span class='badge'>v{APP_VERSION}</span></div><a class='btn' href='/owner' style='text-decoration:none'>운영 플랫폼으로</a></div>
<main style='max-width:980px;margin:auto;padding:18px'>
<div class='card'><h2>새 튜터 수업 만들기</h2><p class='muted'>오답원인 확인 → 짧은 영상 또는 한국어 TTS → 산파식 질문 → 분기 → 재시험 순서로 진행합니다.</p>
<label>수업 제목</label><input id='title' value='산파식 AI 튜터 수업'><label>교실 가명</label><input id='room' value='CLASS-A'>
<div style='display:grid;grid-template-columns:1fr 1fr;gap:10px'><div><label>전자칠판 실행 방식</label><select id='launch'><option>DIRECT_BOARD_WEB</option><option>WINDOWS_OPS_WEB</option><option>CAST</option><option>WIRED_MIRROR</option><option>TEACHER_DEVICE</option></select></div><div><label>연결 방식</label><select id='conn'><option>BROWSER</option><option>MIRACAST</option><option>AIRPLAY</option><option>GOOGLE_CAST</option><option>HDMI</option><option>USB_C</option><option>VENDOR_CAST</option><option>OTHER</option></select></div></div></div>
<div class='card'><h2>문항·시청각 자료</h2>
<label>문항</label><textarea id='question' rows='3'>다음 조건을 자기 말로 다시 설명해 보세요.</textarea>
<label>선택지 — 한 줄에 하나</label><textarea id='choices' rows='4'>A. 첫 번째 선택지\nB. 두 번째 선택지\nC. 세 번째 선택지\nD. 네 번째 선택지</textarea>
<label>정답</label><input id='answer' value='B. 두 번째 선택지'>
<label>오답 원인</label><input id='misconception' value='문제에 없는 조건을 스스로 덧붙여 해석함'>
<label>짧은 설명·TTS 원고</label><textarea id='explanation' rows='3'>정답을 바로 말하지 않고, 문제에서 실제로 주어진 조건과 머릿속에서 덧붙인 조건을 나누어 확인합니다.</textarea>
<label>영상 주소 — 선택사항</label><input id='videoUrl' placeholder='직접 재생 가능한 MP4·WebM 또는 YouTube 주소'><label>자동 멈춤 시점(초)</label><input id='pauseAt' type='number' min='0' step='0.5' value='8'>
<label>산파식 질문</label><textarea id='socratic' rows='2'>문제에서 실제로 주어진 조건은 무엇이고, 내가 추가로 생각한 조건은 무엇인가요?</textarea>
<button class='btn primary' onclick='createSession()'>AI 튜터·시청각 수업 만들기</button><div id='out'></div></div>
<div class='card'><b>개인정보 원칙</b><p class='muted'>학생 실명 대신 가명 또는 번호만 사용합니다. 전자칠판이 표시 전용이어도 학생은 참가 주소나 QR로 응답할 수 있습니다. 영상 주소를 비워 두면 브라우저의 한국어 음성합성으로 설명합니다.</p></div>
</main><script>{COMMON_JS}
async function createSession(){{
  const choices=q('#choices').value.split(/\\n+/).map(x=>x.trim()).filter(Boolean), answer=q('#answer').value.trim();
  const misconception=q('#misconception').value.trim(), explanation=q('#explanation').value.trim(), question=q('#question').value.trim(), socratic=q('#socratic').value.trim();
  const lesson=[
    {{segment_id:'problem',kind:'EXPLAIN',title:'문제와 조건 확인',text:question+'\\n'+choices.join(' | '),misconception_path:misconception}},
    {{segment_id:'media',kind:'VIDEO',title:'오답원인 짧은 설명',text:explanation,media_url:q('#videoUrl').value.trim()||null,pause_at_sec:Number(q('#pauseAt').value||0)||null,misconception_path:misconception}},
    {{segment_id:'socratic',kind:'SOCRATIC',title:'스스로 확인하기',text:socratic,choices:['조건을 다시 확인했다','아직 헷갈린다'],correct_answer:'조건을 다시 확인했다',branch_map:{{'조건을 다시 확인했다':'retest','아직 헷갈린다':'media'}},misconception_path:misconception}},
    {{segment_id:'retest',kind:'RETEST',title:'재시험',text:question,choices:choices,correct_answer:answer,branch_map:{{}},misconception_path:misconception}}
  ];
  const body={{title:q('#title').value,room_alias:q('#room').value,launch_mode:q('#launch').value,connection_method:q('#conn').value,institution_type:'SCHOOL',privacy_mode:'ALIAS_ONLY',lesson}};
  q('#out').innerHTML="<p class='muted'>수업을 만드는 중입니다...</p>";
  try{{let r=await api('/tutor/api/sessions',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(body)}});let join=location.origin+r.join_url;q('#out').innerHTML=`<div class='card' style='margin-top:12px'><a class='btn primary' target='_blank' href='${{r.board_url}}'>전자칠판 열기</a> <a class='btn' target='_blank' href='${{r.teacher_url}}'>교사 콘솔</a><p class='muted'>학생 참가: <a href='${{r.join_url}}' target='_blank'>${{esc(join)}}</a></p></div>`;}}catch(e){{q('#out').innerHTML=`<p style='color:#fb7185'>수업 생성 실패: ${{esc(e.message)}}</p>`;}}
}}
</script></body></html>"""


@classroom_tutor_router.get("", response_class=HTMLResponse, include_in_schema=False)
def home(_: None = Depends(require_owner)):
    return HTMLResponse(HOME_HTML, headers={"Cache-Control": "no-store", "Referrer-Policy": "same-origin"})
