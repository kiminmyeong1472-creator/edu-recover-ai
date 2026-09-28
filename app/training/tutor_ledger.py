"""Append-only evidence ledger connecting Classroom Tutor to the training plane."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import uuid
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.training.tutor_learning import (
    ACTIVE_TUTOR_PURPOSES,
    ContentTrustState,
    EligibilityState,
    EvidenceType,
    LearningPurpose,
    evaluate_purpose_eligibility,
    evaluate_tutor_reward,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _task_fingerprint(segment: dict[str, Any]) -> str:
    """Ignore revision/title changes when detecting a repeated assessment."""
    def normalized(value: Any) -> str:
        return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())
    return _canonical_hash({
        "text": normalized(segment.get("text")),
        "choices": sorted(normalized(x) for x in (segment.get("choices") or [])),
    })


SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS tutor_exposures (
  exposure_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  learner_key TEXT NOT NULL,
  segment_id TEXT NOT NULL,
  concept_key TEXT NOT NULL,
  content_revision_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  source_integrity_verified INTEGER NOT NULL,
  content_trust TEXT NOT NULL,
  pseudonym_salt_configured INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  task_fingerprint TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tutor_exposure_group
  ON tutor_exposures(session_id,learner_key,concept_key);
CREATE TABLE IF NOT EXISTS tutor_evidence_revisions (
  evidence_revision_id TEXT PRIMARY KEY,
  exposure_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  learner_key TEXT NOT NULL,
  concept_key TEXT NOT NULL,
  content_revision_id TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  response_ref TEXT,
  correct INTEGER,
  reason_present INTEGER NOT NULL,
  confidence INTEGER,
  hint_level INTEGER NOT NULL,
  answer_disclosed INTEGER NOT NULL,
  independent INTEGER NOT NULL,
  delayed_hours REAL NOT NULL,
  teacher_validated INTEGER NOT NULL,
  predecessor_revision_id TEXT,
  payload_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(exposure_id) REFERENCES tutor_exposures(exposure_id)
);
CREATE INDEX IF NOT EXISTS idx_tutor_evidence_group
  ON tutor_evidence_revisions(session_id,learner_key,concept_key,created_at);
CREATE TABLE IF NOT EXISTS tutor_content_status_revisions (
  status_revision_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  learner_key TEXT NOT NULL,
  concept_key TEXT NOT NULL,
  content_revision_id TEXT NOT NULL,
  source_integrity_verified INTEGER NOT NULL,
  content_trust TEXT NOT NULL,
  predecessor_revision_id TEXT,
  notes_hash TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tutor_content_status
  ON tutor_content_status_revisions(session_id,learner_key,concept_key,content_revision_id,created_at);
CREATE TABLE IF NOT EXISTS tutor_reward_revisions (
  reward_revision_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  learner_key TEXT NOT NULL,
  concept_key TEXT NOT NULL,
  learning_purpose TEXT NOT NULL,
  components_json TEXT NOT NULL,
  total REAL NOT NULL,
  hard_blocks_json TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tutor_eligibility_revisions (
  eligibility_revision_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  learner_key TEXT NOT NULL,
  concept_key TEXT NOT NULL,
  learning_purpose TEXT NOT NULL,
  state TEXT NOT NULL,
  reasons_json TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL,
  reward_revision_id TEXT NOT NULL,
  predecessor_revision_id TEXT,
  is_current INTEGER NOT NULL,
  decision_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(reward_revision_id) REFERENCES tutor_reward_revisions(reward_revision_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tutor_eligibility_current
  ON tutor_eligibility_revisions(session_id,learner_key,concept_key,learning_purpose)
  WHERE is_current=1;
CREATE TABLE IF NOT EXISTS tutor_training_use_receipts (
  receipt_id TEXT PRIMARY KEY,
  eligibility_revision_id TEXT NOT NULL,
  training_job_id TEXT NOT NULL,
  tutor_model_version TEXT NOT NULL,
  sample_hash TEXT NOT NULL,
  confirmed_actual_use INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(eligibility_revision_id) REFERENCES tutor_eligibility_revisions(eligibility_revision_id)
);
"""


