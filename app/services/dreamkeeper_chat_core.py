"""특허기술 전용 챗봇 핵심부: 맥락 장부 확장과 대화 한 차례의 검사 단계들.

이 모듈은 ``DreamkeeperEngine``(기존 11개 파일 중 하나)을 고치지 않고 그 위에
얹는 추가 계층이다. 엔진과 같은 SQLite 파일에 새 표 두 개를 만든다.

- ``dk_chat_items``  : 정책세대·원문 자격·대화 차례·출력(영수증)·격리 보관물
- ``dk_chat_events`` : 늦은 응답 거부, 엔진 격리 등 영구 기록(추가만 가능)

두 표 모두 SQLite 방아쇠(트리거)로 행 삭제를 막는다. 격리 보관물과 출력의
본문(``body``) 열은 수정도 막는다. 여러 줄을 쓰는 저장은 엔진과 같은 잠금과
``BEGIN IMMEDIATE`` 트랜잭션 하나 안에서 모두 성공하거나 모두 취소된다.

엔진 비공개 함수 의존(최소화, 이유):
- ``engine._tx()``  : 엔진과 같은 재진입 잠금·같은 연결(``:memory:`` 데이터베이스는
  연결 하나에만 존재)·같은 ``BEGIN IMMEDIATE`` 규칙을 써야 장부 확장 표와 엔진 표를
  한 트랜잭션 안에서 일관되게 읽고 쓸 수 있다.
- ``engine._load()``, ``engine._all()`` : 위 트랜잭션 안에서 엔진 표(dk_tasks,
  dk_sources)를 **읽기만** 하기 위해 쓴다. 공개 ``snapshot()``은 자체 트랜잭션을
  열기 때문에 중첩할 수 없다. 엔진 표에 대한 쓰기는 오직 공개 함수
  (begin_run, model_port, put_source)로만 한다.

이 모듈은 외부 모델을 호출하지 않는다. 생성·검증 모델은 호출 가능한 함수로
주입된다. 참·거짓 표시값은 인증이나 의미 증명이 아니다. 기본 일치도 계산과
기본 목표 검사는 글자·형태 기반의 근사이며 의미 이해를 보장하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import time
import unicodedata
from typing import Any, Callable, Mapping
from uuid import uuid4

from app.services.dreamkeeper_engine import DreamkeeperEngine, EngineHold, RunBinding

CONFIG_SCHEMA = "edurecover.dreamkeeper_chat.v1"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "dreamkeeper_chat.json"

# 원문 정정 뒤 결과의 세 분류
UNRESOLVED = "DEPENDENCY_UNRESOLVED"   # 의존 미확정(영수증 없음·불완전)
AFFECTED = "AFFECTED"                  # 영향 있음(옛 판본 사용·무효 원문 사용)
NOT_DEPENDENT = "NOT_DEPENDENT"        # 의존하지 않음
CLASS_LABELS = {UNRESOLVED: "의존 미확정", AFFECTED: "영향 있음", NOT_DEPENDENT: "의존하지 않음"}
HOLD_CLASSES = frozenset({UNRESOLVED, AFFECTED})

# 격리 후보 시험 관문
GATES = ("domain_allowed", "eligibility_and_policy_generation", "required_receipts",
         "io_units_and_operation", "resume_condition")


class ChatHold(RuntimeError):
    """거부된 진행. 기존 기록은 지우지 않는다."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _copy(value: Any) -> Any:
    return json.loads(_json(value))


def _text(value: Any, label: str = "text") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"nonempty {label} required")
    return value


def _hash(value: Any) -> str:
    return sha256(_json(value).encode()).hexdigest()


# ---------------------------------------------------------------- 설정 파일
@dataclass(frozen=True)
class ChatConfig:
    sentence_support_threshold: float
    max_failures_per_engine: int
    known_purposes: tuple[str, ...]
    allowed_domains: tuple[str, ...]
    domain_priority_weight: Mapping[str, float]
    max_question_chars: int
    max_response_chars: int


