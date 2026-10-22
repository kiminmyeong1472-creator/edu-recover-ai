"""Persistent logical-responsibility control for replaceable AI execution instances."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

from app.services.secret_vault import state_root

_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class LogicalResponsibilityError(ValueError):
    pass


class GoalStatus(StrEnum):
    OPEN = "OPEN"
    COMPLETED = "COMPLETED"
    CANCELLED_BY_OWNER = "CANCELLED_BY_OWNER"


class ExecutionState(StrEnum):
    ALLOWED = "ALLOWED"
    STALE_RUN = "STALE_RUN"
    UNKNOWN_GOAL = "UNKNOWN_GOAL"
    GOAL_NOT_OPEN = "GOAL_NOT_OPEN"
    GOAL_CHANGE_REQUIRES_OWNER = "GOAL_CHANGE_REQUIRES_OWNER"


@dataclass(frozen=True)
class ExecutionDecision:
    allowed: bool
    state: str
    logical_id: str
    task_id: str
    run_id: str
    goal_id: str
    control_revision: int
    reason: str


@dataclass(frozen=True)
class HandoffPacket:
    logical_id: str
    task_id: str
    revision: int
    run_id: str
    previous_run_id: str | None
    goals: tuple[dict[str, Any], ...]
    constraints: tuple[str, ...]
    corrections: tuple[dict[str, str], ...]


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS logical_states(
 logical_id TEXT NOT NULL,
 task_id TEXT NOT NULL,
 revision INTEGER NOT NULL,
 goals_json TEXT NOT NULL,
 constraints_json TEXT NOT NULL,
 corrections_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(logical_id, task_id)
);
CREATE TABLE IF NOT EXISTS logical_revisions(
 logical_id TEXT NOT NULL,
 task_id TEXT NOT NULL,
 revision INTEGER NOT NULL,
 event_type TEXT NOT NULL,
 authority_ref TEXT NOT NULL,
 payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(logical_id, task_id, revision)
);
CREATE TABLE IF NOT EXISTS logical_runs(
 run_id TEXT PRIMARY KEY,
 logical_id TEXT NOT NULL,
 task_id TEXT NOT NULL,
 bound_revision INTEGER NOT NULL,
 agent_id TEXT NOT NULL,
 session_id TEXT NOT NULL,
 model_id TEXT NOT NULL,
 previous_run_id TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS logical_model_revisions(
 logical_id TEXT NOT NULL,
 task_id TEXT NOT NULL,
 model_revision TEXT NOT NULL,
 trained_control_revision INTEGER NOT NULL,
 dataset_sha256 TEXT NOT NULL,
 verification_receipt TEXT NOT NULL,
 verification_passed INTEGER NOT NULL,
 active INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL,
 activated_at TEXT,
 PRIMARY KEY(logical_id, task_id, model_revision)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_id(value: str, field: str) -> str:
    value = str(value).strip()
    if not _ID_RE.fullmatch(value):
        raise LogicalResponsibilityError(f"INVALID_{field.upper()}")
    return value


def _require_text(value: str, field: str, *, max_length: int = 4000) -> str:
    value = str(value).strip()
    if not value or len(value) > max_length:
        raise LogicalResponsibilityError(f"INVALID_{field.upper()}")
    return value


def _require_sha256(value: str) -> str:
    value = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(value):
        raise LogicalResponsibilityError("INVALID_DATASET_SHA256")
    return value


class LogicalResponsibilityStore:
    """SQLite-backed logical subject, run binding, handoff and model-revision gate."""

    def __init__(self, path: Path | None = None):
        self.path = path or state_root() / "logical_responsibility.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise RuntimeError("LOGICAL_RESPONSIBILITY_DB_SYMLINK_FORBIDDEN")
        self.path.touch(exist_ok=True)
        self.path.chmod(0o600)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def _normalize_goals(goals: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in goals:
            item = dict(raw)
            goal_id = _require_id(item.get("goal_id", ""), "goal_id")
            if goal_id in seen:
                raise LogicalResponsibilityError("DUPLICATE_GOAL_ID")
            seen.add(goal_id)
            text = _require_text(item.get("text", ""), "goal_text")
            priority = item.get("priority")
            if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 100000:
                raise LogicalResponsibilityError("INVALID_GOAL_PRIORITY")
            status = str(item.get("status") or GoalStatus.OPEN.value)
            if status not in {member.value for member in GoalStatus}:
                raise LogicalResponsibilityError("INVALID_GOAL_STATUS")
            evidence = item.get("completion_evidence_ref")
            if evidence is not None:
                evidence = _require_text(evidence, "completion_evidence_ref", max_length=1000)
            if status == GoalStatus.COMPLETED.value and not evidence:
                raise LogicalResponsibilityError("COMPLETION_EVIDENCE_REQUIRED")
            result.append({
                "goal_id": goal_id,
                "text": text,
                "priority": priority,
                "status": status,
                "completion_evidence_ref": evidence,
            })
        if not result:
            raise LogicalResponsibilityError("AT_LEAST_ONE_GOAL_REQUIRED")
        return sorted(result, key=lambda row: (row["priority"], row["goal_id"]))

    def _active_model(self, db: sqlite3.Connection, logical_id: str, task_id: str) -> str | None:
        row = db.execute(
            "SELECT model_revision FROM logical_model_revisions WHERE logical_id=? AND task_id=? AND active=1",
            (logical_id, task_id),
        ).fetchone()
        return None if row is None else str(row["model_revision"])

    def get_state(self, logical_id: str, task_id: str) -> dict[str, Any]:
        logical_id = _require_id(logical_id, "logical_id")
        task_id = _require_id(task_id, "task_id")
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM logical_states WHERE logical_id=? AND task_id=?",
                (logical_id, task_id),
            ).fetchone()
            if row is None:
                raise LogicalResponsibilityError("LOGICAL_SUBJECT_NOT_FOUND")
            return {
                "logical_id": row["logical_id"],
                "task_id": row["task_id"],
                "revision": row["revision"],
                "goals": json.loads(row["goals_json"]),
                "constraints": json.loads(row["constraints_json"]),
                "corrections": json.loads(row["corrections_json"]),
                "active_model_revision": self._active_model(db, logical_id, task_id),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def create_subject(
        self,
        *,
        logical_id: str,
        task_id: str,
        goals: Iterable[dict[str, Any]],
        constraints: Iterable[str] = (),
        corrections: Iterable[dict[str, str]] = (),
        authority_ref: str,
    ) -> dict[str, Any]:
        logical_id = _require_id(logical_id, "logical_id")
        task_id = _require_id(task_id, "task_id")
        authority_ref = _require_text(authority_ref, "authority_ref", max_length=1000)
        goal_rows = self._normalize_goals(goals)
        constraint_rows = [_require_text(v, "constraint") for v in constraints]
        correction_rows = [{
            "correction_id": _require_id(item.get("correction_id", ""), "correction_id"),
            "text": _require_text(item.get("text", ""), "correction_text"),
            "authority_ref": authority_ref,
        } for item in corrections]
        now = _now()
        with self._db() as db:
            try:
                db.execute(
                    "INSERT INTO logical_states VALUES(?,?,?,?,?,?,?,?)",
                    (logical_id, task_id, 1,
                     json.dumps(goal_rows, ensure_ascii=False, sort_keys=True),
                     json.dumps(constraint_rows, ensure_ascii=False),
                     json.dumps(correction_rows, ensure_ascii=False, sort_keys=True),
                     now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise LogicalResponsibilityError("LOGICAL_SUBJECT_ALREADY_EXISTS") from exc
            db.execute(
                "INSERT INTO logical_revisions VALUES(?,?,?,?,?,?,?)",
                (logical_id, task_id, 1, "CREATE", authority_ref,
                 json.dumps({"goals": goal_rows, "constraints": constraint_rows,
                             "corrections": correction_rows},
                            ensure_ascii=False, sort_keys=True), now),
            )
            db.commit()
        return self.get_state(logical_id, task_id)

    def owner_revise(
        self,
        *,
        logical_id: str,
        task_id: str,
        expected_revision: int,
        authority_ref: str,
        add_goals: Iterable[dict[str, Any]] = (),
        priority_updates: dict[str, int] | None = None,
        cancel_goal_ids: Iterable[str] = (),
        add_constraints: Iterable[str] = (),
        corrections: Iterable[dict[str, str]] = (),
    ) -> dict[str, Any]:
        authority_ref = _require_text(authority_ref, "authority_ref", max_length=1000)
        state = self.get_state(logical_id, task_id)
        if state["revision"] != expected_revision:
            raise LogicalResponsibilityError("STALE_EXPECTED_REVISION")
        goals = {row["goal_id"]: dict(row) for row in state["goals"]}
        add_goals = list(add_goals)
        cancel_goal_ids = list(cancel_goal_ids)
        add_constraints = list(add_constraints)
        corrections = list(corrections)
        for row in add_goals:
            normalized = self._normalize_goals([row])[0]
            if normalized["goal_id"] in goals:
                raise LogicalResponsibilityError("DUPLICATE_GOAL_ID")
            goals[normalized["goal_id"]] = normalized
        for goal_id, priority in (priority_updates or {}).items():
            goal_id = _require_id(goal_id, "goal_id")
            if goal_id not in goals:
                raise LogicalResponsibilityError("UNKNOWN_GOAL")
            if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 100000:
                raise LogicalResponsibilityError("INVALID_GOAL_PRIORITY")
            goals[goal_id]["priority"] = priority
        for goal_id in cancel_goal_ids:
            goal_id = _require_id(goal_id, "goal_id")
            if goal_id not in goals:
                raise LogicalResponsibilityError("UNKNOWN_GOAL")
            if goals[goal_id]["status"] == GoalStatus.COMPLETED.value:
                raise LogicalResponsibilityError("COMPLETED_GOAL_CANNOT_BE_CANCELLED")
            goals[goal_id]["status"] = GoalStatus.CANCELLED_BY_OWNER.value
            goals[goal_id]["completion_evidence_ref"] = None
        constraints = list(state["constraints"])
        constraints.extend(_require_text(v, "constraint") for v in add_constraints)
        correction_rows = list(state["corrections"])
        seen = {row["correction_id"] for row in correction_rows}
        for item in corrections:
            correction_id = _require_id(item.get("correction_id", ""), "correction_id")
            if correction_id in seen:
                raise LogicalResponsibilityError("DUPLICATE_CORRECTION_ID")
            seen.add(correction_id)
            correction_rows.append({
                "correction_id": correction_id,
                "text": _require_text(item.get("text", ""), "correction_text"),
                "authority_ref": authority_ref,
            })
        goal_rows = sorted(goals.values(), key=lambda row: (row["priority"], row["goal_id"]))
        new_revision = expected_revision + 1
        now = _now()
        with self._db() as db:
            changed = db.execute(
                "UPDATE logical_states SET revision=?,goals_json=?,constraints_json=?,corrections_json=?,updated_at=? "
                "WHERE logical_id=? AND task_id=? AND revision=?",
                (new_revision,
                 json.dumps(goal_rows, ensure_ascii=False, sort_keys=True),
                 json.dumps(constraints, ensure_ascii=False),
                 json.dumps(correction_rows, ensure_ascii=False, sort_keys=True),
                 now, logical_id, task_id, expected_revision),
            ).rowcount
            if changed != 1:
                raise LogicalResponsibilityError("REVISION_CONFLICT")
            db.execute(
                "INSERT INTO logical_revisions VALUES(?,?,?,?,?,?,?)",
                (logical_id, task_id, new_revision, "OWNER_REVISION", authority_ref,
                 json.dumps({
                     "add_goals": add_goals,
                     "priority_updates": priority_updates or {},
                     "cancel_goal_ids": cancel_goal_ids,
                     "add_constraints": add_constraints,
                     "corrections": corrections,
                 }, ensure_ascii=False, sort_keys=True), now),
            )
            db.commit()
        return self.get_state(logical_id, task_id)

    def bind_run(
        self, *, logical_id: str, task_id: str, agent_id: str, session_id: str,
        model_id: str, run_id: str | None = None, previous_run_id: str | None = None,
    ) -> HandoffPacket:
        state = self.get_state(logical_id, task_id)
        run_id = _require_id(run_id or f"run-{uuid.uuid4()}", "run_id")
        agent_id = _require_id(agent_id, "agent_id")
        session_id = _require_id(session_id, "session_id")
        model_id = _require_id(model_id, "model_id")
        if previous_run_id is not None:
            previous_run_id = _require_id(previous_run_id, "previous_run_id")
        with self._db() as db:
            try:
                db.execute(
                    "INSERT INTO logical_runs VALUES(?,?,?,?,?,?,?,?,?)",
                    (run_id, logical_id, task_id, state["revision"], agent_id, session_id,
                     model_id, previous_run_id, _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise LogicalResponsibilityError("RUN_ID_ALREADY_EXISTS") from exc
            db.commit()
        return HandoffPacket(
            logical_id=logical_id,
            task_id=task_id,
            revision=state["revision"],
            run_id=run_id,
            previous_run_id=previous_run_id,
            goals=tuple(row for row in state["goals"] if row["status"] == GoalStatus.OPEN.value),
            constraints=tuple(state["constraints"]),
            corrections=tuple(state["corrections"]),
        )

    def handoff(
        self, *, previous_run_id: str, agent_id: str, session_id: str,
        model_id: str, run_id: str | None = None,
    ) -> HandoffPacket:
        previous_run_id = _require_id(previous_run_id, "previous_run_id")
        with self._db() as db:
            previous = db.execute("SELECT * FROM logical_runs WHERE run_id=?",
                                  (previous_run_id,)).fetchone()
        if previous is None:
            raise LogicalResponsibilityError("PREVIOUS_RUN_NOT_FOUND")
        return self.bind_run(
            logical_id=str(previous["logical_id"]),
            task_id=str(previous["task_id"]),
            agent_id=agent_id,
            session_id=session_id,
            model_id=model_id,
            run_id=run_id,
            previous_run_id=previous_run_id,
        )

    def _run_and_state(self, run_id: str) -> tuple[sqlite3.Row, dict[str, Any]]:
        run_id = _require_id(run_id, "run_id")
        with self._db() as db:
            run = db.execute("SELECT * FROM logical_runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise LogicalResponsibilityError("RUN_NOT_FOUND")
        return run, self.get_state(str(run["logical_id"]), str(run["task_id"]))

    def authorize_execution(
        self, *, run_id: str, goal_id: str, proposes_goal_change: bool = False,
    ) -> ExecutionDecision:
        goal_id = _require_id(goal_id, "goal_id")
        run, state = self._run_and_state(run_id)
        common = dict(
            logical_id=str(run["logical_id"]),
            task_id=str(run["task_id"]),
            run_id=str(run["run_id"]),
            goal_id=goal_id,
            control_revision=int(state["revision"]),
        )
        if int(run["bound_revision"]) != int(state["revision"]):
            return ExecutionDecision(False, ExecutionState.STALE_RUN.value,
                                     reason="Run is bound to an older logical control revision.", **common)
        goal = next((row for row in state["goals"] if row["goal_id"] == goal_id), None)
        if goal is None:
            return ExecutionDecision(False, ExecutionState.UNKNOWN_GOAL.value,
                                     reason="Goal is not part of the logical responsibility state.", **common)
        if goal["status"] != GoalStatus.OPEN.value:
            return ExecutionDecision(False, ExecutionState.GOAL_NOT_OPEN.value,
                                     reason="Only unfinished open responsibility may be executed.", **common)
        if proposes_goal_change:
            return ExecutionDecision(False, ExecutionState.GOAL_CHANGE_REQUIRES_OWNER.value,
                                     reason="An execution instance cannot change the user's goal state.", **common)
        return ExecutionDecision(True, ExecutionState.ALLOWED.value,
                                 reason="Current run and unfinished goal match the active logical revision.", **common)

    def record_completion(self, *, run_id: str, goal_id: str, evidence_ref: str) -> dict[str, Any]:
        evidence_ref = _require_text(evidence_ref, "evidence_ref", max_length=1000)
        decision = self.authorize_execution(run_id=run_id, goal_id=goal_id)
        if not decision.allowed:
            raise LogicalResponsibilityError(decision.state)
        state = self.get_state(decision.logical_id, decision.task_id)
        goals = [dict(row) for row in state["goals"]]
        for row in goals:
            if row["goal_id"] == goal_id:
                row["status"] = GoalStatus.COMPLETED.value
                row["completion_evidence_ref"] = evidence_ref
                break
        new_revision = state["revision"] + 1
        now = _now()
        with self._db() as db:
            changed = db.execute(
                "UPDATE logical_states SET revision=?,goals_json=?,updated_at=? "
                "WHERE logical_id=? AND task_id=? AND revision=?",
                (new_revision, json.dumps(goals, ensure_ascii=False, sort_keys=True), now,
                 decision.logical_id, decision.task_id, state["revision"]),
            ).rowcount
            if changed != 1:
                raise LogicalResponsibilityError("REVISION_CONFLICT")
            db.execute(
                "INSERT INTO logical_revisions VALUES(?,?,?,?,?,?,?)",
                (decision.logical_id, decision.task_id, new_revision, "VERIFIED_COMPLETION",
                 "EXECUTION_EVIDENCE",
                 json.dumps({"goal_id": goal_id, "evidence_ref": evidence_ref},
                            ensure_ascii=False, sort_keys=True), now),
            )
            db.commit()
        return self.get_state(decision.logical_id, decision.task_id)

    def register_model_revision(
        self, *, logical_id: str, task_id: str, model_revision: str,
        trained_control_revision: int, dataset_sha256: str,
        verification_receipt: str, verification_passed: bool,
    ) -> dict[str, Any]:
        self.get_state(logical_id, task_id)
        model_revision = _require_id(model_revision, "model_revision")
        dataset_sha256 = _require_sha256(dataset_sha256)
        verification_receipt = _require_text(verification_receipt, "verification_receipt", max_length=4000)
        if not isinstance(verification_passed, bool):
            raise LogicalResponsibilityError("INVALID_VERIFICATION_STATE")
        with self._db() as db:
            try:
                db.execute(
                    "INSERT INTO logical_model_revisions(logical_id,task_id,model_revision,trained_control_revision,"
                    "dataset_sha256,verification_receipt,verification_passed,active,created_at,activated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                    (logical_id, task_id, model_revision, trained_control_revision,
                     dataset_sha256, verification_receipt,
                     1 if verification_passed else 0, 0, _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise LogicalResponsibilityError("MODEL_REVISION_ALREADY_EXISTS") from exc
            db.commit()
        return {
            "logical_id": logical_id,
            "task_id": task_id,
            "model_revision": model_revision,
            "trained_control_revision": trained_control_revision,
            "dataset_sha256": dataset_sha256,
            "verification_passed": verification_passed,
            "active": False,
        }

    def activate_model_revision(self, *, logical_id: str, task_id: str,
                                model_revision: str) -> dict[str, Any]:
        state = self.get_state(logical_id, task_id)
        model_revision = _require_id(model_revision, "model_revision")
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM logical_model_revisions WHERE logical_id=? AND task_id=? AND model_revision=?",
                (logical_id, task_id, model_revision),
            ).fetchone()
            if row is None:
                raise LogicalResponsibilityError("MODEL_REVISION_NOT_FOUND")
            if not bool(row["verification_passed"]):
                raise LogicalResponsibilityError("MODEL_VERIFICATION_REQUIRED")
            if int(row["trained_control_revision"]) != int(state["revision"]):
                raise LogicalResponsibilityError("MODEL_CONTROL_REVISION_MISMATCH")
            db.execute(
                "UPDATE logical_model_revisions SET active=0,activated_at=NULL "
                "WHERE logical_id=? AND task_id=?",
                (logical_id, task_id),
            )
            db.execute(
                "UPDATE logical_model_revisions SET active=1,activated_at=? "
                "WHERE logical_id=? AND task_id=? AND model_revision=?",
                (_now(), logical_id, task_id, model_revision),
            )
            db.commit()
        return self.get_state(logical_id, task_id)


def dataset_digest(items: Any) -> str:
    payload = json.dumps(items, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
