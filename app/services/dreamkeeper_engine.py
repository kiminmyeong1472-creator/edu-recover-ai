"""Persistent, provider-independent Dreamkeeper control kernel.

Only ModelPort.read_contract and ModelPort.submit_candidate may be registered
as model tools. Every other method is for an authenticated, trusted host. The
host must verify user identity, source-use completeness and the meaning of
review booleans; strings and booleans here are not authentication or semantic
proof. This module performs no model calls and no external side effects.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Iterator
from uuid import uuid4


class EngineHold(RuntimeError):
    """Refused transition. Existing records are retained."""


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("nonempty text required")
    return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _positive(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("positive integer revision required")
    return value


def _checks(values: dict[str, bool], required: tuple[str, ...] = ()) -> None:
    if not isinstance(values, dict) or not values:
        raise EngineHold("host checks required")
    if any(values.get(key) is not True for key in required):
        raise EngineHold("required host check not verified")
    if any(not isinstance(key, str) or value is not True for key, value in values.items()):
        raise EngineHold("all supplied host checks must be explicitly true")


@dataclass(frozen=True)
class RunBinding:
    owner_id: str
    logical_id: str
    task_id: str
    run_id: str
    run_epoch: int
    goal_revision: int
    model_id: str
    session_id: str


class ModelPort:
    """Narrow tool surface, not a Python-process security sandbox."""

    def __init__(self, engine: DreamkeeperEngine, binding: RunBinding):
        self._engine = engine
        self._binding = binding

    def read_contract(self, source_selection: dict[str, list[str]] | None = None,
                      *, record_delivery: bool = True) -> dict:
        """Model-tool wrappers must fix record_delivery=True.

        Host-only preflight checks may use False when returned values are never
        forwarded to a model. This flag is not a model-controlled tool argument.
        """
        return self._engine._read_contract(self._binding, source_selection,
                                           record_delivery=record_delivery)

    def submit_candidate(self, *, content: Any, purpose: str,
                         source_refs: dict[str, dict[str, int]],
                         consumption_complete: bool, goal_ids: list[str]) -> str:
        return self._engine._submit(self._binding, content=content, purpose=purpose,
            source_refs=source_refs, consumption_complete=consumption_complete,
            goal_ids=goal_ids)


class DreamkeeperEngine:
    REVIEW_CHECKS = ("goals_preserved", "source_usage_verified", "source_trace_verified",
                     "evidence_verified", "purpose_allowed")
    RESUME_CHECKS = ("context_restored", "corrections_rechecked",
                     "use_scope_verified", "obligations_preserved")

    def __init__(self, database: str | Path, owner_id: str):
        self.owner_id = _text(owner_id)
        self.database = _text(str(database))
        if self.database != ":memory:" and not Path(self.database).resolve().parent.is_dir():
            raise ValueError("database parent must already exist")
        self._lock = threading.RLock()
        self._closed = False
        # A single retained connection is essential for a private :memory: DB.
        self._memory = self._connect() if self.database == ":memory:" else None
        db = self._memory or self._connect()
        try:
            for table, extra in (("tasks", ""), ("sources", ", item TEXT NOT NULL"),
                                 ("candidates", ", item TEXT NOT NULL"),
                                 ("approvals", ", item TEXT NOT NULL")):
                key = "owner,logical_id,task_id" + (",item" if extra else "")
                db.execute(f"CREATE TABLE IF NOT EXISTS dk_{table} ("
                    f"owner TEXT NOT NULL, logical_id TEXT NOT NULL,task_id TEXT NOT NULL"
                    f"{extra}, state TEXT NOT NULL, PRIMARY KEY({key}))")
            db.execute("CREATE TABLE IF NOT EXISTS dk_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                "owner TEXT NOT NULL,logical_id TEXT NOT NULL,task_id TEXT NOT NULL,"
                "kind TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL)")
        finally:
            if db is not self._memory:
                db.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database, timeout=15, isolation_level=None,
                               check_same_thread=False)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if self._closed:
                raise EngineHold("engine closed")
            db = self._memory or self._connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                if db is not self._memory:
                    db.close()

    def close(self) -> None:
        with self._lock:
            if self._memory is not None:
                self._memory.close()
                self._memory = None
            self._closed = True

    def _key(self, logical_id: str, task_id: str) -> tuple[str, str, str]:
        return self.owner_id, _text(logical_id), _text(task_id)

    def _load(self, db, table, logical_id, task_id, item=None) -> dict:
        args = self._key(logical_id, task_id)
        where = "owner=? AND logical_id=? AND task_id=?"
        if item is not None:
            where += " AND item=?"
            args += (_text(item),)
        row = db.execute(f"SELECT state FROM dk_{table} WHERE {where}", args).fetchone()
        if row is None:
            raise EngineHold("record unavailable in this owner and task")
        return json.loads(row[0])

    def _write(self, db, table, logical_id, task_id, state, item=None) -> None:
        args = self._key(logical_id, task_id)
        if item is None:
            db.execute(f"INSERT OR REPLACE INTO dk_{table} VALUES(?,?,?,?)", args + (_json(state),))
        else:
            db.execute(f"INSERT OR REPLACE INTO dk_{table} VALUES(?,?,?,?,?)",
                       args + (_text(item), _json(state)))

    def _event(self, db, logical_id, task_id, kind, payload) -> None:
        db.execute("INSERT INTO dk_events(owner,logical_id,task_id,kind,payload,created) "
                   "VALUES(?,?,?,?,?,?)", self._key(logical_id, task_id) +
                   (kind, _json(payload), time.time()))

    def _all(self, db, table, logical_id, task_id) -> dict[str, dict]:
        rows = db.execute(f"SELECT item,state FROM dk_{table} WHERE owner=? AND logical_id=? "
                          "AND task_id=?", self._key(logical_id, task_id)).fetchall()
        return {key: json.loads(raw) for key, raw in rows}

    @staticmethod
    def _goals(goals, priorities, rules, obligations) -> None:
        if not isinstance(goals, dict) or not goals or not isinstance(obligations, dict):
            raise ValueError("structured goals and obligations required")
        if not isinstance(priorities, list) or len(priorities) != len(goals) or set(priorities) != set(goals):
            raise ValueError("full, unique priority order required")
        if not isinstance(rules, list):
            raise ValueError("rules list required")
        for value in [*goals, *goals.values(), *rules, *obligations, *obligations.values()]:
            _text(value)

    def create_task(self, logical_id: str, task_id: str, *, goals: dict[str, str],
                    priorities: list[str], rules: list[str], obligations: dict[str, str],
                    user_receipt: str) -> dict:
        """Trusted host has authenticated the user's task-creation instruction."""
        self._goals(goals, priorities, rules, obligations)
        _text(user_receipt)
        with self._tx() as db:
            exists = db.execute("SELECT 1 FROM dk_tasks WHERE owner=? AND logical_id=? AND task_id=?",
                                self._key(logical_id, task_id)).fetchone()
            if exists:
                raise EngineHold("task exists; resume or explicitly revise")
            state = dict(logical_id=logical_id, task_id=task_id, goal_revision=1, run_epoch=0,
                phase="ACTIVE", goals=goals, priorities=priorities, rules=rules,
                obligations=obligations, corrections=[], current_run=None)
            self._write(db, "tasks", logical_id, task_id, state)
            self._event(db, logical_id, task_id, "TASK_CREATED", {
                "user_receipt": user_receipt,"before":None,"after":state})
            return json.loads(_json(state))

    def revise_task(self, logical_id: str, task_id: str, *, expected_revision: int,
                    user_authorized: bool, user_receipt: str, instruction: str = "",
                    goals=None, priorities=None, rules=None, obligations=None) -> dict:
        """Not a model tool. User authorization is verified by the host."""
        if user_authorized is not True:
            raise EngineHold("authenticated user change required")
        _positive(expected_revision)
        _text(user_receipt)
        with self._tx() as db:
            state = self._load(db, "tasks", logical_id, task_id)
            before=json.loads(_json(state))
            if state["goal_revision"] != expected_revision:
                raise EngineHold("stale user revision")
            changes = {"goals": goals, "priorities": priorities, "rules": rules, "obligations": obligations}
            for key, value in changes.items():
                if value is not None:
                    state[key] = value
            self._goals(state["goals"], state["priorities"], state["rules"], state["obligations"])
            state["goal_revision"] += 1
            state["current_run"] = None
            state["corrections"].append(dict(instruction=instruction,user_receipt=user_receipt,
                                              goal_revision=state["goal_revision"]))
            self._write(db, "tasks", logical_id, task_id, state)
            self._event(db, logical_id, task_id, "USER_REVISED", {
                "before":before,"after":state,"user_receipt":user_receipt})
            return json.loads(_json(state))

    def put_source(self, logical_id: str, task_id: str, *, source_id: str, fields: dict,
                   expected_revision: int, user_receipt: str) -> dict:
        """Trusted source correction; revisions are tracked per actual field."""
        _text(source_id); _text(user_receipt)
        if not isinstance(fields, dict) or not fields:
            raise ValueError("nonempty source fields required")
        for field in fields:
            _text(field)
        _json(fields)
        with self._tx() as db:
            self._load(db, "tasks", logical_id, task_id)
            existing = self._all(db, "sources", logical_id, task_id).get(source_id)
            before=json.loads(_json(existing)) if existing is not None else None
            source = existing or {"source_id": source_id, "revision": 0, "fields": {}}
            if type(expected_revision) is not int or source["revision"] != expected_revision:
                raise EngineHold("stale source correction")
            changed = []
            for field, value in fields.items():
                old = source["fields"].get(field)
                if old is None or _json(old["value"]) != _json(value):
                    changed.append(field)
                    source["fields"][field] = {"revision": 1 if old is None else old["revision"]+1,
                                               "value": value}
            if changed:
                source["revision"] += 1
                self._write(db, "sources", logical_id, task_id, source, source_id)
                for cid, candidate in self._all(db, "candidates", logical_id, task_id).items():
                    used = candidate["source_refs"].get(source_id, {})
                    dependent = bool(set(changed) & set(used))
                    if dependent or not candidate.get("source_trace_verified", False):
                        candidate["status"] = "HOLD"
                        candidate["hold_reason"] = "source corrected" if dependent else "incomplete use record"
                        self._write(db, "candidates", logical_id, task_id, candidate, cid)
                self._event(db, logical_id, task_id, "SOURCE_CORRECTED", {
                    "source_id": source_id, "fields": changed, "revision": source["revision"],
                    "user_receipt": user_receipt,"before":before,"after":source})
            return json.loads(_json(source))

    def snapshot(self, logical_id: str, task_id: str) -> dict:
        with self._tx() as db:
            state = self._load(db, "tasks", logical_id, task_id)
            sources = self._all(db, "sources", logical_id, task_id)
            review_hash = self._review_state_hash(state, sources)
            state["sources"] = sources
            state["review_state_hash"] = review_hash
            return state

    def _review_state_hash(self, state: dict, sources: dict) -> str:
        """Bind a host's review to this task and every source field revision.

        This is a state comparison, not proof that the host performed a review.
        Source values change only together with their field revisions.
        """
        revisions = {sid: {"revision": source["revision"], "fields": {
            field: item["revision"] for field, item in source["fields"].items()}}
            for sid, source in sources.items()}
        raw = _json({"owner_id": self.owner_id, "task": state,
                     "source_revisions": revisions})
        return sha256(raw.encode()).hexdigest()

    def begin_run(self, logical_id: str, task_id: str, *, model_id: str, session_id: str) -> RunBinding:
        _text(model_id); _text(session_id)
        with self._tx() as db:
            state = self._load(db, "tasks", logical_id, task_id)
            if state["phase"] != "ACTIVE":
                raise EngineHold("task not active")
            state["run_epoch"] += 1
            binding = RunBinding(self.owner_id,logical_id,task_id,uuid4().hex,
                state["run_epoch"],state["goal_revision"],model_id,session_id)
            state["current_run"] = dict(run_id=binding.run_id,model_id=model_id,
                                        session_id=session_id,delivered_refs={})
            self._write(db, "tasks", logical_id, task_id, state)
            self._event(db, logical_id, task_id, "RUN_STARTED", dict(
                run_id=binding.run_id,run_epoch=binding.run_epoch,model_id=model_id,session_id=session_id))
            return binding

    def model_port(self, binding: RunBinding) -> ModelPort:
        return ModelPort(self, binding)

    def _binding(self, db, binding: RunBinding) -> dict:
        if binding.owner_id != self.owner_id:
            raise EngineHold("binding belongs to another owner")
        state = self._load(db, "tasks", binding.logical_id, binding.task_id)
        current = state["current_run"]
        if state["phase"] != "ACTIVE" or state["goal_revision"] != binding.goal_revision or \
                state["run_epoch"] != binding.run_epoch or current is None or \
                any(current.get(key) != value for key,value in dict(run_id=binding.run_id,
                    model_id=binding.model_id,session_id=binding.session_id).items()):
            raise EngineHold("old, paused or replaced run")
        return state

    def _read_contract(self, binding: RunBinding, source_selection=None,
                       *, record_delivery: bool = True) -> dict:
        if type(record_delivery) is not bool:
            raise ValueError("explicit delivery flag required")
        with self._tx() as db:
            state = self._binding(db, binding)
            available = self._all(db, "sources", binding.logical_id, binding.task_id)
            if source_selection is None:
                selection={sid:list(source["fields"]) for sid,source in available.items()}
            else:
                if not isinstance(source_selection,dict):
                    raise ValueError("source selection mapping required")
                selection=source_selection
            selected={}
            for sid,fields in selection.items():
                if sid not in available or not isinstance(fields,list) or not fields or \
                        len(fields)!=len(set(fields)):
                    raise EngineHold("unknown source or invalid selected fields")
                chosen={}
                for field in fields:
                    if field not in available[sid]["fields"]:
                        raise EngineHold("unknown selected field")
                    item=available[sid]["fields"][field]
                    chosen[field]=item
                    if record_delivery:
                        delivered=state["current_run"]["delivered_refs"].setdefault(sid,{})
                        old=delivered.get(field)
                        if old is not None and old!=item["revision"]:
                            raise EngineHold("previously delivered field changed; start a new run")
                        delivered[field]=item["revision"]
                selected[sid]={"source_id":sid,"revision":available[sid]["revision"],"fields":chosen}
            if record_delivery:
                self._write(db,"tasks",binding.logical_id,binding.task_id,state)
            state["sources"] = selected
            return state

    def _refs(self, db, logical_id, task_id, refs) -> None:
        if not isinstance(refs, dict):
            raise ValueError("source reference mapping required")
        for source_id, fields in refs.items():
            _text(source_id)
            if not isinstance(fields, dict) or not fields:
                raise ValueError("explicit consumed fields required")
            source = self._load(db, "sources", logical_id, task_id, source_id)
            for field, revision in fields.items():
                _text(field); _positive(revision)
                if source["fields"].get(field, {}).get("revision") != revision:
                    raise EngineHold("source field absent or changed")

    def _submit(self, binding, *, content, purpose, source_refs, consumption_complete, goal_ids) -> str:
        _text(purpose)
        if type(consumption_complete) is not bool:
            raise ValueError("explicit use-record completeness required")
        raw = _json(content)
        if len(raw.encode()) > 2_000_000:
            raise ValueError("candidate too large")
        with self._tx() as db:
            state = self._binding(db, binding)
            if not isinstance(goal_ids, list) or len(goal_ids) != len(state["goals"]) or \
                    set(goal_ids) != set(state["goals"]):
                raise EngineHold("candidate must account for every current goal")
            if not isinstance(source_refs,dict):
                raise ValueError("source reference mapping required")
            source_refs=json.loads(_json(source_refs))
            # Providing a source field to a model is actual model input even
            # when its final answer does not cite that field. Bind it in host
            # code; a model cannot erase it by claiming non-use.
            for sid,fields in state["current_run"]["delivered_refs"].items():
                target=source_refs.setdefault(sid,{})
                if not isinstance(target,dict):
                    raise ValueError("source field references required")
                for field,revision in fields.items():
                    if field in target and target[field]!=revision:
                        raise EngineHold("model reference contradicts delivered revision")
                    target[field]=revision
            self._refs(db, binding.logical_id, binding.task_id, source_refs)
            cid = uuid4().hex
            candidate = dict(candidate_id=cid,content=json.loads(raw),content_raw=raw,
                content_hash=sha256(raw.encode()).hexdigest(),purpose=purpose,
                source_refs=source_refs,consumption_complete=consumption_complete,
                goal_ids=goal_ids,goal_revision=binding.goal_revision,run_id=binding.run_id,
                run_epoch=binding.run_epoch,status="DRAFT",source_trace_verified=False)
            self._write(db,"candidates",binding.logical_id,binding.task_id,candidate,cid)
            self._event(db,binding.logical_id,binding.task_id,"CANDIDATE_SAVED",candidate)
            return cid

    def candidate(self, logical_id: str, task_id: str, candidate_id: str) -> dict:
        with self._tx() as db:
            self._load(db,"tasks",logical_id,task_id)
            return self._load(db,"candidates",logical_id,task_id,candidate_id)

    def _current_candidate(self, db, logical_id, task_id, candidate_id) -> tuple[dict,dict]:
        state = self._load(db,"tasks",logical_id,task_id)
        candidate = self._load(db,"candidates",logical_id,task_id,candidate_id)
        run = state["current_run"]
        if state["phase"] != "ACTIVE" or candidate["status"] not in {"DRAFT","APPROVED"} or \
                not candidate["consumption_complete"] or run is None or \
                candidate["goal_revision"] != state["goal_revision"] or \
                candidate["run_epoch"] != state["run_epoch"] or candidate["run_id"] != run["run_id"]:
            raise EngineHold("candidate held, incomplete, consumed or no longer current")
        self._refs(db,logical_id,task_id,candidate["source_refs"])
        if sha256(candidate["content_raw"].encode()).hexdigest() != candidate["content_hash"]:
            raise EngineHold("candidate content integrity failed")
        return state,candidate

    def review_candidate(self, logical_id: str, task_id: str, *, candidate_id: str,
                         checks: dict[str,bool], reviewer: str, receipt: str,
                         expected_hash: str | None = None,
                         obligation_checks: dict[str,bool] | None = None) -> str:
        """Host-only checks. This kernel cannot establish semantic truth."""
        _checks(checks,self.REVIEW_CHECKS);_text(reviewer);_text(receipt)
        with self._tx() as db:
            state,candidate = self._current_candidate(db,logical_id,task_id,candidate_id)
            obligation_checks={} if obligation_checks is None else obligation_checks
            self._obligation_checks(state,obligation_checks)
            if expected_hash is not None and expected_hash != candidate["content_hash"]:
                raise EngineHold("reviewed content hash differs")
            aid = uuid4().hex
            approval = {key:candidate[key] for key in ("candidate_id","content_hash","purpose",
                "source_refs","goal_revision","run_epoch","run_id")}
            approval.update(approval_id=aid,checks=checks,reviewer=reviewer,receipt=receipt,used=False)
            approval.update(obligations=state["obligations"],obligation_checks=obligation_checks)
            self._write(db,"approvals",logical_id,task_id,approval,aid)
            candidate["status"]="APPROVED"
            candidate["source_trace_verified"]=True
            self._write(db,"candidates",logical_id,task_id,candidate,candidate_id)
            self._event(db,logical_id,task_id,"HOST_REVIEWED",approval)
            return aid

    @staticmethod
    def _obligation_checks(state: dict, checks: dict[str,bool]) -> None:
        if not isinstance(checks,dict) or set(checks)!=set(state["obligations"]) or \
                any(value is not True for value in checks.values()):
            raise EngineHold("every current obligation needs an explicit host check")

    def consume(self, logical_id: str, task_id: str, *, candidate_id: str,
                approval_id: str, purpose: str) -> dict:
        """One serialized final admission, no remote call or external execution.

        A later correction cannot undo an effect already performed by a caller.
        The caller must preserve its own external idempotency and effect receipt.
        """
        _text(purpose)
        with self._tx() as db:
            state,candidate = self._current_candidate(db,logical_id,task_id,candidate_id)
            approval=self._load(db,"approvals",logical_id,task_id,approval_id)
            if approval["used"] or purpose != candidate["purpose"] or any(
                    approval[key] != candidate[key] for key in ("candidate_id","content_hash",
                    "purpose","source_refs","goal_revision","run_epoch","run_id")):
                raise EngineHold("approval does not authorize this exact current consumption")
            _checks(approval["checks"],self.REVIEW_CHECKS)
            if approval["obligations"]!=state["obligations"]:
                raise EngineHold("approval obligations differ from current obligations")
            self._obligation_checks(state,approval["obligation_checks"])
            candidate["status"]="CONSUMED";approval["used"]=True
            self._write(db,"candidates",logical_id,task_id,candidate,candidate_id)
            self._write(db,"approvals",logical_id,task_id,approval,approval_id)
            result={key:candidate[key] for key in ("candidate_id","content","content_hash",
                    "purpose","goal_revision","run_epoch")}
            result.update(status="CONSUMED",approval_id=approval_id,
                fact_status="HOST_REVIEWED" if purpose=="fact" else "NOT_ASSERTED")
            self._event(db,logical_id,task_id,"CONSUMED",result)
            return result

    def _suspend(self, logical_id, task_id, phase, reason, receipt):
        _text(reason);_text(receipt)
        with self._tx() as db:
            state=self._load(db,"tasks",logical_id,task_id)
            before=json.loads(_json(state))
            state["phase"]=phase;state["run_epoch"]+=1;state["current_run"]=None
            self._write(db,"tasks",logical_id,task_id,state)
            self._event(db,logical_id,task_id,phase,{"reason":reason,"receipt":receipt,
                                                  "before":before,"after":state})
            return state

    def pause(self, logical_id: str, task_id: str, *, reason: str, receipt: str) -> dict:
        return self._suspend(logical_id,task_id,"PAUSED",reason,receipt)

    def quarantine(self, logical_id: str, task_id: str, *, reason: str, receipt: str) -> dict:
        return self._suspend(logical_id,task_id,"QUARANTINED",reason,receipt)

    def resume_after_check(self, logical_id: str, task_id: str, *, review_state_hash: str,
                           checks: dict[str,bool], reviewer: str, receipt: str) -> dict:
        """Resume only the exact snapshot reviewed by the trusted host."""
        _text(review_state_hash)
        _checks(checks,self.RESUME_CHECKS);_text(reviewer);_text(receipt)
        with self._tx() as db:
            state=self._load(db,"tasks",logical_id,task_id)
            before=json.loads(_json(state))
            if state["phase"] not in {"PAUSED","QUARANTINED"}:
                raise EngineHold("task is not suspended")
            sources=self._all(db,"sources",logical_id,task_id)
            if review_state_hash != self._review_state_hash(state,sources):
                raise EngineHold("reviewed state changed; inspect a fresh snapshot")
            state["phase"]="ACTIVE";state["run_epoch"]+=1;state["current_run"]=None
            self._write(db,"tasks",logical_id,task_id,state)
            self._event(db,logical_id,task_id,"CHECKED_RESUME",{
                "checks":checks,"reviewer":reviewer,"receipt":receipt,
                "review_state_hash":review_state_hash,"before":before,"after":state})
            return state

    def history(self, logical_id: str, task_id: str) -> list[dict]:
        with self._tx() as db:
            self._load(db,"tasks",logical_id,task_id)
            rows=db.execute("SELECT seq,kind,payload,created FROM dk_events WHERE owner=? "
                "AND logical_id=? AND task_id=? ORDER BY seq",self._key(logical_id,task_id)).fetchall()
            return [dict(seq=seq,kind=kind,payload=json.loads(raw),created=created)
                    for seq,kind,raw,created in rows]