def _unique_texts(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a nonempty list")
    for item in value:
        _text(item, label)
    if len(set(value)) != len(value):
        raise ValueError(f"{label} must be unique")
    return tuple(value)


def _int_in(value: Any, low: int, high: int, label: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in [{low}, {high}]")
    return value


def parse_config(raw: Any) -> ChatConfig:
    """설정값 검사. 알 수 없는 키, 범위 밖 값, 참·거짓 값의 숫자 위장을 거부한다."""
    if not isinstance(raw, dict):
        raise ValueError("config object required")
    required = {"schema", "sentence_support_threshold", "max_failures_per_engine",
                "known_purposes", "allowed_domains", "domain_priority_weight",
                "max_question_chars", "max_response_chars"}
    if set(raw) != required:
        raise ValueError(f"config keys must be exactly {sorted(required)}")
    if raw["schema"] != CONFIG_SCHEMA:
        raise ValueError("unsupported config schema")
    threshold = raw["sentence_support_threshold"]
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or \
            not math.isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError("sentence_support_threshold must be a number in (0, 1]")
    domains = _unique_texts(raw["allowed_domains"], "allowed_domains")
    weights = raw["domain_priority_weight"]
    if not isinstance(weights, dict) or set(weights) != set(domains):
        raise ValueError("domain_priority_weight must cover exactly allowed_domains")
    for weight in weights.values():
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or \
                not math.isfinite(weight) or weight <= 0:
            raise ValueError("domain weights must be positive numbers")
    return ChatConfig(
        sentence_support_threshold=float(threshold),
        max_failures_per_engine=_int_in(raw["max_failures_per_engine"], 1, 20,
                                        "max_failures_per_engine"),
        known_purposes=_unique_texts(raw["known_purposes"], "known_purposes"),
        allowed_domains=domains,
        domain_priority_weight={key: float(value) for key, value in weights.items()},
        max_question_chars=_int_in(raw["max_question_chars"], 1, 100_000, "max_question_chars"),
        max_response_chars=_int_in(raw["max_response_chars"], 1, 1_000_000, "max_response_chars"),
    )


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> ChatConfig:
    with open(path, encoding="utf-8") as handle:
        return parse_config(json.load(handle))


# ------------------------------------------------- 교체 가능한 기본 검사 함수
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+|\n+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_STRIP = re.compile(r"[\s\W_]+", re.UNICODE)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_END.split(text) if part and part.strip()]


def _bigrams(text: str) -> set[str]:
    compact = _STRIP.sub("", _norm(text))
    if len(compact) == 1:
        return {compact}
    return {compact[i:i + 2] for i in range(len(compact) - 1)}


def default_support_score(sentence: str, evidence_texts: list[str]) -> float:
    """문장 근거 일치도(0~1)의 기본 근사.

    1) 문장 속 숫자가 근거에 하나라도 없으면 0.
    2) 문장 글자 두 개 묶음 가운데 근거 전체에 들어 있는 비율.
    의미 일치를 보장하지 않으며 교체 가능하도록 분리했다.
    """
    evidence = " ".join(evidence_texts)
    numbers = set(_NUMBER.findall(_norm(evidence)))
    if any(number not in numbers for number in _NUMBER.findall(_norm(sentence))):
        return 0.0
    grams = _bigrams(sentence)
    if not grams:
        return 0.0
    return len(grams & _bigrams(evidence)) / len(grams)


_ABANDON_PATTERNS = (
    re.compile(r"(다른|나머지|기존|이전|원래)\s*목표\S*\s*(?:는|은|를|을|도)?\s*"
               r"(?:모두|전부|다)?\s*(폐기|삭제|무시|제외|생략|포기|중단|버리|버린|빼)"),
    re.compile(r"목표\S*\s*(?:를|을)\s*(폐기|삭제|포기|버리|버린|무시)"),
    re.compile(r"우선순위\S*.{0,40}(나머지|다른)\s*(목표|작업|일)\S*\s*(?:는|은|를|을)?\s*"
               r"(삭제|폐기|제외|생략|무시|빼)"),
    re.compile(r"\b(drop|abandon|ignore|discard|delete|remove)\s+(?:all\s+)?(?:the\s+)?"
               r"(?:other|remaining|previous|original)?\s*goals?\b", re.IGNORECASE),
)


def default_goal_checker(check: dict) -> dict:
    """목표 이탈 기본 검사(교체 가능).

    입력: goals, priorities, goal_revision, response(text, goal_ids, declared_goals,
    goal_changes). 응답이 목표를 빠뜨리거나 다른 목표를 들이거나, 장부와 다른 목표
    내용을 선언하거나, 목표 변경·폐기를 담으면 실패. 허락된 변경은 사용자 인증을
    거친 ``engine.revise_task``로만 장부에 반영되므로 응답 안의 변경 요구는 허락된
    변경이 아니다. 우선순위 지시는 다른 목표 삭제 권한이 아니다.
    """
    goals = check["goals"]
    response = check["response"]
    reasons = []
    goal_ids = response.get("goal_ids")
    if not isinstance(goal_ids, list) or set(goal_ids) != set(goals) or len(goal_ids) != len(goals):
        reasons.append("goal_ids must account for every ledger goal and no other")
    declared = response.get("declared_goals")
    if declared is not None:
        if not isinstance(declared, dict):
            reasons.append("declared_goals must be a mapping")
        else:
            for gid, text in declared.items():
                if gid not in goals:
                    reasons.append(f"declared unknown goal {gid}")
                elif not isinstance(text, str) or \
                        " ".join(_norm(text).split()) != " ".join(_norm(goals[gid]).split()):
                    reasons.append(f"declared goal {gid} differs from ledger goal")
    if response.get("goal_changes"):
        reasons.append("response proposes goal changes without authenticated user revision")
    text = response.get("text", "")
    for pattern in _ABANDON_PATTERNS:
        if pattern.search(_norm(text)):
            reasons.append("response text expresses abandoning goals")
            break
    return {"ok": not reasons, "reasons": reasons}


def default_priority(item_state: dict, rescore: float, config: ChatConfig) -> float:
    return round(rescore * config.domain_priority_weight[item_state["domain"]], 6)


def _resume_rescore(core: "DreamkeeperChatCore", body: dict, state: dict, current: dict) -> tuple[bool, float]:
    """기본 재개 조건: 현재 판본 근거로 다시 계산한 일치도가 현재 임계값 이상."""
    evidence = []
    for sid, ref in state["receipt"]["sources"].items():
        source = current["sources"].get(sid)
        if source is None:
            return False, 0.0
        for fname in ref["fields"]:
            evidence.append(_json(source["fields"][fname]["value"]))
    score = core._score(body["sentence"], evidence)
    return score is not None and score >= core.config.sentence_support_threshold, score or 0.0


DEFAULT_RESUME_EVALUATORS: dict[str, Callable] = {"rescore_meets_threshold": _resume_rescore}


# ---------------------------------------------------------- 주입 명세
@dataclass(frozen=True)
class EngineSpec:
    """일하는 모델. generate(입력 dict) -> 응답 dict. 비밀 열쇠는 함수 안에서
    환경변수로만 읽어야 하며 이 핵심부는 열쇠를 다루지 않는다."""
    name: str
    family: str
    generate: Callable[[dict], dict] = field(repr=False)


@dataclass(frozen=True)
class VerifierSpec:
    """다른 계열 검증 모델. verify({"evidence":…, "response":…}) -> {"supported": bool}."""
    name: str
    family: str
    verify: Callable[[dict], dict] = field(repr=False)


def same_family(a: str, b: str) -> bool:
    return _norm(_text(a, "family")).strip() == _norm(_text(b, "family")).strip()


# ------------------------------------------------------------- 핵심부
class DreamkeeperChatCore:
    TABLE_SQL = (
        "CREATE TABLE IF NOT EXISTS dk_chat_items (owner TEXT NOT NULL, logical_id TEXT NOT NULL,"
        " task_id TEXT NOT NULL, kind TEXT NOT NULL, item TEXT NOT NULL, body TEXT NOT NULL,"
        " state TEXT NOT NULL, created REAL NOT NULL,"
        " PRIMARY KEY(owner, logical_id, task_id, kind, item))",
        "CREATE TABLE IF NOT EXISTS dk_chat_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
        " owner TEXT NOT NULL, logical_id TEXT NOT NULL, task_id TEXT NOT NULL,"
        " kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL)",
        "CREATE TRIGGER IF NOT EXISTS dk_chat_items_no_delete BEFORE DELETE ON dk_chat_items"
        " BEGIN SELECT RAISE(ABORT, 'dreamkeeper chat ledger rows cannot be deleted'); END",
        "CREATE TRIGGER IF NOT EXISTS dk_chat_items_fixed_body BEFORE UPDATE OF"
        " owner, logical_id, task_id, kind, item, body, created ON dk_chat_items"
        " BEGIN SELECT RAISE(ABORT, 'dreamkeeper chat ledger body is immutable'); END",
        "CREATE TRIGGER IF NOT EXISTS dk_chat_events_no_delete BEFORE DELETE ON dk_chat_events"
        " BEGIN SELECT RAISE(ABORT, 'dreamkeeper chat events are append-only'); END",
        "CREATE TRIGGER IF NOT EXISTS dk_chat_events_no_update BEFORE UPDATE ON dk_chat_events"
        " BEGIN SELECT RAISE(ABORT, 'dreamkeeper chat events are append-only'); END",
    )

    def __init__(self, engine: DreamkeeperEngine, config: ChatConfig, *,
                 scorer: Callable[[str, list[str]], float] = default_support_score,
                 goal_checker: Callable[[dict], dict] = default_goal_checker,
                 resume_evaluators: Mapping[str, Callable] | None = None,
                 priority_fn: Callable[[dict, float, ChatConfig], float] = default_priority):
        if not isinstance(engine, DreamkeeperEngine) or not isinstance(config, ChatConfig):
            raise TypeError("DreamkeeperEngine and ChatConfig required")
        self.engine = engine
        self.owner_id = engine.owner_id
        self.config = config
        self.scorer = scorer
        self.goal_checker = goal_checker
        self.resume_evaluators = dict(DEFAULT_RESUME_EVALUATORS if resume_evaluators is None
                                      else resume_evaluators)
        self.priority_fn = priority_fn
        with self._tx() as db:
            for sql in self.TABLE_SQL:
                db.execute(sql)

    # ---------------------------------------------------------- 저장 기초
    def _tx(self):
        return self.engine._tx()  # 비공개 의존: 모듈 설명 참고

    def _get(self, db, logical, task, kind, item) -> tuple[dict, dict] | None:
        row = db.execute("SELECT body, state FROM dk_chat_items WHERE owner=? AND logical_id=?"
                         " AND task_id=? AND kind=? AND item=?",
                         (self.owner_id, logical, task, kind, item)).fetchone()
        return None if row is None else (json.loads(row[0]), json.loads(row[1]))

    def _items(self, db, logical, task, kind) -> dict[str, tuple[dict, dict]]:
        rows = db.execute("SELECT item, body, state FROM dk_chat_items WHERE owner=? AND"
                          " logical_id=? AND task_id=? AND kind=? ORDER BY created, item",
                          (self.owner_id, logical, task, kind)).fetchall()
        return {item: (json.loads(body), json.loads(state)) for item, body, state in rows}

    def _insert(self, db, logical, task, kind, item, body, state) -> None:
        db.execute("INSERT INTO dk_chat_items VALUES(?,?,?,?,?,?,?,?)",
                   (self.owner_id, logical, task, kind, item, _json(body), _json(state), time.time()))

    def _update(self, db, logical, task, kind, item, state) -> None:
        cur = db.execute("UPDATE dk_chat_items SET state=? WHERE owner=? AND logical_id=? AND"
                         " task_id=? AND kind=? AND item=?",
                         (_json(state), self.owner_id, logical, task, kind, item))
        if cur.rowcount != 1:
            raise ChatHold("ledger row missing")

    def _event(self, db, logical, task, kind, payload) -> None:
        db.execute("INSERT INTO dk_chat_events(owner,logical_id,task_id,kind,payload,created)"
                   " VALUES(?,?,?,?,?,?)",
                   (self.owner_id, logical, task, kind, _json(payload), time.time()))

    def _engine_state(self, db, logical, task) -> dict:
        """엔진 표 읽기 전용 조회(같은 트랜잭션)."""
        try:
            state = self.engine._load(db, "tasks", logical, task)
        except EngineHold as exc:
            raise ChatHold(str(exc)) from exc
        state["sources"] = self.engine._all(db, "sources", logical, task)
        return state

    def _meta(self, db, logical, task) -> dict:
        found = self._get(db, logical, task, "meta", "meta")
        if found is None:
            self._engine_state(db, logical, task)  # 엔진 업무가 있어야 장부 확장 가능
            state = {"policy_generation": 1, "engines": {}}
            self._insert(db, logical, task, "meta", "meta", {"logical_id": logical, "task_id": task},
                         state)
            self._event(db, logical, task, "CHAT_LEDGER_CREATED", state)
            return state
        return found[1]

    def _score(self, sentence: str, evidence: list[str]) -> float | None:
        try:
            value = self.scorer(sentence, evidence)
        except Exception:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or \
                not math.isfinite(value) or not 0 <= value <= 1:
            return None
        return float(value)

    # ------------------------------------------- 2 목적별 자격·정책세대
    def set_source_policy(self, logical: str, task: str, *, source_id: str,
                          allowed_purposes: list[str], valid: bool, user_receipt: str) -> dict:
        """호스트가 인증한 원문 허용목적·유효상태 기록. 현재 정책세대로 묶인다."""
        _text(source_id); _text(user_receipt)
        purposes = _unique_texts(allowed_purposes, "allowed_purposes")
        if any(p not in self.config.known_purposes for p in purposes):
            raise ValueError("allowed purpose not in configured known_purposes")
        if type(valid) is not bool:
            raise ValueError("explicit validity required")
        with self._tx() as db:
            meta = self._meta(db, logical, task)
            if source_id not in self._engine_state(db, logical, task)["sources"]:
                raise ChatHold("source not in engine ledger")
            state = {"source_id": source_id, "allowed_purposes": list(purposes), "valid": valid,
                     "policy_generation": meta["policy_generation"], "user_receipt": user_receipt}
            existing = self._get(db, logical, task, "source_policy", source_id)
            if existing is None:
                self._insert(db, logical, task, "source_policy", source_id,
                             {"source_id": source_id}, state)
            else:
                self._update(db, logical, task, "source_policy", source_id, state)
            self._event(db, logical, task, "SOURCE_POLICY_SET",
                        {"before": existing[1] if existing else None, "after": state})
            self._reconcile(db, logical, task, "source policy changed")
            return state

    def advance_policy_generation(self, logical: str, task: str, *, reason: str,
                                  user_receipt: str) -> int:
        """정책세대를 올린다. 옛 세대에 묶인 원문 자격은 다시 기록될 때까지 근거 불가."""
        _text(reason); _text(user_receipt)
        with self._tx() as db:
            meta = self._meta(db, logical, task)
            before = meta["policy_generation"]
            meta["policy_generation"] += 1
            self._update(db, logical, task, "meta", "meta", meta)
            self._event(db, logical, task, "POLICY_GENERATION_ADVANCED", {
                "before": before, "after": meta["policy_generation"], "reason": reason,
                "user_receipt": user_receipt})
            return meta["policy_generation"]

    @staticmethod
    def eligibility(source: dict | None, policy: dict | None, purpose: str,
                    policy_generation: int) -> list[str]:
        """근거 자격 사유 목록(빈 목록이면 자격 있음)."""
        problems = []
        if source is None:
            problems.append("source missing")
        if policy is None:
            return problems + ["no source policy"]
        if policy.get("valid") is not True:
            problems.append("source not valid")
        if purpose not in policy.get("allowed_purposes", []):
            problems.append("purpose not allowed for source")
        if policy.get("policy_generation") != policy_generation:
            problems.append("source policy generation is not current")
        return problems

    # --------------------------------------- 4 선택적 정정과 세 분류
    def correct_source(self, logical: str, task: str, *, source_id: str, fields: dict,
                       expected_revision: int, user_receipt: str) -> dict:
        """엔진 공개 put_source로 정정한 뒤 출력 결과를 세 분류로 다시 나눈다.

        엔진 쓰기와 장부 분류는 서로 다른 트랜잭션이다. 그 사이에 멈추면
        ``load_ledger``/``start_turn``의 재분류가 같은 결정적 규칙으로 복구한다.
        """
        source = self.engine.put_source(logical, task, source_id=source_id, fields=fields,
                                        expected_revision=expected_revision,
                                        user_receipt=user_receipt)
        with self._tx() as db:
            self._meta(db, logical, task)
            summary = self._reconcile(db, logical, task, f"source {source_id} corrected")
        return {"source": source, "classification": summary}

    @staticmethod
    def classify(receipt: dict | None, current_sources: dict, policies: dict) -> tuple[str, list[str]]:
        if not isinstance(receipt, dict) or receipt.get("complete") is not True or \
                not isinstance(receipt.get("sources"), dict) or not receipt["sources"]:
            return UNRESOLVED, ["receipt missing or incomplete"]
        reasons = []
        for sid, ref in receipt["sources"].items():
            source = current_sources.get(sid)
            if source is None:
                reasons.append(f"{sid}: source missing")
                continue
            policy = policies.get(sid)
            if policy is None or policy.get("valid") is not True:
                reasons.append(f"{sid}: source no longer valid")
            for fname, revision in ref.get("fields", {}).items():
                if source["fields"].get(fname, {}).get("revision") != revision:
                    reasons.append(f"{sid}.{fname}: used revision {revision} is outdated")
        return (AFFECTED, reasons) if reasons else (NOT_DEPENDENT, [])

    def _reconcile(self, db, logical, task, trigger: str) -> dict:
        current = self._engine_state(db, logical, task)["sources"]
        policies = {sid: st for sid, (_, st) in self._items(db, logical, task, "source_policy").items()}
        summary = {UNRESOLVED: [], AFFECTED: [], NOT_DEPENDENT: []}
        for oid, (body, state) in self._items(db, logical, task, "output").items():
            # 호스트 재확인 영수증이 있으면 그것이 현재 효력 영수증이다(원래 영수증은 보존).
            label, reasons = self.classify(state.get("recheck_receipt") or body.get("receipt"),
                                           current, policies)
            summary[label].append(oid)
            # 한 번 보류된 결과는 호스트 확인 없이 풀지 않는다(보류는 단조).
            hold = state.get("hold", False) or label in HOLD_CLASSES
            if state.get("classification") != label or state.get("hold") != hold:
                state.setdefault("history", []).append({
                    "at": time.time(), "trigger": trigger, "from": state.get("classification"),
                    "to": label, "reasons": reasons})
                state.update(classification=label, classification_ko=CLASS_LABELS[label],
                             hold=hold, hold_reasons=reasons if label in HOLD_CLASSES
                             else state.get("hold_reasons", []))
                self._update(db, logical, task, "output", oid, state)
        if summary[UNRESOLVED] or summary[AFFECTED]:
            self._event(db, logical, task, "OUTPUTS_RECLASSIFIED", {"trigger": trigger, **summary})
        return summary

    def import_result(self, logical: str, task: str, *, text: str, origin: str,
                      user_receipt: str, receipt: dict | None = None) -> str:
        """채팅 경로 밖에서 만든 결과(예: SDK 초안)를 장부에 들임. 영수증이 없으면
        재분류에서 반드시 「의존 미확정」으로 보류된다."""
        _text(text); _text(origin); _text(user_receipt)
        oid = uuid4().hex
        with self._tx() as db:
            self._meta(db, logical, task)
            self._insert(db, logical, task, "output", oid,
                         {"text": text, "origin": origin, "receipt": receipt,
                          "user_receipt": user_receipt}, {"status": "IMPORTED"})
            self._event(db, logical, task, "RESULT_IMPORTED", {"output_id": oid, "origin": origin})
            self._reconcile(db, logical, task, "result imported")
        return oid

    # ----------------------------------------------------- 1 맥락 장부
    def load_ledger(self, logical: str, task: str) -> dict:
        """주체번호의 목표·의무·세대·원문·사용 기록·보류 목록·격리 후보 묶음.
        재시작 후에도 같은 SQLite 파일에서 그대로 복구된다."""
        with self._tx() as db:
            meta = self._meta(db, logical, task)
            self._reconcile(db, logical, task, "ledger loaded")
            engine_state = self._engine_state(db, logical, task)
            outputs = {k: {"body": b, "state": s} for k, (b, s) in
                       self._items(db, logical, task, "output").items()}
            turns = {k: {"body": b, "state": s} for k, (b, s) in
                     self._items(db, logical, task, "turn").items()}
            quarantine = {k: {"body": b, "state": s} for k, (b, s) in
                          self._items(db, logical, task, "quarantine").items()}
            policies = {k: s for k, (_, s) in self._items(db, logical, task, "source_policy").items()}
            return {
                "owner_id": self.owner_id, "logical_id": logical, "task_id": task,
                "goals": engine_state["goals"], "priorities": engine_state["priorities"],
                "rules": engine_state["rules"], "obligations": engine_state["obligations"],
                "generation": {"goal_revision": engine_state["goal_revision"],
                               "run_epoch": engine_state["run_epoch"],
                               "policy_generation": meta["policy_generation"],
                               "phase": engine_state["phase"]},
                "sources": engine_state["sources"], "source_policies": policies,
                "usage_records": outputs, "turns": turns,
                "holds": {"outputs": sorted(k for k, v in outputs.items() if v["state"].get("hold")),
                          "turns": sorted(k for k, v in turns.items()
                                          if v["state"]["status"] in {"HOLD", "STALE"})},
                "quarantine": quarantine, "engines": meta["engines"],
            }

    def events(self, logical: str, task: str) -> list[dict]:
        with self._tx() as db:
            rows = db.execute("SELECT seq, kind, payload, created FROM dk_chat_events WHERE owner=?"
                              " AND logical_id=? AND task_id=? ORDER BY seq",
                              (self.owner_id, logical, task)).fetchall()
        return [{"seq": s, "kind": k, "payload": json.loads(p), "created": c} for s, k, p, c in rows]

    # -------------------------------------------------- 대화 한 차례
    def _turn(self, db, logical, task, turn_id) -> tuple[dict, dict]:
        found = self._get(db, logical, task, "turn", turn_id)
        if found is None:
            raise ChatHold("turn missing")
        return found

    def turn(self, logical: str, task: str, turn_id: str) -> dict:
        with self._tx() as db:
            body, state = self._turn(db, logical, task, turn_id)
            return {"body": body, "state": state}

    def start_turn(self, logical: str, task: str, *, question: str, purpose: str | None,
                   domain: str) -> str:
        _text(question, "question"); _text(domain, "domain")
        if len(question) > self.config.max_question_chars:
            raise ValueError("question too long")
        if purpose is not None:
            _text(purpose, "purpose")
        tid = uuid4().hex
        with self._tx() as db:
            meta = self._meta(db, logical, task)
            self._reconcile(db, logical, task, "turn started")
            st = self._engine_state(db, logical, task)
            state = {"status": "STARTED", "phase_at_start": st["phase"],
                     "goal_revision": st["goal_revision"],
                     "policy_generation": meta["policy_generation"], "attempts": []}
            self._insert(db, logical, task, "turn", tid,
                         {"question": question, "requested_purpose": purpose, "domain": domain},
                         state)
            self._event(db, logical, task, "TURN_STARTED", {"turn_id": tid})
        return tid

    def _set_turn(self, db, logical, task, tid, **changes) -> dict:
        body, state = self._turn(db, logical, task, tid)
        state.update(changes)
        self._update(db, logical, task, "turn", tid, state)
        return state

    def hold_turn(self, logical: str, task: str, tid: str, *, reason: str,
                  status: str = "HOLD") -> dict:
        if status not in {"HOLD", "STALE"}:
            raise ValueError("hold status required")
        with self._tx() as db:
            state = self._set_turn(db, logical, task, tid, status=status, hold_reason=reason)
            self._event(db, logical, task, f"TURN_{status}", {"turn_id": tid, "reason": reason})
            return state

    def judge_purpose(self, logical: str, task: str, tid: str,
                      classifier: Callable[[str], str] | None = None) -> str | None:
        """질문 목적 판정. 분류 함수와 명시 목적이 다르면 보류(모호)."""
        with self._tx() as db:
            body, state = self._turn(db, logical, task, tid)
        if state["phase_at_start"] != "ACTIVE":
            self.hold_turn(logical, task, tid, reason="task not active")
            return None
        if body["domain"] not in self.config.allowed_domains:
            self.hold_turn(logical, task, tid, reason="domain not allowed")
            return None
        purpose = body["requested_purpose"]
        if classifier is not None:
            try:
                judged = classifier(body["question"])
            except Exception as exc:
                self.hold_turn(logical, task, tid, reason=f"purpose classifier error {type(exc).__name__}")
                return None
            if purpose is not None and judged != purpose:
                self.hold_turn(logical, task, tid, reason="purpose ambiguous")
                return None
            purpose = judged
        if purpose not in self.config.known_purposes:
            self.hold_turn(logical, task, tid, reason="purpose unknown")
            return None
        with self._tx() as db:
            self._set_turn(db, logical, task, tid, purpose=purpose, status="PURPOSE_JUDGED")
        return purpose

    def select_evidence(self, logical: str, task: str, tid: str) -> dict | None:
        """자격 있는 원문만 고른다. 고른 원문의 판본·자격을 처리 시작 세대로 고정."""
        with self._tx() as db:
            meta = self._meta(db, logical, task)
            body, state = self._turn(db, logical, task, tid)
            st = self._engine_state(db, logical, task)
            policies = {k: s for k, (_, s) in self._items(db, logical, task, "source_policy").items()}
            selection, excluded = {}, {}
            for sid in sorted(set(st["sources"]) | set(policies)):
                problems = self.eligibility(st["sources"].get(sid), policies.get(sid),
                                            state["purpose"], meta["policy_generation"])
                if problems:
                    excluded[sid] = problems
                else:
                    selection[sid] = sorted(st["sources"][sid]["fields"])
            if not selection:
                self._set_turn(db, logical, task, tid, excluded=excluded)
                ok = False
            else:
                fingerprint = self._fingerprint(st, policies, meta, selection)
                self._set_turn(db, logical, task, tid, selection=selection, excluded=excluded,
                               fingerprint=fingerprint, status="EVIDENCE_SELECTED")
                ok = True
        if not ok:
            self.hold_turn(logical, task, tid, reason="no eligible evidence")
            return None
        return selection

    @staticmethod
    def _fingerprint(st: dict, policies: dict, meta: dict, selection: dict) -> dict:
        return {
            "phase": st["phase"], "goal_revision": st["goal_revision"],
            "policy_generation": meta["policy_generation"],
            "sources": {sid: {"revision": st["sources"][sid]["revision"],
                              "fields": {f: i["revision"] for f, i in st["sources"][sid]["fields"].items()}}
                        for sid in selection if sid in st["sources"]},
            "policies": {sid: {k: policies[sid].get(k) for k in
                               ("valid", "allowed_purposes", "policy_generation")}
                         for sid in selection if sid in policies},
        }

    # ---------------------------------------------- 7 엔진 상태·교체
    def engine_status(self, logical: str, task: str) -> dict:
        with self._tx() as db:
            return self._meta(db, logical, task)["engines"]

    def next_engine(self, logical: str, task: str, specs: list[EngineSpec]) -> EngineSpec | None:
        engines = self.engine_status(logical, task)
        for spec in specs:
            if not engines.get(spec.name, {}).get("quarantined", False):
                return spec
        return None

    def begin_attempt(self, logical: str, task: str, tid: str, spec: EngineSpec) -> RunBinding:
        """엔진의 공개 begin_run을 그대로 다시 써서 같은 주체번호·업무를 이어받는다."""
        if self.engine_status(logical, task).get(spec.name, {}).get("quarantined"):
            raise ChatHold("engine quarantined")
        binding = self.engine.begin_run(logical, task, model_id=spec.name, session_id=tid)
        if binding.logical_id != logical or binding.task_id != task:
            raise ChatHold("logical id changed during engine handover")
        with self._tx() as db:
            body, state = self._turn(db, logical, task, tid)
            state["attempts"].append({"engine": spec.name, "family": spec.family,
                                      "binding": binding.__dict__, "result": "STARTED"})
            state.update(status="ATTEMPT_STARTED", current_attempt=len(state["attempts"]) - 1)
            self._update(db, logical, task, "turn", tid, state)
        return binding

    def record_failure(self, logical: str, task: str, tid: str, *, engine_name: str,
                       reason: str) -> dict:
        """실패 1회 기록. 한도에 이르면 그 엔진을 이 업무에서 격리. 응답은 지우지 않는다."""
        with self._tx() as db:
            meta = self._meta(db, logical, task)
            body, state = self._turn(db, logical, task, tid)
            entry = meta["engines"].setdefault(engine_name, {"failures": 0, "quarantined": False})
            entry["failures"] += 1
            if state.get("current_attempt") is not None:
                attempt = state["attempts"][state["current_attempt"]]
                attempt.update(result="FAILED", reason=reason)
                if attempt.get("response") is not None:
                    self._insert(db, logical, task, "quarantine", uuid4().hex,
                                 {"type": "failed_response", "turn_id": tid, "engine": engine_name,
                                  "response": attempt["response"]},
                                 {"reason": reason, "trial_status": "NOT_CANDIDATE",
                                  "adoption_status": "NOT_ADOPTED"})
            self._event(db, logical, task, "ENGINE_FAILURE",
                        {"turn_id": tid, "engine": engine_name, "reason": reason,
                         "failures": entry["failures"]})
            if not entry["quarantined"] and entry["failures"] >= self.config.max_failures_per_engine:
                entry["quarantined"] = True
                self._event(db, logical, task, "ENGINE_QUARANTINED",
                            {"engine": engine_name, "failures": entry["failures"],
                             "limit": self.config.max_failures_per_engine, "turn_id": tid})
            self._update(db, logical, task, "meta", "meta", meta)
            self._update(db, logical, task, "turn", tid, state)
            return dict(entry)

    # ------------------------------------------------- 응답 생성
    def generate(self, logical: str, task: str, tid: str, binding: RunBinding,
                 spec: EngineSpec) -> dict:
        """호스트가 실제로 전달한 원문(엔진 delivered_refs 기록)만 생성 모델에 준다."""
        with self._tx() as db:
            body, state = self._turn(db, logical, task, tid)
        port = self.engine.model_port(binding)
        contract = port.read_contract(state["selection"], record_delivery=True)
        evidence = {sid: {f: item["value"] for f, item in src["fields"].items()}
                    for sid, src in contract["sources"].items()}
        request = {"question": body["question"], "purpose": state["purpose"],
                   "goals": contract["goals"], "priorities": contract["priorities"],
                   "obligations": contract["obligations"], "evidence": evidence}
        raw = spec.generate(_copy(request))
        response = self._validate_response(raw)
        with self._tx() as db:
            body, state = self._turn(db, logical, task, tid)
            attempt = state["attempts"][state["current_attempt"]]
            attempt.update(response=response, response_hash=_hash(response), result="RESPONDED",
                           delivered={sid: {f: i["revision"] for f, i in src["fields"].items()}
                                      for sid, src in contract["sources"].items()})
            state["status"] = "RESPONDED"
            self._update(db, logical, task, "turn", tid, state)
        return response

    def _validate_response(self, raw: Any) -> dict:
        if not isinstance(raw, dict):
            raise ChatHold("response must be a mapping")
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > self.config.max_response_chars:
            raise ChatHold("response text invalid")
        response = {"text": text, "goal_ids": raw.get("goal_ids"),
                    "declared_goals": raw.get("declared_goals"),
                    "goal_changes": raw.get("goal_changes"),
                    "claimed_sources": raw.get("claimed_sources", {})}
        _json(response)
        return response

    def _attempt(self, logical, task, tid) -> tuple[dict, dict, dict]:
        with self._tx() as db:
            body, state = self._turn(db, logical, task, tid)
        return body, state, state["attempts"][state["current_attempt"]]

    # ---------------------------------------------- 5 목표 이탈 검사
    def check_goals(self, logical: str, task: str, tid: str) -> dict:
        body, state, attempt = self._attempt(logical, task, tid)
        with self._tx() as db:
            st = self._engine_state(db, logical, task)
        check = {"goals": st["goals"], "priorities": st["priorities"],
                 "goal_revision": st["goal_revision"], "question": body["question"],
                 "response": _copy(attempt["response"])}
        try:
            result = self.goal_checker(check)
        except Exception as exc:
            result = {"ok": False, "reasons": [f"goal checker error {type(exc).__name__}"]}
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            result = {"ok": False, "reasons": ["goal checker returned no explicit boolean"]}
        if state["goal_revision"] != st["goal_revision"]:
            result = {"ok": False, "reasons": ["goal revision changed during turn"]}
        self._note_attempt(logical, task, tid, goal_check=result)
        return result

    def _note_attempt(self, logical, task, tid, **changes) -> None:
        with self._tx() as db:
            body, state = self._turn(db, logical, task, tid)
            state["attempts"][state["current_attempt"]].update(changes)
            self._update(db, logical, task, "turn", tid, state)

    # ---------------------------------------------- 6 독립 검증
    def verify(self, logical: str, task: str, tid: str, verifier: VerifierSpec,
               worker: EngineSpec) -> dict:
        """다른 계열 검증. 입력은 근거와 응답 두 가지뿐이다."""
        if same_family(verifier.family, worker.family):
            with self._tx() as db:
                self._event(db, logical, task, "VERIFIER_REFUSED",
                            {"turn_id": tid, "verifier": verifier.name, "worker": worker.name,
                             "family": verifier.family})
            raise ChatHold("verifier must belong to a different model family")
        body, state, attempt = self._attempt(logical, task, tid)
        evidence = self._delivered_evidence(logical, task, attempt["delivered"])
        payload = {"evidence": evidence, "response": attempt["response"]["text"]}
        try:
            raw = verifier.verify(_copy(payload))
            ok = isinstance(raw, dict) and raw.get("supported") is True
            reasons = raw.get("reasons", []) if isinstance(raw, dict) else []
        except Exception as exc:
            ok, reasons = False, [f"verifier error {type(exc).__name__}"]
        result = {"ok": ok, "reasons": reasons if isinstance(reasons, list) else [],
                  "verifier": verifier.name, "family": verifier.family}
        self._note_attempt(logical, task, tid, verification=result)
        return result

    def _delivered_evidence(self, logical, task, delivered: dict) -> dict:
        """영수증에 적힌 판본의 값만 돌려준다. 판본이 바뀌었으면 비워서 실패하게 둔다."""
        with self._tx() as db:
            sources = self._engine_state(db, logical, task)["sources"]
        out = {}
        for sid, fields in delivered.items():
            for fname, revision in fields.items():
                item = sources.get(sid, {}).get("fields", {}).get(fname)
                if item is not None and item["revision"] == revision:
                    out.setdefault(sid, {})[fname] = item["value"]
        return out

    # ---------------------------------------------- 8 늦은 응답 차단
    def _stale_reasons(self, db, logical, task, state, binding: dict | None) -> list[str]:
        meta = self._meta(db, logical, task)
        st = self._engine_state(db, logical, task)
        policies = {k: s for k, (_, s) in self._items(db, logical, task, "source_policy").items()}
        now = self._fingerprint(st, policies, meta, state["selection"])
        reasons = [f"{key} changed" for key in now if now[key] != state["fingerprint"].get(key)]
        if binding is not None:
            run = st.get("current_run") or {}
            if st["run_epoch"] != binding["run_epoch"] or run.get("run_id") != binding["run_id"]:
                reasons.append("engine run replaced")
        return reasons

    def check_generation(self, logical: str, task: str, tid: str, *, stage: str) -> bool:
        """처리 시작 세대와 현재 세대를 대조. 다르면 응답을 폐기하고 사유를 영구 기록."""
        with self._tx() as db:
            body, state = self._turn(db, logical, task, tid)
            attempt = state["attempts"][state["current_attempt"]]
            reasons = self._stale_reasons(db, logical, task, state, attempt["binding"])
            if reasons:
                self._reject_late(db, logical, task, tid, state, attempt, stage, reasons)
                return False
            return True

    def _reject_late(self, db, logical, task, tid, state, attempt, stage, reasons) -> None:
        attempt.update(result="LATE_REJECTED", late_reasons=reasons)
        state.update(status="STALE", hold_reason=f"late response rejected at {stage}")
        self._update(db, logical, task, "turn", tid, state)
        qid = uuid4().hex
        self._insert(db, logical, task, "quarantine", qid,
                     {"type": "rejected_response", "turn_id": tid, "engine": attempt["engine"],
                      "response": attempt.get("response")},
                     {"reason": "late response", "stage": stage, "reasons": reasons,
                      "trial_status": "NOT_CANDIDATE", "adoption_status": "NOT_ADOPTED"})
        self._event(db, logical, task, "LATE_RESPONSE_REJECTED", {
            "turn_id": tid, "stage": stage, "reasons": reasons, "engine": attempt["engine"],
            "run_id": attempt["binding"]["run_id"], "response_hash": attempt.get("response_hash"),
            "quarantine_id": qid})

    # ---------------------------------------------- 9 환각 제어부
    def filter_sentences(self, logical: str, task: str, tid: str) -> list[str]:
        """문장마다 일치도를 계산해 임계값 이상만 남긴다. 미달 문장은 격리 보관."""
        body, state, attempt = self._attempt(logical, task, tid)
        evidence_map = self._delivered_evidence(logical, task, attempt["delivered"])
        evidence = [_json(v) if not isinstance(v, str) else v
                    for fields in evidence_map.values() for v in fields.values()]
        threshold = self.config.sentence_support_threshold
        passed, scored = [], []
        receipt = self._receipt(attempt["delivered"], state)
        with self._tx() as db:
            for index, sentence in enumerate(split_sentences(attempt["response"]["text"])):
                score = self._score(sentence, evidence)
                ok = score is not None and score >= threshold
                scored.append({"index": index, "score": score, "passed": ok})
                if ok:
                    passed.append(sentence)
                    continue
                self._insert(db, logical, task, "quarantine", uuid4().hex,
                             {"type": "sentence", "turn_id": tid, "index": index,
                              "sentence": sentence, "response_hash": attempt["response_hash"]},
                             {"score": score, "threshold": threshold,
                              "reason": "scorer invalid" if score is None else "below threshold",
                              "domain": body["domain"], "purpose": state["purpose"],
                              "receipt": receipt,
                              "io_spec": {"input_unit": "question+evidence",
                                          "output_unit": "sentence",
                                          "operation": "evidence_grounded_answer"},
                              "resume_condition": {"type": "rescore_meets_threshold"},
                              "trial_status": "NOT_EVALUATED", "adoption_status": "NOT_ADOPTED"})
            body, state = self._turn(db, logical, task, tid)
            state["attempts"][state["current_attempt"]].update(sentence_scores=scored,
                                                               passed_sentences=passed)
            state["status"] = "SENTENCES_FILTERED"
            self._update(db, logical, task, "turn", tid, state)
            self._event(db, logical, task, "SENTENCES_FILTERED", {
                "turn_id": tid, "passed": len(passed), "quarantined": len(scored) - len(passed),
                "threshold": threshold})
        return passed

    # ---------------------------------------------- 3 근거 영수증
    def _receipt(self, delivered: dict, state: dict) -> dict:
        return {"basis": "HOST_DELIVERED", "purpose": state["purpose"],
                "policy_generation": state["fingerprint"]["policy_generation"],
                "sources": {sid: {"source_revision": state["fingerprint"]["sources"][sid]["revision"],
                                  "fields": dict(fields)}
                            for sid, fields in delivered.items()}}

    @staticmethod
    def receipt_problems(selection: dict, delivered: dict, engine_refs: dict,
                         claimed: Any) -> list[str]:
        """영수증 완전성: 호스트 선택 = 엔진 전달 기록 = 엔진 후보 출처, 모델 주장 ⊆ 전달."""
        def triples(refs):
            return {(sid, f, r) for sid, fields in refs.items() for f, r in fields.items()}
        problems = []
        if not delivered:
            problems.append("no host-delivered sources")
        if {(sid, f) for sid, fields in selection.items() for f in fields} != \
                {(sid, f) for sid, fields in delivered.items() for f in fields}:
            problems.append("delivered fields differ from host selection")
        if triples(engine_refs) != triples(delivered):
            problems.append("engine candidate references differ from host delivery")
        if claimed:
            if not isinstance(claimed, dict) or any(not isinstance(v, dict) for v in claimed.values()):
                problems.append("model source claim malformed")
            elif not triples(claimed) <= triples(delivered):
                problems.append("model claims sources that were not delivered")
        return problems

    def _incomplete(self, logical, task, tid, problems, cid) -> None:
        with self._tx() as db:
            body, state = self._turn(db, logical, task, tid)
            self._incomplete_in(db, logical, task, tid, state, problems, cid)

    def _incomplete_in(self, db, logical, task, tid, state, problems, cid) -> None:
        state.update(status="HOLD", hold_reason="receipt incomplete")
        self._update(db, logical, task, "turn", tid, state)
        self._event(db, logical, task, "RECEIPT_INCOMPLETE",
                    {"turn_id": tid, "problems": problems, "candidate_id": cid})

    def record_receipt(self, logical: str, task: str, tid: str) -> str | None:
        """엔진 후보 제출 → 영수증 완전성 검사 → 최종 세대 대조와 출력 기록을 한 트랜잭션에."""
        body, state, attempt = self._attempt(logical, task, tid)
        binding = RunBinding(**attempt["binding"])
        response = attempt["response"]
        claimed = response.get("claimed_sources") or {}
        # 제출 전 검사: 모델 주장이 전달 기록 밖을 가리키거나 선택과 전달이 다르면 제출하지 않음.
        pre = self.receipt_problems(state["selection"], attempt["delivered"],
                                    attempt["delivered"], claimed)
        if pre:
            self._incomplete(logical, task, tid, pre, None)
            return None
        port = self.engine.model_port(binding)
        try:
            cid = port.submit_candidate(
                content={"text": " ".join(attempt["passed_sentences"])}, purpose=state["purpose"],
                source_refs=_copy(claimed) if isinstance(claimed, dict) else {},
                consumption_complete=True,
                goal_ids=list(response["goal_ids"]))
        except (EngineHold, ValueError) as exc:
            with self._tx() as db:
                body, state = self._turn(db, logical, task, tid)
                attempt = state["attempts"][state["current_attempt"]]
                reasons = self._stale_reasons(db, logical, task, state, attempt["binding"])
                if reasons:
                    self._reject_late(db, logical, task, tid, state, attempt, "receipt", reasons)
                    return None
            self._incomplete(logical, task, tid, [f"engine refused candidate: {exc}"], None)
            return None
        candidate = self.engine.candidate(logical, task, cid)
        problems = self.receipt_problems(state["selection"], attempt["delivered"],
                                         candidate["source_refs"], claimed)
        receipt = self._receipt(attempt["delivered"], state)
        receipt.update(complete=not problems, problems=problems, candidate_id=cid,
                       model_claimed=claimed)
        with self._tx() as db:
            body, state = self._turn(db, logical, task, tid)
            attempt = state["attempts"][state["current_attempt"]]
            reasons = self._stale_reasons(db, logical, task, state, attempt["binding"])
            if reasons:
                self._reject_late(db, logical, task, tid, state, attempt, "receipt", reasons)
                return None
            if problems:
                self._incomplete_in(db, logical, task, tid, state, problems, cid)
                return None
            oid = uuid4().hex
            meta = self._meta(db, logical, task)
            self._insert(db, logical, task, "output", oid, {
                "turn_id": tid, "text": " ".join(attempt["passed_sentences"]),
                "sentences": attempt["passed_sentences"], "receipt": receipt,
                "candidate_id": cid, "engine": attempt["engine"],
                "run_id": binding.run_id, "run_epoch": binding.run_epoch,
                "goal_revision": binding.goal_revision,
                "policy_generation": meta["policy_generation"], "purpose": state["purpose"],
                "domain": body["domain"], "verification": attempt.get("verification")},
                {"status": "RECORDED", "classification": NOT_DEPENDENT,
                 "classification_ko": CLASS_LABELS[NOT_DEPENDENT], "hold": False, "history": []})
            state.update(status="RECEIPT_RECORDED", output_id=oid)
            self._update(db, logical, task, "turn", tid, state)
            self._event(db, logical, task, "RECEIPT_RECORDED",
                        {"turn_id": tid, "output_id": oid, "candidate_id": cid,
                         "sources": receipt["sources"]})
            return oid

    def emit_output(self, logical: str, task: str, tid: str) -> dict | None:
        """출력 직전 세대 대조. 통과한 문장만으로 된 출력을 돌려준다."""
        with self._tx() as db:
            body, state = self._turn(db, logical, task, tid)
            attempt = state["attempts"][state["current_attempt"]]
            reasons = self._stale_reasons(db, logical, task, state, attempt["binding"])
            out_body, out_state = self._get(db, logical, task, "output", state["output_id"])
            if reasons or out_state.get("hold"):
                if reasons:
                    self._reject_late(db, logical, task, tid, state, attempt, "output", reasons)
                else:
                    state.update(status="HOLD", hold_reason="output held by reclassification")
                    self._update(db, logical, task, "turn", tid, state)
                out_state.update(status="WITHHELD")
                self._update(db, logical, task, "output", state["output_id"], out_state)
                return None
            out_state["status"] = "OUTPUT"
            self._update(db, logical, task, "output", state["output_id"], out_state)
            attempt["result"] = "OUTPUT"
            state["status"] = "OUTPUT"
            self._update(db, logical, task, "turn", tid, state)
            self._event(db, logical, task, "OUTPUT_EMITTED",
                        {"turn_id": tid, "output_id": state["output_id"]})
            return {"output_id": state["output_id"], "text": out_body["text"],
                    "receipt": out_body["receipt"], "engine": out_body["engine"]}

    # ---------------------------------------------- 10 격리 후보 시험
    def evaluate_quarantine_candidates(self, logical: str, task: str, *, domain: str,
                                       purpose: str, io_spec: dict) -> list[dict]:
        """다섯 관문을 모두 통과한 후보만 우선도를 계산해 시험 허가. 채택은 아님."""
        _text(domain); _text(purpose)
        if not isinstance(io_spec, dict) or set(io_spec) != {"input_unit", "output_unit", "operation"}:
            raise ValueError("io_spec needs input_unit, output_unit, operation")
        permitted = []
        with self._tx() as db:
            meta = self._meta(db, logical, task)
            current = self._engine_state(db, logical, task)
            policies = {k: s for k, (_, s) in self._items(db, logical, task, "source_policy").items()}
            for qid, (body, state) in self._items(db, logical, task, "quarantine").items():
                if body.get("type") != "sentence":
                    continue
                gates, rescore = self._gate_item(meta, current, policies, body, state,
                                                 domain=domain, purpose=purpose, io_spec=io_spec)
                record = {"evaluated_at": time.time(), "gates": gates,
                          "policy_generation": meta["policy_generation"], "domain": domain,
                          "purpose": purpose, "io_spec": io_spec}
                if all(gates[g] is True for g in GATES):
                    priority = self.priority_fn(state, rescore, self.config)
                    record.update(priority=priority, rescore=rescore)
                    state.update(trial_status="TRIAL_PERMITTED", trial_priority=priority)
                    permitted.append({"quarantine_id": qid, "priority": priority,
                                      "sentence": body["sentence"]})
                else:
                    state.update(trial_status="TRIAL_DENIED", trial_priority=None)
                state.setdefault("gate_history", []).append(record)
                # 채택 상태는 관문 통과와 별개로 그대로 둔다.
                state.setdefault("adoption_status", "NOT_ADOPTED")
                self._update(db, logical, task, "quarantine", qid, state)
            permitted.sort(key=lambda item: (-item["priority"], item["quarantine_id"]))
            self._event(db, logical, task, "QUARANTINE_GATES_EVALUATED",
                        {"permitted": [p["quarantine_id"] for p in permitted],
                         "domain": domain, "purpose": purpose, "io_spec": io_spec})
        return permitted

    def _gate_item(self, meta, current, policies, body, state, *, domain, purpose,
                   io_spec) -> tuple[dict, float]:
        """격리 후보 하나의 다섯 관문 판정(평가와 채택 직전 재확인에 함께 쓴다)."""
        gates = {}
        gates["domain_allowed"] = state["domain"] in self.config.allowed_domains and \
            state["domain"] == domain
        receipt = state.get("receipt")
        rs = receipt.get("sources", {}) if isinstance(receipt, dict) else {}
        gates["eligibility_and_policy_generation"] = bool(rs) and \
            receipt.get("policy_generation") == meta["policy_generation"] and \
            state["purpose"] == purpose and all(
                not self.eligibility(current["sources"].get(sid), policies.get(sid),
                                     purpose, meta["policy_generation"]) for sid in rs)
        gates["required_receipts"] = bool(rs) and receipt.get("basis") == "HOST_DELIVERED" \
            and all(current["sources"].get(sid, {}).get("fields", {}).get(f, {})
                    .get("revision") == rev
                    for sid, ref in rs.items() for f, rev in ref["fields"].items())
        gates["io_units_and_operation"] = state.get("io_spec") == io_spec
        rescore = 0.0
        evaluator = self.resume_evaluators.get(state.get("resume_condition", {}).get("type"))
        if evaluator is None or not gates["required_receipts"]:
            gates["resume_condition"] = False
        else:
            try:
                ok, rescore = evaluator(self, body, state, current)
                gates["resume_condition"] = ok is True
            except Exception:
                gates["resume_condition"] = False
        return gates, rescore

    def run_trial(self, logical: str, task: str, quarantine_id: str,
                  trial_fn: Callable[[dict], dict]) -> dict:
        """시험 허가된 후보만 시험. 결과는 기록만 하며 채택하지 않는다."""
        with self._tx() as db:
            found = self._get(db, logical, task, "quarantine", quarantine_id)
        if found is None or found[1].get("trial_status") != "TRIAL_PERMITTED":
            raise ChatHold("candidate not permitted for trial")
        body, state = found
        try:
            result = trial_fn(_copy(body))
            outcome = {"passed": isinstance(result, dict) and result.get("passed") is True,
                       "detail": result if isinstance(result, dict) else None}
        except Exception as exc:
            outcome = {"passed": False, "detail": {"error": type(exc).__name__}}
        with self._tx() as db:
            body, state = self._get(db, logical, task, "quarantine", quarantine_id)
            state.setdefault("trials", []).append({"at": time.time(), **outcome})
            state["trial_status"] = "TRIAL_DONE"
            state["adoption_status"] = "NOT_ADOPTED"
            self._update(db, logical, task, "quarantine", quarantine_id, state)
            self._event(db, logical, task, "QUARANTINE_TRIAL_RECORDED",
                        {"quarantine_id": quarantine_id, "passed": outcome["passed"]})
        return {"quarantine_id": quarantine_id, **outcome, "adoption_status": "NOT_ADOPTED"}

    def adopt_candidate(self, logical: str, task: str, quarantine_id: str, *,
                        user_confirmed: bool, user_receipt: str) -> dict:
        """채택은 시험 허가와 별개. 시험을 통과했고, 지금 다시 다섯 관문을 통과하고,
        사용자 확인값이 명시적으로 참일 때만 채택 상태로 바꾼다(출력에 자동 반영 안 함)."""
        _text(user_receipt, "user_receipt")
        if user_confirmed is not True:
            raise ChatHold("explicit user confirmation required for adoption")
        with self._tx() as db:
            meta = self._meta(db, logical, task)
            found = self._get(db, logical, task, "quarantine", quarantine_id)
            if found is None:
                raise ChatHold("candidate missing")
            body, state = found
            trials = state.get("trials", [])
            if state.get("trial_status") != "TRIAL_DONE" or not trials or \
                    trials[-1].get("passed") is not True:
                raise ChatHold("candidate has no passed trial")
            last = state["gate_history"][-1]
            current = self._engine_state(db, logical, task)
            policies = {k: v for k, (_, v) in self._items(db, logical, task, "source_policy").items()}
            gates, _ = self._gate_item(meta, current, policies, body, state, domain=last["domain"],
                                       purpose=last["purpose"], io_spec=last["io_spec"])
            if not all(gates[g] is True for g in GATES):
                state.setdefault("gate_history", []).append(
                    {"evaluated_at": time.time(), "gates": gates, "stage": "adoption",
                     "policy_generation": meta["policy_generation"], "domain": last["domain"],
                     "purpose": last["purpose"], "io_spec": last["io_spec"]})
                self._update(db, logical, task, "quarantine", quarantine_id, state)
                self._event(db, logical, task, "ADOPTION_REFUSED",
                            {"quarantine_id": quarantine_id, "gates": gates})
                failed = [g for g in GATES if gates[g] is not True]
                refused = True
            else:
                state.update(adoption_status="ADOPTED", adopted_at=time.time(),
                             adoption_user_receipt=user_receipt)
                self._update(db, logical, task, "quarantine", quarantine_id, state)
                self._event(db, logical, task, "CANDIDATE_ADOPTED",
                            {"quarantine_id": quarantine_id, "user_receipt": user_receipt})
                refused = False
        if refused:
            raise ChatHold("gates no longer pass: " + ", ".join(failed))
        return {"quarantine_id": quarantine_id, "trial_status": state["trial_status"],
                "adoption_status": state["adoption_status"]}

    # ------------------------------------- 보류된 결과의 재확인과 풀기
    def recheck_output(self, logical: str, task: str, output_id: str, *, purpose: str,
                       sources: dict, user_receipt: str) -> dict:
        """보류된 결과를 현재 판본 근거로 재확인해 호스트 재확인 영수증을 붙인다.

        - 지정한 원문 항목은 모두 지금 자격이 있어야 한다(유효·허용목적·현재 정책세대).
        - 결과 문장마다 그 항목들의 현재 값과 일치도를 다시 계산해 모두 임계값 이상.
        - 원래 영수증(본문)은 바꾸지 않는다. 「의존 미확정」은 이 보완 전에는 풀 수 없다.
        """
        _text(user_receipt, "user_receipt"); _text(purpose, "purpose")
        if not isinstance(sources, dict) or not sources or any(
                not isinstance(f, list) or not f or len(set(f)) != len(f) for f in sources.values()):
            raise ValueError("source field selection required")
        with self._tx() as db:
            meta = self._meta(db, logical, task)
            found = self._get(db, logical, task, "output", output_id)
            if found is None:
                raise ChatHold("output missing")
            body, state = found
            if body.get("purpose") not in (None, purpose):
                raise ChatHold("purpose differs from output purpose")
            if purpose not in self.config.known_purposes:
                raise ChatHold("purpose unknown")
            current = self._engine_state(db, logical, task)["sources"]
            policies = {k: v for k, (_, v) in self._items(db, logical, task, "source_policy").items()}
            problems, refs, evidence = [], {}, []
            for sid, fields in sorted(sources.items()):
                issues = self.eligibility(current.get(sid), policies.get(sid), purpose,
                                          meta["policy_generation"])
                problems += [f"{sid}: {p}" for p in issues]
                if issues:
                    continue
                for fname in fields:
                    item = current[sid]["fields"].get(fname)
                    if item is None:
                        problems.append(f"{sid}.{fname}: field missing")
                        continue
                    refs.setdefault(sid, {"source_revision": current[sid]["revision"],
                                          "fields": {}})["fields"][fname] = item["revision"]
                    value = item["value"]
                    evidence.append(value if isinstance(value, str) else _json(value))
            sentences = body.get("sentences") or split_sentences(body["text"])
            scores = [self._score(sentence, evidence) for sentence in sentences]
            threshold = self.config.sentence_support_threshold
            if any(score is None or score < threshold for score in scores):
                problems.append("a sentence is below the support threshold on current evidence")
            record = {"at": time.time(), "purpose": purpose, "sources": refs, "scores": scores,
                      "threshold": threshold, "problems": problems, "user_receipt": user_receipt}
            state.setdefault("rechecks", []).append(record)
            if problems:
                self._update(db, logical, task, "output", output_id, state)
                self._event(db, logical, task, "OUTPUT_RECHECK_FAILED",
                            {"output_id": output_id, "problems": problems})
            else:
                state["recheck_receipt"] = {
                    "basis": "HOST_RECHECKED", "complete": True, "purpose": purpose,
                    "policy_generation": meta["policy_generation"], "sources": refs,
                    "user_receipt": user_receipt}
                self._update(db, logical, task, "output", output_id, state)
                self._event(db, logical, task, "OUTPUT_RECHECKED",
                            {"output_id": output_id, "sources": refs})
                self._reconcile(db, logical, task, "output rechecked")
            return {"output_id": output_id, "ok": not problems, "problems": problems,
                    "scores": scores}

    def release_output(self, logical: str, task: str, output_id: str, *, user_confirmed: bool,
                       user_receipt: str) -> dict:
        """재확인 영수증이 현재 판본과 맞고 사용자 확인값이 참일 때만 보류를 푼다."""
        _text(user_receipt, "user_receipt")
        if user_confirmed is not True:
            raise ChatHold("explicit user confirmation required for release")
        with self._tx() as db:
            self._meta(db, logical, task)
            self._reconcile(db, logical, task, "release requested")
            found = self._get(db, logical, task, "output", output_id)
            if found is None:
                raise ChatHold("output missing")
            body, state = found
            if not state.get("hold"):
                raise ChatHold("output is not held")
            current = self._engine_state(db, logical, task)["sources"]
            policies = {k: v for k, (_, v) in self._items(db, logical, task, "source_policy").items()}
            recheck = state.get("recheck_receipt")
            label, reasons = self.classify(recheck, current, policies)
            if recheck is None or label != NOT_DEPENDENT:
                self._event(db, logical, task, "OUTPUT_RELEASE_REFUSED",
                            {"output_id": output_id, "classification": label,
                             "reasons": reasons or ["no host recheck receipt"]})
                refused = reasons or ["no host recheck receipt"]
            else:
                state.update(hold=False, status="RELEASED", released_at=time.time(),
                             release_user_receipt=user_receipt)
                state.setdefault("history", []).append({"at": time.time(), "trigger": "released",
                                                        "user_receipt": user_receipt})
                self._update(db, logical, task, "output", output_id, state)
                self._event(db, logical, task, "OUTPUT_RELEASED",
                            {"output_id": output_id, "user_receipt": user_receipt})
                refused = None
        if refused:
            raise ChatHold("release refused: " + "; ".join(refused))
        return {"output_id": output_id, "status": "RELEASED", "hold": False}