class TutorLearningLedger:
    """Store only pseudonymous metadata; raw answers stay in the tutoring DB."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as conn:
            conn.executescript(SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tutor_exposures)")}
            if "task_fingerprint" not in columns:
                conn.execute("ALTER TABLE tutor_exposures ADD COLUMN task_fingerprint TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @property
    def pseudonym_salt_configured(self) -> bool:
        return bool(os.environ.get("EDURECOVER_TRAINING_PSEUDONYM_SALT", "").strip())

    def learner_key(self, session_id: str, student_alias: str) -> str:
        salt = os.environ.get("EDURECOVER_TRAINING_PSEUDONYM_SALT", "").strip()
        if salt:
            digest = hmac.new(salt.encode("utf-8"), f"{session_id}\x00{student_alias}".encode("utf-8"), hashlib.sha256).hexdigest()
        else:
            digest = hashlib.sha256(f"UNPROMOTABLE\x00{session_id}\x00{student_alias}".encode("utf-8")).hexdigest()
        return f"lrn_{digest[:24]}"

    def _insert_exposure(
        self,
        conn: sqlite3.Connection,
        *,
        session_id: str,
        learner_key: str,
        segment: dict[str, Any],
    ) -> str:
        exposure_id = f"exp_{uuid.uuid4().hex}"
        concept_key = str(segment.get("concept_key") or "UNSPECIFIED")
        content_revision_id = str(segment.get("content_revision_id") or f"{session_id}:{segment.get('segment_id')}:v1")
        content_hash = _canonical_hash({
            "segment_id": segment.get("segment_id"),
            "kind": segment.get("kind"),
            "title": segment.get("title"),
            "text": segment.get("text"),
            "choices": segment.get("choices") or [],
            "correct_answer": segment.get("correct_answer"),
            "content_revision_id": content_revision_id,
        })
        source_integrity = bool(segment.get("source_integrity_verified", False))
        trust = str(segment.get("content_trust") or ContentTrustState.UNVERIFIED.value)
        conn.execute(
            """INSERT INTO tutor_exposures VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                exposure_id, session_id, learner_key, str(segment.get("segment_id")), concept_key,
                content_revision_id, content_hash, int(source_integrity), trust,
                int(self.pseudonym_salt_configured), _now(), _task_fingerprint(segment),
            ),
        )
        return exposure_id

    def _insert_evidence(
        self,
        conn: sqlite3.Connection,
        *,
        exposure_id: str,
        session_id: str,
        learner_key: str,
        concept_key: str,
        content_revision_id: str,
        evidence_type: EvidenceType,
        response_ref: str | None,
        correct: bool | None,
        reason_present: bool,
        confidence: int | None,
        hint_level: int = 0,
        answer_disclosed: bool = False,
        independent: bool = False,
        delayed_hours: float = 0.0,
        teacher_validated: bool = False,
        predecessor_revision_id: str | None = None,
    ) -> str:
        evidence_revision_id = f"evr_{uuid.uuid4().hex}"
        payload = {
            "exposure_id": exposure_id,
            "evidence_type": evidence_type.value,
            "response_ref": response_ref,
            "correct": correct,
            "reason_present": reason_present,
            "confidence": confidence,
            "hint_level": hint_level,
            "answer_disclosed": answer_disclosed,
            "independent": independent,
            "delayed_hours": delayed_hours,
            "teacher_validated": teacher_validated,
            "predecessor_revision_id": predecessor_revision_id,
        }
        conn.execute(
            """INSERT INTO tutor_evidence_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                evidence_revision_id, exposure_id, session_id, learner_key, concept_key,
                content_revision_id, evidence_type.value, response_ref,
                None if correct is None else int(correct), int(reason_present), confidence,
                int(hint_level), int(answer_disclosed), int(independent), float(delayed_hours),
                int(teacher_validated), predecessor_revision_id, _canonical_hash(payload), _now(),
            ),
        )
        return evidence_revision_id

    def _existing_initial_incorrect(self, conn: sqlite3.Connection, session_id: str, learner_key: str, concept_key: str) -> bool:
        row = conn.execute(
            """SELECT 1 FROM tutor_evidence_revisions WHERE session_id=? AND learner_key=? AND concept_key=?
               AND evidence_type=? AND correct=0 LIMIT 1""",
            (session_id, learner_key, concept_key, EvidenceType.PRE_FEEDBACK_RESPONSE.value),
        ).fetchone()
        return bool(row)

    def capture_response(
        self,
        *,
        session_id: str,
        student_alias: str,
        segment: dict[str, Any],
        response_ref: str,
        correct: bool | None,
        reason_present: bool,
        confidence: int | None,
    ) -> dict[str, Any]:
        learner_key = self.learner_key(session_id, student_alias)
        concept_key = str(segment.get("concept_key") or "UNSPECIFIED")
        content_revision_id = str(segment.get("content_revision_id") or f"{session_id}:{segment.get('segment_id')}:v1")
        kind = str(segment.get("kind") or "")
        phase = str(segment.get("feedback_phase") or "")
        hint_level = max(0, int(segment.get("hint_level") or 0))
        answer_disclosed = bool(segment.get("answer_disclosed", False))
        independent = bool(segment.get("independent_followup", False)) and hint_level == 0 and not answer_disclosed
        delayed_hours = max(0.0, float(segment.get("delayed_hours") or 0))

        with self._db() as conn:
            history = conn.execute(
                "SELECT task_fingerprint,created_at FROM tutor_exposures WHERE session_id=? AND learner_key=? AND concept_key=?",
                (session_id, learner_key, concept_key),
            ).fetchall()
            fingerprint = _task_fingerprint(segment)
            if not str(segment.get("text") or "").strip() or any(
                not row["task_fingerprint"] or row["task_fingerprint"] == fingerprint for row in history
            ):
                independent = False
            if history:
                first_at = min(datetime.fromisoformat(row["created_at"]) for row in history)
                elapsed_hours = max(0.0, (datetime.now(timezone.utc) - first_at).total_seconds() / 3600)
                delayed_hours = min(delayed_hours, elapsed_hours)
            else:
                delayed_hours = 0.0
            if independent and delayed_hours >= 24:
                evidence_type = EvidenceType.DELAYED_FOLLOWUP_RESPONSE
            elif independent:
                evidence_type = EvidenceType.INDEPENDENT_FOLLOWUP_RESPONSE
            elif segment.get("independent_followup") or kind == "RETEST":
                evidence_type = EvidenceType.RETRY_RESPONSE
            elif phase == "PRE_FEEDBACK" and not answer_disclosed and hint_level == 0:
                evidence_type = EvidenceType.PRE_FEEDBACK_RESPONSE
            elif kind in {"VIDEO", "EXPLANATION", "WORKED_EXAMPLE"}:
                evidence_type = EvidenceType.HINT_USE
            else:
                evidence_type = EvidenceType.RETRY_RESPONSE
            exposure_id = self._insert_exposure(conn, session_id=session_id, learner_key=learner_key, segment=segment)
            evidence_revision_id = self._insert_evidence(
                conn,
                exposure_id=exposure_id,
                session_id=session_id,
                learner_key=learner_key,
                concept_key=concept_key,
                content_revision_id=content_revision_id,
                evidence_type=evidence_type,
                response_ref=response_ref,
                correct=correct,
                reason_present=reason_present,
                confidence=confidence,
                hint_level=hint_level,
                answer_disclosed=answer_disclosed,
                independent=independent,
                delayed_hours=delayed_hours,
            )
            if correct is True and self._existing_initial_incorrect(conn, session_id, learner_key, concept_key):
                self._insert_evidence(
                    conn,
                    exposure_id=exposure_id,
                    session_id=session_id,
                    learner_key=learner_key,
                    concept_key=concept_key,
                    content_revision_id=content_revision_id,
                    evidence_type=EvidenceType.SELF_CORRECTION,
                    response_ref=response_ref,
                    correct=True,
                    reason_present=reason_present,
                    confidence=confidence,
                    hint_level=hint_level,
                    answer_disclosed=answer_disclosed,
                    independent=independent,
                    delayed_hours=delayed_hours,
                )
            self._reevaluate(conn, session_id, learner_key, concept_key)
        return {"learner_key": learner_key, "evidence_revision_id": evidence_revision_id, "status": "CAPTURED_NOT_AUTOMATICALLY_TRAINED"}

    def _latest_statuses(self, conn: sqlite3.Connection, session_id: str, learner_key: str, concept_key: str) -> dict[str, dict[str, Any]]:
        rows = conn.execute(
            """SELECT * FROM tutor_content_status_revisions WHERE session_id=? AND learner_key=? AND concept_key=?
               ORDER BY created_at ASC, rowid ASC""",
            (session_id, learner_key, concept_key),
        ).fetchall()
        return {str(row["content_revision_id"]): dict(row) for row in rows}

    def _evidence_rows(self, conn: sqlite3.Connection, session_id: str, learner_key: str, concept_key: str) -> list[dict[str, Any]]:
        rows = [dict(row) for row in conn.execute(
            """SELECT e.*,x.source_integrity_verified AS exposure_integrity,x.content_trust AS exposure_trust,
                      x.pseudonym_salt_configured
               FROM tutor_evidence_revisions e JOIN tutor_exposures x ON x.exposure_id=e.exposure_id
               WHERE e.session_id=? AND e.learner_key=? AND e.concept_key=?
               ORDER BY e.created_at,e.rowid""",
            (session_id, learner_key, concept_key),
        ).fetchall()]
        statuses = self._latest_statuses(conn, session_id, learner_key, concept_key)
        validations = {row["response_ref"]: row for row in rows
                       if row["evidence_type"] == EvidenceType.TEACHER_VALIDATION.value and row["response_ref"]}
        for row in rows:
            status = statuses.get(str(row["content_revision_id"]))
            row["source_integrity_verified"] = bool(status["source_integrity_verified"]) if status else bool(row["exposure_integrity"])
            row["content_trust"] = str(status["content_trust"]) if status else str(row["exposure_trust"])
            for key in ("correct", "reason_present", "answer_disclosed", "independent", "teacher_validated", "pseudonym_salt_configured"):
                if row.get(key) is not None:
                    row[key] = bool(row[key])
            validation = validations.get(row.get("response_ref"))
            if validation and validation["content_revision_id"] == row["content_revision_id"] and validation["correct"] is not None:
                row["correct"] = bool(validation["correct"])
                row["reason_present"] = bool(validation["reason_present"])
        return rows

    @staticmethod
    def _purpose_rows(purpose: LearningPurpose, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed = {
            LearningPurpose.MASTERY_MODEL: {
                EvidenceType.PRE_FEEDBACK_RESPONSE.value,
                EvidenceType.INDEPENDENT_FOLLOWUP_RESPONSE.value,
                EvidenceType.DELAYED_FOLLOWUP_RESPONSE.value,
                EvidenceType.TEACHER_VALIDATION.value,
                EvidenceType.EVIDENCE_CORRECTION.value,
            },
            LearningPurpose.ERROR_PATTERN_MODEL: {
                EvidenceType.PRE_FEEDBACK_RESPONSE.value,
                EvidenceType.TEACHER_VALIDATION.value,
                EvidenceType.EVIDENCE_CORRECTION.value,
            },
            LearningPurpose.TUTOR_POLICY_MODEL: {
                EvidenceType.HINT_USE.value,
                EvidenceType.SELF_CORRECTION.value,
                EvidenceType.INDEPENDENT_FOLLOWUP_RESPONSE.value,
                EvidenceType.DELAYED_FOLLOWUP_RESPONSE.value,
                EvidenceType.TEACHER_VALIDATION.value,
                EvidenceType.EVIDENCE_CORRECTION.value,
            },
            LearningPurpose.REVIEW_TIMING_MODEL: {
                EvidenceType.DELAYED_FOLLOWUP_RESPONSE.value,
                EvidenceType.TEACHER_VALIDATION.value,
                EvidenceType.EVIDENCE_CORRECTION.value,
            },
        }[purpose]
        return [row for row in rows if row.get("evidence_type") in allowed]

    def _reevaluate(self, conn: sqlite3.Connection, session_id: str, learner_key: str, concept_key: str) -> None:
        all_rows = self._evidence_rows(conn, session_id, learner_key, concept_key)
        for purpose in ACTIVE_TUTOR_PURPOSES:
            rows = self._purpose_rows(purpose, all_rows)
            reward = evaluate_tutor_reward(rows)
            reward_id = f"rwd_{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO tutor_reward_revisions VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    reward_id, session_id, learner_key, concept_key, purpose.value,
                    json.dumps(reward.components, ensure_ascii=False, sort_keys=True), reward.total,
                    json.dumps(reward.hard_block_reasons, ensure_ascii=False),
                    json.dumps(reward.evidence_refs, ensure_ascii=False), _now(),
                ),
            )
            decision = evaluate_purpose_eligibility(purpose, rows, reward)
            decision_hash = _canonical_hash({
                "state": decision.state.value,
                "reasons": decision.reasons,
                "evidence_refs": decision.evidence_refs,
                "reward_total": decision.reward_total,
            })
            previous = conn.execute(
                """SELECT * FROM tutor_eligibility_revisions WHERE session_id=? AND learner_key=? AND concept_key=?
                   AND learning_purpose=? AND is_current=1""",
                (session_id, learner_key, concept_key, purpose.value),
            ).fetchone()
            if previous and previous["decision_hash"] == decision_hash:
                continue
            predecessor = previous["eligibility_revision_id"] if previous else None
            if previous:
                conn.execute("UPDATE tutor_eligibility_revisions SET is_current=0 WHERE eligibility_revision_id=?", (predecessor,))
            conn.execute(
                "INSERT INTO tutor_eligibility_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"elg_{uuid.uuid4().hex}", session_id, learner_key, concept_key, purpose.value,
                    decision.state.value, json.dumps(decision.reasons, ensure_ascii=False),
                    json.dumps(decision.evidence_refs, ensure_ascii=False), reward_id, predecessor, 1,
                    decision_hash, _now(),
                ),
            )

    def review_response(
        self,
        *,
        session_id: str,
        student_alias: str,
        response_ref: str,
        segment: dict[str, Any],
        correct: bool | None,
        reason_present: bool,
        confidence: int | None,
        source_integrity_verified: bool,
        content_trust: ContentTrustState,
        hint_or_explanation_confirmed: bool,
        notes: str | None,
    ) -> dict[str, Any]:
        learner_key = self.learner_key(session_id, student_alias)
        concept_key = str(segment.get("concept_key") or "UNSPECIFIED")
        content_revision_id = str(segment.get("content_revision_id") or f"{session_id}:{segment.get('segment_id')}:v1")
        with self._db() as conn:
            exposures = conn.execute(
                """SELECT * FROM tutor_exposures WHERE session_id=? AND learner_key=? AND concept_key=? AND content_revision_id=? ORDER BY created_at""",
                (session_id, learner_key, concept_key, content_revision_id),
            ).fetchall()
            if not exposures:
                raise ValueError("학습증거로 회수된 학생 응답이 없습니다.")
            for exposure in exposures:
                previous = conn.execute(
                    """SELECT status_revision_id FROM tutor_content_status_revisions
                       WHERE session_id=? AND learner_key=? AND concept_key=? AND content_revision_id=?
                       ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                    (session_id, learner_key, concept_key, exposure["content_revision_id"]),
                ).fetchone()
                conn.execute(
                    "INSERT INTO tutor_content_status_revisions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"cts_{uuid.uuid4().hex}", session_id, learner_key, concept_key,
                        exposure["content_revision_id"], int(source_integrity_verified), content_trust.value,
                        previous["status_revision_id"] if previous else None,
                        _canonical_hash(notes or ""), _now(),
                    ),
                )
            response_evidence = conn.execute(
                """SELECT * FROM tutor_evidence_revisions WHERE session_id=? AND learner_key=? AND response_ref=? AND concept_key=? AND content_revision_id=?
                   ORDER BY created_at DESC LIMIT 1""",
                (session_id, learner_key, response_ref, concept_key, content_revision_id),
            ).fetchone()
            if not response_evidence:
                raise ValueError("해당 응답의 학습증거 revision을 찾을 수 없습니다.")
            validation_id = self._insert_evidence(
                conn,
                exposure_id=response_evidence["exposure_id"],
                session_id=session_id,
                learner_key=learner_key,
                concept_key=concept_key,
                content_revision_id=content_revision_id,
                evidence_type=EvidenceType.TEACHER_VALIDATION,
                response_ref=response_ref,
                correct=correct,
                reason_present=reason_present,
                confidence=confidence,
                independent=bool(segment.get("independent_followup", False)),
                delayed_hours=float(segment.get("delayed_hours") or 0),
                teacher_validated=True,
            )
            if hint_or_explanation_confirmed:
                hint_revision = str(segment.get("tutor_action_content_revision") or content_revision_id)
                hint_previous = conn.execute(
                    """SELECT status_revision_id FROM tutor_content_status_revisions
                       WHERE session_id=? AND learner_key=? AND concept_key=? AND content_revision_id=?
                       ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                    (session_id, learner_key, concept_key, hint_revision),
                ).fetchone()
                conn.execute(
                    "INSERT INTO tutor_content_status_revisions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"cts_{uuid.uuid4().hex}", session_id, learner_key, concept_key,
                        hint_revision, int(source_integrity_verified), content_trust.value,
                        hint_previous["status_revision_id"] if hint_previous else None,
                        _canonical_hash(notes or ""), _now(),
                    ),
                )
                self._insert_evidence(
                    conn,
                    exposure_id=response_evidence["exposure_id"],
                    session_id=session_id,
                    learner_key=learner_key,
                    concept_key=concept_key,
                    content_revision_id=hint_revision,
                    evidence_type=EvidenceType.HINT_USE,
                    response_ref=response_ref,
                    correct=None,
                    reason_present=False,
                    confidence=None,
                    hint_level=max(1, int(segment.get("hint_level") or 1)),
                    teacher_validated=True,
                )
            self._reevaluate(conn, session_id, learner_key, concept_key)
        return {"ok": True, "learner_key": learner_key, "teacher_validation_revision_id": validation_id}

    def mark_content_defect(self, *, session_id: str, content_revision_id: str, notes: str | None = None) -> int:
        affected: set[tuple[str, str]] = set()
        with self._db() as conn:
            exposures = conn.execute(
                "SELECT * FROM tutor_exposures WHERE session_id=? AND content_revision_id=?",
                (session_id, content_revision_id),
            ).fetchall()
            for exposure in exposures:
                previous = conn.execute(
                    """SELECT status_revision_id FROM tutor_content_status_revisions WHERE session_id=? AND learner_key=?
                       AND concept_key=? AND content_revision_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                    (session_id, exposure["learner_key"], exposure["concept_key"], content_revision_id),
                ).fetchone()
                conn.execute(
                    "INSERT INTO tutor_content_status_revisions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"cts_{uuid.uuid4().hex}", session_id, exposure["learner_key"], exposure["concept_key"],
                        content_revision_id, 0, ContentTrustState.DEFECT_CONFIRMED.value,
                        previous["status_revision_id"] if previous else None,
                        _canonical_hash(notes or ""), _now(),
                    ),
                )
                affected.add((str(exposure["learner_key"]), str(exposure["concept_key"])))
            for learner_key, concept_key in affected:
                self._reevaluate(conn, session_id, learner_key, concept_key)
        return len(affected)

    def session_status(self, session_id: str) -> dict[str, Any]:
        with self._db() as conn:
            exposures = conn.execute("SELECT COUNT(*) AS n FROM tutor_exposures WHERE session_id=?", (session_id,)).fetchone()["n"]
            evidence = conn.execute("SELECT COUNT(*) AS n FROM tutor_evidence_revisions WHERE session_id=?", (session_id,)).fetchone()["n"]
            rows = conn.execute(
                """SELECT eligibility_revision_id,learner_key,concept_key,learning_purpose,state,reasons_json,
                          evidence_refs_json,reward_revision_id,predecessor_revision_id,created_at
                   FROM tutor_eligibility_revisions WHERE session_id=? AND is_current=1
                   ORDER BY learner_key,concept_key,learning_purpose""",
                (session_id,),
            ).fetchall()
            current = []
            for row in rows:
                item = dict(row)
                item["reasons"] = json.loads(item.pop("reasons_json"))
                item["evidence_refs"] = json.loads(item.pop("evidence_refs_json"))
                current.append(item)
            counts = {state.value: 0 for state in EligibilityState}
            for item in current:
                counts[item["state"]] = counts.get(item["state"], 0) + 1
        return {
            "session_id": session_id,
            "connected": True,
            "mode": "EVIDENCE_CAPTURE_AND_ELIGIBILITY_ONLY",
            "automatic_weight_update": False,
            "pseudonym_salt_configured": self.pseudonym_salt_configured,
            "persistence": "FILESYSTEM_SQLITE",
            "exposure_count": exposures,
            "evidence_revision_count": evidence,
            "eligibility_counts": counts,
            "current_eligibility": current,
            "blocked_reuse": ["ITEM_GENERATOR", "DISTRACTOR_RANKER", "CONTENT_RECOMMENDATION_MODEL"],
        }

    def dataset_snapshot(self) -> dict[str, Any]:
        with self._db() as conn:
            rows = conn.execute(
                """SELECT eligibility_revision_id,session_id,learner_key,concept_key,learning_purpose,
                          evidence_refs_json,reward_revision_id,decision_hash
                   FROM tutor_eligibility_revisions WHERE is_current=1 AND state=?
                   ORDER BY eligibility_revision_id""",
                (EligibilityState.ELIGIBLE.value,),
            ).fetchall()
            records = []
            for row in rows:
                item = dict(row)
                item["evidence_refs"] = json.loads(item.pop("evidence_refs_json"))
                records.append(item)
        payload = {"records": records, "blocked_scopes": ["ITEM_GENERATOR", "DISTRACTOR_RANKER", "CONTENT_RECOMMENDATION_MODEL"]}
        return {
            "snapshot_hash": _canonical_hash(payload),
            "record_count": len(records),
            "records": records,
            "materialized_training_files": False,
            "actual_training_started": False,
        }

    def create_actual_use_receipt(
        self,
        *,
        eligibility_revision_id: str,
        training_job_id: str,
        tutor_model_version: str,
        sample_hash: str,
    ) -> dict[str, Any]:
        if os.environ.get("EDURECOVER_ENABLE_ACTUAL_TRAINING_RECEIPTS", "").strip().lower() not in {"1", "true", "yes", "on"}:
            raise ValueError("실제 학습사용 영수증 기능이 owner 정책상 HOLD 상태입니다.")
        if not os.environ.get("EDURECOVER_TUTOR_TRAINING_CLEARANCE_ID", "").strip():
            raise ValueError("튜터모델 학습에 대한 명시적 IP/FTO clearance ID가 없습니다.")
        with self._db() as conn:
            eligibility = conn.execute(
                "SELECT * FROM tutor_eligibility_revisions WHERE eligibility_revision_id=? AND is_current=1 AND state=?",
                (eligibility_revision_id, EligibilityState.ELIGIBLE.value),
            ).fetchone()
            if not eligibility:
                raise ValueError("현재 ELIGIBLE인 학습자격 revision만 실제 사용으로 기록할 수 있습니다.")
            receipt_id = f"tur_{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO tutor_training_use_receipts VALUES(?,?,?,?,?,?,?)",
                (receipt_id, eligibility_revision_id, training_job_id, tutor_model_version, sample_hash, 1, _now()),
            )
        return {"receipt_id": receipt_id, "confirmed_actual_use": True}
