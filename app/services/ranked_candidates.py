"""Persistent, evidence-gated exploration of ranked hypotheses.

Scores are provider ordering signals, not calibrated correctness probabilities.
Live adaptation rebuilds verified receipt-case statistics; no model is trained.
All mutations run inside the classroom database's transaction and ownership gate.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from app.services.cause_selection import CauseMapRequest

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidate_batches (
 batch_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, digest TEXT NOT NULL,
 batch_json TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS candidate_review_receipts (
 receipt_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
 status TEXT NOT NULL, payload_digest TEXT NOT NULL, receipt_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 FOREIGN KEY(batch_id) REFERENCES candidate_batches(batch_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS candidate_review_lookup ON candidate_review_receipts(batch_id,candidate_id,created_at);
CREATE TABLE IF NOT EXISTS candidate_rewards (
 reward_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, batch_id TEXT NOT NULL,
 candidate_id TEXT NOT NULL, original_rank INTEGER, reward INTEGER NOT NULL,
 outcome_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(batch_id,candidate_id),
 FOREIGN KEY(batch_id) REFERENCES candidate_batches(batch_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS candidate_policies (
 session_id TEXT NOT NULL, context_ref TEXT NOT NULL, revision INTEGER NOT NULL, policy_json TEXT NOT NULL,
 PRIMARY KEY(session_id,context_ref),
 FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS candidate_publications (
 batch_id TEXT NOT NULL, candidate_id TEXT NOT NULL, segment_id TEXT NOT NULL,
 lesson_digest TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(batch_id,candidate_id),
 FOREIGN KEY(batch_id) REFERENCES candidate_batches(batch_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS candidate_invalidations (
 invalidation_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, candidate_id TEXT,
 payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(batch_id) REFERENCES candidate_batches(batch_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS candidate_split_groups (
 session_id TEXT NOT NULL, source_binding_digest TEXT NOT NULL, split_group TEXT NOT NULL, split TEXT NOT NULL,
 PRIMARY KEY(session_id,source_binding_digest,split_group),
 FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS candidate_evidence_revocations (
 revocation_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
 payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);
"""

IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$"
MAX_CANDIDATE_RANK = 100


class Candidate(BaseModel):
    candidate_id: str = Field(pattern=IDENTIFIER)
    origin: Literal["MODEL", "HUMAN_INSIGHT"] = "MODEL"
    rank_basis: Literal["TEACHER_IMPORTED_RANK", "MODEL_SUPPLIED_RANK", "SELF_REPORTED_RANK", "GENERATION_ORDER", "NOT_APPLICABLE"] = "TEACHER_IMPORTED_RANK"
    rank: int | None = Field(default=None, ge=1, le=MAX_CANDIDATE_RANK)
    model_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    score_basis: str = Field(default="PROVIDER_ORDERING_SIGNAL_UNCALIBRATED", max_length=500)
    source_ref: str | None = Field(default=None, max_length=500)
    concept_refs: list[str] = Field(default_factory=list, max_length=30)
    hypothesis_type: str = Field(default="ALTERNATIVE_ANSWER", max_length=100)
    text: str = Field(min_length=1, max_length=10000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def honest_origin(self):
        if self.origin == "MODEL" and self.rank is None:
            raise ValueError("모델 후보에는 모델 순위 또는 명시된 생성 순서가 필요합니다.")
        if self.origin == "MODEL" and self.rank_basis == "NOT_APPLICABLE":
            raise ValueError("모델 후보의 순위 출처를 지정해 주세요.")
        if self.origin == "MODEL" and self.model_score is None:
            self.score_basis = "NOT_AVAILABLE_NO_MEASURED_MODEL_SCORE"
        if any(not reference.strip() for reference in self.concept_refs):
            raise ValueError("개념 연결 참조가 비어 있습니다.")
        if self.origin == "HUMAN_INSIGHT":
            if self.rank is not None or self.model_score is not None:
                raise ValueError("교사 직관 후보에 임의의 모델 순위·점수를 붙이지 마세요.")
            self.score_basis = "NOT_APPLICABLE_HUMAN_INSIGHT"
            self.rank_basis = "NOT_APPLICABLE"
        return self


class SearchScope(BaseModel):
    max_rank: int = Field(default=3, ge=1, le=MAX_CANDIDATE_RANK)
    score_margin: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    include_candidate_ids: list[str] = Field(default_factory=list, max_length=100)


class VerificationCriteria(BaseModel):
    kind: Literal["EVIDENCE", "NUMERIC"] = "EVIDENCE"
    expected_numeric_value: float | None = Field(default=None, allow_inf_nan=False)
    numeric_absolute_tolerance: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    criteria_ref: str = Field(default="TEACHER_CHECKED_TASK_EVIDENCE", max_length=500)
    required_dimensions: list[Literal["ANSWER", "EXPLANATION", "ACTUAL_CAUSE"]] = Field(
        default_factory=lambda: ["ANSWER"], min_length=1, max_length=3)

    @model_validator(mode="after")
    def numeric_requirements(self):
        if len(set(self.required_dimensions)) != len(self.required_dimensions):
            raise ValueError("검증할 판단 종류가 중복되어 있습니다.")
        if "ANSWER" not in self.required_dimensions:
            raise ValueError("후보 판단에는 답·주장 자체의 정확성 검증을 포함해야 합니다.")
        if self.kind == "NUMERIC" and (self.expected_numeric_value is None or
                                        self.numeric_absolute_tolerance is None):
            raise ValueError("수치 검증은 기준값과 허용 절대오차를 탐색 전에 정해야 합니다.")
        if self.kind == "EVIDENCE" and (self.expected_numeric_value is not None or
                                        self.numeric_absolute_tolerance is not None):
            raise ValueError("수치 허용오차를 쓰려면 NUMERIC 검증 기준을 선택하세요.")
        return self


class CandidateBatchCreate(BaseModel):
    context_ref: str = Field(min_length=1, max_length=500)
    candidates: list[Candidate] = Field(min_length=1, max_length=100)
    search: SearchScope = Field(default_factory=SearchScope)
    verification: VerificationCriteria = Field(default_factory=VerificationCriteria)
    task_context: str = Field(default="", max_length=10000)
    source_revision_ref: str = Field(default="", max_length=500)
    cause_map_request: CauseMapRequest | None = None

    @model_validator(mode="after")
    def distinct_candidates(self):
        ids = [x.candidate_id for x in self.candidates]
        ranks = [x.rank for x in self.candidates if x.rank is not None]
        if len(set(ids)) != len(ids) or len(set(ranks)) != len(ranks):
            raise ValueError("후보 식별자와 원순위는 각각 중복되지 않아야 합니다.")
        if not set(self.search.include_candidate_ids).issubset(ids):
            raise ValueError("지정한 탐색 후보가 원래 후보 목록에 없습니다.")
        return self


class DimensionCheck(BaseModel):
    status: Literal["PASSED", "FAILED", "UNKNOWN", "CONFLICTING"] = "UNKNOWN"
    evidence_refs: list[str] = Field(default_factory=list, max_length=30)
    teacher_checked: bool = False
    method: Literal["SOURCE_CHECK", "CONTROLLED_COMPARISON", "INDEPENDENT_RETEST", "DIRECT_OBSERVATION"] = "SOURCE_CHECK"
    observation_ref: str = Field(default="", max_length=500)
    contrast_ref: str = Field(default="", max_length=500)


class CandidateReview(BaseModel):
    expected_digest: str = Field(min_length=64, max_length=64)
    candidate_id: str = Field(pattern=IDENTIFIER)
    outcome: Literal["ACCEPTED", "REJECTED", "UNRESOLVED"]
    reason: str = Field(min_length=1, max_length=5000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=30)
    teacher_verified: bool = False
    ground_truth_status: Literal["VERIFIED", "UNKNOWN", "CONFLICTING"]
    measured_numeric_value: float | None = Field(default=None, allow_inf_nan=False)
    reason_status: Literal["UNVERIFIED", "TEACHER_CHECKED"] = "UNVERIFIED"
    learning_use_approved: bool = False
    rights_status: Literal["UNKNOWN", "APPROVED"] = "UNKNOWN"
    split: Literal["TRAIN", "EVAL"] = "TRAIN"
    split_group: str = Field(default="", max_length=500)
    answer_check: DimensionCheck | None = None
    explanation_check: DimensionCheck | None = None
    actual_cause_check: DimensionCheck | None = None


class CandidateInvalidation(BaseModel):
    expected_digest: str = Field(min_length=64, max_length=64)
    candidate_id: str | None = Field(default=None, pattern=IDENTIFIER)
    reason: str = Field(min_length=1, max_length=5000)
    evidence_refs: list[str] = Field(min_length=1, max_length=30)
    teacher_verified: Literal[True]


class EvidenceRevocation(BaseModel):
    revoked_evidence_refs: list[str] = Field(min_length=1, max_length=30)
    correction_evidence_refs: list[str] = Field(min_length=1, max_length=30)
    reason: str = Field(min_length=1, max_length=5000)
    teacher_verified: Literal[True]


class CandidatePublication(BaseModel):
    expected_digest: str = Field(min_length=64, max_length=64)
    candidate_id: str = Field(pattern=IDENTIFIER)
    expected_lesson_digest: str = Field(min_length=64, max_length=64)
    teacher_verified_content: Literal[True]


class PolicyLearning(BaseModel):
    expected_revision: int = Field(ge=0)
    context_ref: str | None = Field(default=None, max_length=500)


class CandidateGateError(ValueError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _case_keys(candidate: dict, criteria: dict) -> list[str]:
    """Exact, predeclared teaching labels; no token model or learned graph."""
    gate = "gate:" + _digest(criteria) + "|"
    return sorted({gate + "type:" + candidate.get("hypothesis_type", "ALTERNATIVE_ANSWER"),
                   *(gate + "concept:" + ref for ref in candidate.get("concept_refs", []))})


def case_priorities(candidates: list[Candidate], current: dict, criteria: dict) -> list[dict]:
    statistics = current.get("case_statistics", {})
    entries = []
    for candidate in candidates:
        values = [statistics[key]["mean_reward"] for key in _case_keys(candidate.model_dump(), criteria)
                  if key in statistics]
        score = max(0.0, 1.0 + sum(values) / len(values)) if values else 1.0
        entries.append({"candidate_id": candidate.candidate_id, "original_rank": candidate.rank,
                        "case_score": score, "policy_revision": current["revision"],
                        "priority_semantics": "RECEIPT_CASE_SUPPORT_NOT_CORRECTNESS_PROBABILITY"})
    total = sum(entry["case_score"] for entry in entries)
    for entry in entries:
        entry["policy_priority"] = entry["case_score"] / total if total else 1 / len(entries)
    return sorted(entries, key=lambda entry: (-entry["policy_priority"], entry["candidate_id"]))


def revoked_evidence(conn: sqlite3.Connection, session_id: str) -> set[str]:
    refs = set()
    for row in conn.execute("SELECT payload_json FROM candidate_evidence_revocations WHERE session_id=?",
                            (session_id,)).fetchall():
        payload = json.loads(row["payload_json"])
        saved = payload.pop("digest")
        if not secrets.compare_digest(saved, _digest(payload)):
            raise CandidateGateError(423, "근거 철회 기록의 무결성을 확인할 수 없습니다.")
        refs.update(payload["revoked_evidence_refs"])
    return refs


def assert_current_evidence(conn: sqlite3.Connection, session_id: str, references: list[str]) -> None:
    if set(references) & revoked_evidence(conn, session_id):
        raise CandidateGateError(423, "철회된 근거가 포함되어 있습니다. 새로운 근거 버전으로 다시 검증하세요.")


def review_evidence_refs(receipt: dict) -> set[str]:
    refs = set(receipt.get("evidence_refs", []))
    for check in receipt.get("dimension_checks", {}).values():
        refs.update(check.get("evidence_refs", []))
        refs.update(ref for ref in (check.get("observation_ref"), check.get("contrast_ref")) if ref)
    return refs


def candidate_used_evidence_refs(batch: dict, candidate: dict, receipt: dict | None = None) -> set[str]:
    refs = set(candidate.get("evidence_refs", []))
    refs.update((batch["manifest"].get("approved_cause_context") or {}).get("evidence_refs", []))
    generation = batch["manifest"].get("generation_receipt") or {}
    for dependency in generation.get("verified_reason_dependencies", []):
        refs.update(dependency.get("evidence_refs", []))
    if receipt:
        refs.update(review_evidence_refs(receipt))
    return refs


def evidence_affected_candidates(conn: sqlite3.Connection, session_id: str, references: list[str]) -> list[dict]:
    """Follow recorded evidence use only; preserve unrelated candidate uses."""
    revoked = set(references)
    affected = []
    for row in conn.execute("SELECT batch_id FROM candidate_batches WHERE session_id=?", (session_id,)).fetchall():
        batch = get_batch(conn, session_id, row["batch_id"], allow_invalidated_dependencies=True)
        for candidate in batch["manifest"]["candidates"]:
            receipt = latest_review(conn, batch["batch_id"], candidate["candidate_id"])
            used = candidate_used_evidence_refs(batch, candidate, receipt)
            if revoked & used and not conn.execute("SELECT 1 FROM candidate_invalidations WHERE batch_id=? AND "
                "(candidate_id IS NULL OR candidate_id=?)", (batch["batch_id"], candidate["candidate_id"])).fetchone():
                affected.append({"batch_id": batch["batch_id"], "batch_digest": batch["digest"],
                                 "candidate_id": candidate["candidate_id"],
                                 "context_ref": batch["manifest"]["context_ref"],
                                 "source_binding_digest": batch["manifest"]["source_binding_digest"],
                                 "receipt_id": receipt["receipt_id"] if receipt else None,
                                 "matched_evidence_refs": sorted(revoked & used)})
    return affected


def revoke_evidence(conn: sqlite3.Connection, session_id: str, request: EvidenceRevocation, reviewer: str) -> dict:
    if any(not ref.strip() for ref in [*request.revoked_evidence_refs, *request.correction_evidence_refs]):
        raise CandidateGateError(422, "철회할 근거와 정정 근거의 참조가 비어 있습니다.")
    if set(request.revoked_evidence_refs) & set(request.correction_evidence_refs):
        raise CandidateGateError(422, "철회한 근거 자체를 정정 증거로 다시 사용할 수 없습니다.")
    assert_current_evidence(conn, session_id, request.correction_evidence_refs)
    affected = evidence_affected_candidates(conn, session_id, request.revoked_evidence_refs)
    if not affected and not set(request.revoked_evidence_refs) <= revoked_evidence(conn, session_id):
        raise CandidateGateError(404, "이 수업의 기록에서 해당 근거의 실제 사용을 찾지 못했습니다.")
    payload = {"revocation_id": uuid.uuid4().hex, "session_id": session_id,
               **request.model_dump(), "reviewer": reviewer, "affected_candidate_uses": affected,
               "created_at": _now()}
    payload["digest"] = _digest(payload)
    conn.execute("INSERT INTO candidate_evidence_revocations VALUES(?,?,?,?)",
                 (payload["revocation_id"], session_id, json.dumps(payload, ensure_ascii=False), payload["created_at"]))
    for item in affected:
        invalidate_candidate(conn, session_id, item["batch_id"], CandidateInvalidation(
            expected_digest=item["batch_digest"], candidate_id=item["candidate_id"],
            reason=request.reason, evidence_refs=request.correction_evidence_refs, teacher_verified=True), reviewer)
    return payload


def policy(conn: sqlite3.Connection, session_id: str, context_ref: str,
           source_binding_digest: str | None = None) -> dict:
    row = conn.execute("SELECT * FROM candidate_policies WHERE session_id=? AND context_ref=?",
                       (session_id, context_ref)).fetchone()
    if row:
        result = json.loads(row["policy_json"])
        result["revision"] = row["revision"]
        if not source_binding_digest or result.get("source_binding_digest") == source_binding_digest:
            return result
    result = {"revision": row["revision"] if row else 0, "effective_max_rank": 3, "rank_statistics": {},
            "context_ref": context_ref,
            "source_binding_digest": source_binding_digest,
            "effective_score_margin": 0.0,
            "learned_reward_ids": [], "learned_reward_count": 0,
            "base_model_updated": False, "model_weights_updated": False,
            "learning_kind": "VERIFIED_RECEIPT_CASE_STATISTICS",
            "case_statistics": {}}
    return result


def current_reason_memory(conn: sqlite3.Connection, session_id: str, current_policy: dict,
                          source_binding_digest: str) -> list[dict]:
    memory = []
    for item in current_policy.get("accepted_reason_memory", []):
        # Earlier records did not bind the reason to its original receipt.
        # They are preserved in storage but cannot enter a new provider call.
        if not all(item.get(key) for key in ("batch_id", "receipt_id", "receipt_digest", "source_binding_digest")):
            continue
        if item["source_binding_digest"] != source_binding_digest or invalidated(
                conn, item["batch_id"], item["candidate_id"]):
            continue
        receipt = latest_review(conn, item["batch_id"], item["candidate_id"])
        if not receipt or receipt["receipt_id"] != item["receipt_id"] or receipt["receipt_digest"] != item["receipt_digest"] or \
                receipt["status"] != "ACCEPTED" or not receipt["learning_use_approved"] or \
                receipt["rights_status"] != "APPROVED" or receipt["reason_status"] != "TEACHER_CHECKED" or \
                receipt["reason"] != item.get("verified_reason"):
            raise CandidateGateError(423, "후보 생성에 참고할 이유와 승인된 원 검증 기록이 일치하지 않습니다.")
        assert_current_evidence(conn, session_id, item.get("evidence_refs", []))
        batch = get_batch(conn, session_id, item["batch_id"], source_binding_digest=source_binding_digest)
        candidate = next((candidate for candidate in batch["manifest"]["candidates"]
                          if candidate["candidate_id"] == item["candidate_id"]), None)
        if not candidate or candidate["concept_refs"] != item.get("concept_refs") or \
                candidate["hypothesis_type"] != item.get("hypothesis_type") or \
                sorted(candidate_used_evidence_refs(batch, candidate, receipt)) != item.get("evidence_refs"):
            raise CandidateGateError(423, "이유 기억의 개념·근거 계보가 원 후보와 일치하지 않습니다.")
        memory.append(item)
    return memory


def create_batch(conn: sqlite3.Connection, session_id: str, req: CandidateBatchCreate,
                 source_binding: dict, generation_receipt: dict | None = None,
                 approved_cause_context: dict | None = None) -> dict:
    if conn.execute("SELECT COUNT(*) FROM candidate_batches WHERE session_id=?", (session_id,)).fetchone()[0] >= 500:
        raise CandidateGateError(429, "이 수업의 후보 기록 저장 한도에 도달했습니다.")
    source_binding_digest = _digest(source_binding)
    current_policy = policy(conn, session_id, req.context_ref, source_binding_digest)
    for candidate in req.candidates:
        assert_current_evidence(conn, session_id, candidate.evidence_refs)
    if approved_cause_context:
        assert_current_evidence(conn, session_id, approved_cause_context.get("evidence_refs", []))
    for dependency in (generation_receipt or {}).get("verified_reason_dependencies", []):
        assert_current_evidence(conn, session_id, dependency.get("evidence_refs", []))
        receipt = latest_review(conn, dependency["batch_id"], dependency["candidate_id"])
        if not receipt or receipt["receipt_digest"] != dependency["receipt_digest"] or invalidated(
                conn, dependency["batch_id"], dependency["candidate_id"]):
            raise CandidateGateError(423, "생성에 참고한 검증 사례가 철회·변경되었습니다. 최신 근거로 다시 생성하세요.")
    explicit = set(req.search.include_candidate_ids)
    effective_max_rank = min(MAX_CANDIDATE_RANK, max(
        req.search.max_rank, current_policy["effective_max_rank"],
        max((x.rank for x in req.candidates if x.candidate_id in explicit and x.rank is not None), default=0)))
    effective_margin = max(req.search.score_margin, current_policy.get("effective_score_margin", 0.0))
    best_score = max((x.model_score for x in req.candidates if x.model_score is not None), default=0.0)
    selected = [x for x in sorted(req.candidates, key=lambda x: (x.rank is None, x.rank or 0))
                if x.origin == "HUMAN_INSIGHT" or x.candidate_id in explicit or
                (x.rank <= effective_max_rank and (x.model_score is None or
                                                   x.model_score + 1e-12 >= best_score - effective_margin))]
    learned_priorities = []
    if current_policy.get("case_statistics"):
        learned_priorities = case_priorities(req.candidates, current_policy, req.verification.model_dump())
        selected_ids = {candidate.candidate_id for candidate in selected}
        extras = [item for item in learned_priorities if item["policy_priority"] > 1 / len(req.candidates) + 1e-12][:3]
        for item in extras:
            if item["candidate_id"] not in selected_ids:
                selected.append(next(candidate for candidate in req.candidates if candidate.candidate_id == item["candidate_id"]))
                selected_ids.add(item["candidate_id"])
    if not selected:
        raise CandidateGateError(423, "지정한 탐색 범위에 후보가 없습니다. 범위를 조정하거나 직접 탐색할 후보를 지정하세요.")
    manifest = {
        "context_ref": req.context_ref,
        "task_context": req.task_context,
        "source_revision_ref": req.source_revision_ref,
        "source_binding": source_binding,
        "source_binding_digest": source_binding_digest,
        "generation_receipt": generation_receipt,
        "approved_cause_context": approved_cause_context,
        "ranking_provenance": "SERVER_PROVIDER_RESPONSE_SELF_REPORTED_ORDER" if generation_receipt else
            "TEACHER_IMPORTED_METADATA_NOT_PROVIDER_ATTESTED",
        "candidates": [x.model_dump() for x in selected],
        "selected_candidate_ids": [x.candidate_id for x in selected],
        "search": {"requested_max_rank": req.search.max_rank,
                   "effective_max_rank": effective_max_rank,
                   "score_margin": req.search.score_margin,
                   "effective_score_margin": effective_margin,
                   "include_candidate_ids": req.search.include_candidate_ids,
                   "hard_rank_limit": MAX_CANDIDATE_RANK,
                   "learned_priority_extra_budget": 3,
                   "learned_priority_extra_rank_limit": MAX_CANDIDATE_RANK},
        "selection_mode": "DETERMINISTIC_ENUMERATION",
        # There was no randomized draw; inventing a probability would corrupt replay.
        "exploration_probability": None,
        "score_semantics": "MODEL_ORDERING_SIGNAL_NOT_CORRECTNESS_PROBABILITY",
        "policy_revision": current_policy["revision"],
        "learned_content_priorities": learned_priorities,
        "priority_semantics": "EXPLORATION_PRIORITY_NOT_CORRECTNESS_PROBABILITY",
        "verification_gate": "TEACHER_VERIFIED_EVIDENCE_REQUIRED_INDEPENDENT_OF_SEARCH_SCOPE",
        "verification_criteria": req.verification.model_dump(),
        "reward_purpose": "VERIFIED_REQUIRED_DIMENSIONS_NOT_STUDENT_LEARNING_GAIN",
    }
    batch = {"batch_id": uuid.uuid4().hex, "session_id": session_id,
             "manifest": manifest, "candidates": [x.model_dump() for x in req.candidates],
             "policy_revision": current_policy["revision"], "created_at": _now()}
    batch["digest"] = _digest(batch)
    conn.execute("INSERT INTO candidate_batches VALUES(?,?,?,?,?)",
                 (batch["batch_id"], session_id, batch["digest"],
                  json.dumps(batch, ensure_ascii=False), batch["created_at"]))
    return batch


def get_batch(conn: sqlite3.Connection, session_id: str, batch_id: str,
              expected_digest: str | None = None, source_binding_digest: str | None = None,
              allow_invalidated_dependencies: bool = False) -> dict:
    row = conn.execute("SELECT * FROM candidate_batches WHERE session_id=? AND batch_id=?",
                       (session_id, batch_id)).fetchone()
    if not row:
        raise CandidateGateError(404, "이 수업의 후보 묶음을 찾을 수 없습니다.")
    if expected_digest and not secrets.compare_digest(row["digest"], expected_digest):
        raise CandidateGateError(409, "검토한 후보 묶음과 저장된 기록이 다릅니다.")
    batch = json.loads(row["batch_json"])
    saved_digest = batch.pop("digest")
    if not secrets.compare_digest(saved_digest, _digest(batch)):
        raise CandidateGateError(423, "후보 원본의 무결성을 확인할 수 없습니다.")
    batch["digest"] = saved_digest
    if source_binding_digest and not secrets.compare_digest(batch["manifest"]["source_binding_digest"], source_binding_digest):
        raise CandidateGateError(423, "후보를 생성·검증한 원자료 버전이 현재 수업과 다릅니다. 새 자료로 재검증해 주세요.")
    generation = batch["manifest"].get("generation_receipt") or {}
    if not allow_invalidated_dependencies:
        for dependency in generation.get("verified_reason_dependencies", []):
            receipt = latest_review(conn, dependency["batch_id"], dependency["candidate_id"])
            if not receipt or receipt["receipt_digest"] != dependency["receipt_digest"] or invalidated(
                    conn, dependency["batch_id"], dependency["candidate_id"]):
                raise CandidateGateError(423, "후보 생성에 실제 사용한 부모 검증 사례가 철회·변경되었습니다.")
    if generation.get("response_id") and not allow_invalidated_dependencies:
        response = conn.execute("SELECT response_id,segment_id,answer,reason_text FROM responses WHERE session_id=? AND response_id=?",
                                (session_id, generation["response_id"])).fetchone()
        if not response or _digest({key: response[key] for key in
                ("response_id", "segment_id", "answer", "reason_text")}) != generation["response_digest"]:
            raise CandidateGateError(423, "후보를 만든 학생 응답 근거가 변경·삭제되었습니다. 다시 검증해 주세요.")
    return batch


def invalidated(conn: sqlite3.Connection, batch_id: str, candidate_id: str, _visited=None) -> bool:
    if conn.execute("SELECT 1 FROM candidate_invalidations WHERE batch_id=? AND "
                    "(candidate_id IS NULL OR candidate_id=?) LIMIT 1", (batch_id, candidate_id)).fetchone():
        return True
    visited = set(_visited or ())
    key = (batch_id, candidate_id)
    if key in visited:
        return True  # Unknown/cyclic provenance must not authorize consumption.
    visited.add(key)
    row = conn.execute("SELECT batch_json FROM candidate_batches WHERE batch_id=?", (batch_id,)).fetchone()
    if not row:
        return True
    batch = json.loads(row["batch_json"])
    saved_digest = batch.pop("digest")
    if not secrets.compare_digest(saved_digest, _digest(batch)):
        raise CandidateGateError(423, "후보 의존 기록의 무결성을 확인할 수 없습니다.")
    generation = batch["manifest"].get("generation_receipt") or {}
    if generation.get("response_id"):
        response = conn.execute("SELECT response_id,segment_id,answer,reason_text FROM responses WHERE session_id=? AND response_id=?",
                                (batch["session_id"], generation["response_id"])).fetchone()
        if not response or _digest({key: response[key] for key in ("response_id", "segment_id", "answer", "reason_text")}) != generation["response_digest"]:
            return True
    for dependency in generation.get("verified_reason_dependencies", []):
        receipt = latest_review(conn, dependency["batch_id"], dependency["candidate_id"])
        if not receipt or receipt["receipt_digest"] != dependency["receipt_digest"] or invalidated(
                conn, dependency["batch_id"], dependency["candidate_id"], visited):
            return True
    return False


def candidate_dependency_closure(conn: sqlite3.Connection, session_id: str, batch_id: str,
                                  candidate_id: str | None) -> list[dict]:
    """Follow only the exact parent cases consumed by recorded provider calls."""
    batches = [get_batch(conn, session_id, row["batch_id"], allow_invalidated_dependencies=True)
               for row in conn.execute("SELECT batch_id FROM candidate_batches WHERE session_id=?", (session_id,)).fetchall()]
    root = next((batch for batch in batches if batch["batch_id"] == batch_id), None)
    if root is None:
        raise CandidateGateError(404, "철회할 후보 묶음이 없습니다.")
    selected = {(batch_id, candidate["candidate_id"]) for candidate in root["candidates"]
                if candidate_id is None or candidate["candidate_id"] == candidate_id}
    if not selected:
        raise CandidateGateError(404, "철회할 후보가 없습니다.")
    while True:
        expanded = set(selected)
        for batch in batches:
            dependencies = (batch["manifest"].get("generation_receipt") or {}).get("verified_reason_dependencies", [])
            if any((dependency["batch_id"], dependency["candidate_id"]) in selected for dependency in dependencies):
                expanded.update((batch["batch_id"], candidate["candidate_id"])
                                for candidate in batch["manifest"]["candidates"])
        if expanded == selected:
            break
        selected = expanded
    return [{"batch_id": batch["batch_id"], "candidate_id": candidate["candidate_id"],
             "batch_digest": batch["digest"], "context_ref": batch["manifest"]["context_ref"]}
            for batch in batches for candidate in batch["candidates"]
            if (batch["batch_id"], candidate["candidate_id"]) in selected]


def invalidate_candidate(conn: sqlite3.Connection, session_id: str, batch_id: str,
                          req: CandidateInvalidation, reviewer: str) -> dict:
    batch = get_batch(conn, session_id, batch_id, req.expected_digest, allow_invalidated_dependencies=True)
    if req.candidate_id and req.candidate_id not in {candidate["candidate_id"] for candidate in batch["candidates"]}:
        raise CandidateGateError(404, "철회할 후보가 이 묶음에 없습니다.")
    payload = {"invalidation_id": uuid.uuid4().hex, "batch_id": batch_id,
               "candidate_id": req.candidate_id, "reason": req.reason,
               "evidence_refs": req.evidence_refs, "teacher_verified": True,
               "reviewer": reviewer, "created_at": _now()}
    payload["digest"] = _digest(payload)
    conn.execute("INSERT INTO candidate_invalidations VALUES(?,?,?,?,?)",
                 (payload["invalidation_id"], batch_id, req.candidate_id,
                  json.dumps(payload, ensure_ascii=False), payload["created_at"]))
    return payload


def verified_receipt(receipt_json: str) -> dict:
    receipt = json.loads(receipt_json)
    saved_digest = receipt.pop("receipt_digest")
    if not secrets.compare_digest(saved_digest, _digest(receipt)):
        raise CandidateGateError(423, "검증 영수증의 무결성을 확인할 수 없습니다.")
    receipt["receipt_digest"] = saved_digest
    return receipt


def latest_review(conn: sqlite3.Connection, batch_id: str, candidate_id: str) -> dict | None:
    row = conn.execute("SELECT receipt_json FROM candidate_review_receipts WHERE batch_id=? AND candidate_id=? "
                       "ORDER BY created_at DESC,receipt_id DESC LIMIT 1", (batch_id, candidate_id)).fetchone()
    return verified_receipt(row["receipt_json"]) if row else None


def list_batches(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute("SELECT batch_id FROM candidate_batches WHERE session_id=? ORDER BY created_at DESC",
                        (session_id,)).fetchall()
    batches = []
    for row in rows:
        batch = get_batch(conn, session_id, row["batch_id"], allow_invalidated_dependencies=True)
        batch["reviews"] = {c["candidate_id"]: latest_review(conn, row["batch_id"], c["candidate_id"])
                            for c in batch["manifest"]["candidates"]}
        batch["invalidated_candidate_ids"] = [candidate["candidate_id"] for candidate in batch["candidates"]
                                              if invalidated(conn, row["batch_id"], candidate["candidate_id"])]
        batches.append(batch)
    return batches


def review_candidate(conn: sqlite3.Connection, session_id: str, batch_id: str,
                     req: CandidateReview, reviewer: str) -> dict:
    batch = get_batch(conn, session_id, batch_id, req.expected_digest)
    if invalidated(conn, batch_id, req.candidate_id):
        raise CandidateGateError(423, "철회된 후보는 채택·학습에 사용하지 않습니다. 새 후보 묶음으로 재검증하세요.")
    candidate = next((x for x in batch["manifest"]["candidates"]
                      if x["candidate_id"] == req.candidate_id), None)
    if not candidate:
        raise CandidateGateError(409, "검증 전에 기록된 탐색 목록에 없는 후보입니다. 새 탐색 기록을 만드세요.")
    assert_current_evidence(conn, session_id, req.evidence_refs)
    if req.outcome != "UNRESOLVED" and (req.ground_truth_status != "VERIFIED" or
            not req.teacher_verified or not req.reason.strip() or
            not req.evidence_refs or any(not ref.strip() for ref in req.evidence_refs)):
        raise CandidateGateError(423, "채택·기각에는 확인된 정답 근거와 교사 검증이 필요합니다. 불확실하면 미확정으로 보류하세요.")
    criteria = batch["manifest"]["verification_criteria"]
    # A correct final answer is not proof of its explanation or the student's
    # actual error cause. These remain separate, immutable decisions.
    checks = {}
    for dimension, check in (("ANSWER", req.answer_check), ("EXPLANATION", req.explanation_check),
                              ("ACTUAL_CAUSE", req.actual_cause_check)):
        if check is not None:
            if check.status in {"PASSED", "FAILED"} and (not check.teacher_checked or not check.evidence_refs or
                                                          any(not ref.strip() for ref in check.evidence_refs)):
                raise CandidateGateError(423, "각 확정 판단에는 별도로 확인한 근거와 교사 검수가 필요합니다.")
            references = [*check.evidence_refs, *(ref for ref in (check.observation_ref, check.contrast_ref) if ref)]
            assert_current_evidence(conn, session_id, references)
            if dimension == "ACTUAL_CAUSE" and check.status in {"PASSED", "FAILED"}:
                if check.method == "SOURCE_CHECK" or not check.observation_ref or (
                    check.method in {"CONTROLLED_COMPARISON", "INDEPENDENT_RETEST"} and not check.contrast_ref):
                    raise CandidateGateError(423, "실제 원인 판단에는 학생 관찰과 대조·별도 재검사 근거가 필요합니다. 풀이가 맞다는 이유만으로 확정하지 않습니다.")
            checks[dimension] = check.model_dump()
    if "ANSWER" not in checks:
        checks["ANSWER"] = {"status": "UNKNOWN" if req.outcome == "UNRESOLVED" else
                            "PASSED" if req.outcome == "ACCEPTED" else "FAILED",
                            "evidence_refs": req.evidence_refs, "teacher_checked": req.teacher_verified,
                            "method": "SOURCE_CHECK", "observation_ref": "", "contrast_ref": "",
                            "legacy_answer_only": True}
    required_dimensions = criteria.get("required_dimensions", ["ANSWER"])
    required_states = [checks.get(dimension, {}).get("status", "UNKNOWN") for dimension in required_dimensions]
    if req.outcome == "ACCEPTED" and any(state != "PASSED" for state in required_states):
        raise CandidateGateError(423, "탐색 전에 정한 답·설명·실제 원인 검증이 모두 통과해야 후보를 채택할 수 있습니다. 미확정 판단은 별도로 남겨 주세요.")
    if req.outcome == "REJECTED" and not any(state == "FAILED" for state in required_states):
        raise CandidateGateError(423, "확인된 실패가 없는 후보를 기각으로 학습하지 않습니다. 미확정으로 보류하세요.")
    if checks["ANSWER"]["status"] in {"PASSED", "FAILED"} and criteria["kind"] == "NUMERIC":
        if req.measured_numeric_value is None:
            raise CandidateGateError(423, "탐색 전에 정한 수치 검증 기준에 따른 측정값이 필요합니다.")
        error = abs(req.measured_numeric_value - criteria["expected_numeric_value"])
        within = error <= criteria["numeric_absolute_tolerance"]
        if (checks["ANSWER"]["status"] == "PASSED") != within:
            raise CandidateGateError(423, "측정값이 사전에 고정한 수치 허용오차 판정과 일치하지 않습니다.")
    request_digest = _digest({**req.model_dump(), "reviewer": reviewer})
    duplicate = conn.execute("SELECT receipt_json FROM candidate_review_receipts WHERE batch_id=? AND candidate_id=? "
                             "AND payload_digest=?", (batch_id, req.candidate_id, request_digest)).fetchone()
    if duplicate:
        # Verify the stored receipt even on a retried request.
        receipt = verified_receipt(duplicate["receipt_json"])
        return {**receipt, "idempotent": True, "reward_created": False}
    prior = latest_review(conn, batch_id, req.candidate_id)
    if prior and prior["status"] != "UNRESOLVED":
        raise CandidateGateError(409, "확정된 검증 기록은 덮어쓰지 않습니다. 재검증은 새 후보 묶음으로 기록하세요.")
    receipt = {"receipt_id": uuid.uuid4().hex, "batch_id": batch_id,
               "batch_digest": batch["digest"], "candidate_id": req.candidate_id,
               "original_rank": candidate["rank"], "original_model_score": candidate["model_score"],
               "status": req.outcome, "ground_truth_status": req.ground_truth_status,
               "reason": req.reason, "evidence_refs": req.evidence_refs,
               "teacher_verified": req.teacher_verified, "reviewer": reviewer,
               "verification_criteria": criteria, "measured_numeric_value": req.measured_numeric_value,
               "dimension_checks": checks,
               "actual_cause_confirmed": checks.get("ACTUAL_CAUSE", {}).get("status") == "PASSED",
               "created_at": _now(), "idempotent": False,
               "reason_status": req.reason_status, "learning_use_approved": req.learning_use_approved,
               "rights_status": req.rights_status, "split": req.split,
               "split_group": req.split_group or batch["manifest"]["context_ref"],
               "reward_created": req.outcome != "UNRESOLVED" and req.learning_use_approved and
                    req.reason_status == "TEACHER_CHECKED" and req.rights_status == "APPROVED"}
    receipt["receipt_digest"] = _digest(receipt)
    if receipt["reward_created"]:
        allocated = conn.execute("SELECT split FROM candidate_split_groups WHERE session_id=? AND source_binding_digest=? "
                                  "AND split_group=?", (session_id, batch["manifest"]["source_binding_digest"], receipt["split_group"])).fetchone()
        if allocated and allocated["split"] != req.split:
            raise CandidateGateError(423, "같은 원자료·문항 가족을 학습과 평가 양쪽에 사용할 수 없습니다.")
        conn.execute("INSERT OR IGNORE INTO candidate_split_groups VALUES(?,?,?,?)",
                     (session_id, batch["manifest"]["source_binding_digest"], receipt["split_group"], req.split))
    conn.execute("INSERT INTO candidate_review_receipts VALUES(?,?,?,?,?,?,?)",
                 (receipt["receipt_id"], batch_id, req.candidate_id, req.outcome, request_digest,
                  json.dumps(receipt, ensure_ascii=False), receipt["created_at"]))
    if receipt["reward_created"]:
        reward_id = uuid.uuid4().hex
        outcome = {"context_ref": batch["manifest"]["context_ref"], "candidate": candidate,
                   "receipt": receipt, "selection_manifest": batch["manifest"],
                   "candidate_set": batch["candidates"],
                   "candidate_set_id": batch_id, "batch_id": batch_id,
                   "source_binding_digest": batch["manifest"]["source_binding_digest"],
                   "reward": 1 if req.outcome == "ACCEPTED" else -1,
                   "learning_use_approved": True, "rights_status": req.rights_status,
                   "reason_status": req.reason_status, "split": req.split, "valid": True,
                   "split_group": receipt["split_group"], "reward_id": reward_id,
                   "receipt_digest": receipt["receipt_digest"],
                   "reward_semantics": "VERIFIED_CANDIDATE_CORRECTNESS_NOT_STUDENT_LEARNING_GAIN"}
        outcome["reward_digest"] = _digest(outcome)
        conn.execute("INSERT INTO candidate_rewards VALUES(?,?,?,?,?,?,?,?)",
                     (reward_id, session_id, batch_id, req.candidate_id, candidate["rank"],
                      1 if req.outcome == "ACCEPTED" else -1,
                      json.dumps(outcome, ensure_ascii=False), receipt["created_at"]))
    return receipt


def learn_policy(conn: sqlite3.Connection, session_id: str, context_ref: str, expected_revision: int,
                  source_binding_digest: str) -> dict:
    current = policy(conn, session_id, context_ref, source_binding_digest)
    if current["revision"] != expected_revision:
        raise CandidateGateError(409, "후보 탐색 정책이 변경됐습니다. 최신 상태를 다시 불러오세요.")
    old_learned = set(current["learned_reward_ids"])
    examples = valid_reward_examples(conn, session_id, source_binding_digest)
    context_examples = [example for example in examples if example["context_ref"] == context_ref]
    examples = [example for example in context_examples if example["split"] == "TRAIN"]
    learned = {example["reward_id"] for example in examples}
    if learned == old_learned and current.get("learning_kind") == "VERIFIED_RECEIPT_CASE_STATISTICS":
        return {**current, "new_reward_count": 0}
    current.update({"effective_max_rank": 3, "effective_score_margin": 0.0, "rank_statistics": {}})
    stats = current["rank_statistics"]
    for outcome in examples:
        rank = outcome["candidate"]["rank"]
        key = str(rank) if rank is not None else "HUMAN_INSIGHT"
        statistic = stats.setdefault(key, {"accepted": 0, "rejected": 0, "mean_reward": 0})
        statistic["accepted" if outcome["reward"] == 1 else "rejected"] += 1
        count = statistic["accepted"] + statistic["rejected"]
        statistic["mean_reward"] = (statistic["accepted"] - statistic["rejected"]) / count
        if outcome["reward"] == 1 and rank is not None:
            current["effective_max_rank"] = min(MAX_CANDIDATE_RANK,
                                                max(current["effective_max_rank"], rank))
            best = max((c["model_score"] for c in outcome["candidate_set"] if c["model_score"] is not None), default=0)
            if outcome["candidate"]["model_score"] is not None:
                current["effective_score_margin"] = max(current.get("effective_score_margin", 0),
                                                        best - outcome["candidate"]["model_score"])
    current.update({"revision": current["revision"] + 1, "learned_reward_ids": sorted(learned),
                    "learned_reward_count": len(learned), "rank_statistics": stats})
    # The live path uses reversible receipt cases, not reinforced self-training
    # of a generative neural network. The offline training module is preserved
    # for separate, explicitly initiated research, and is not called here.
    current.pop("content_policy", None)
    current["case_statistics"] = {}
    for example in examples:
        for key in _case_keys(example["candidate"], example["receipt"]["verification_criteria"]):
            statistic = current["case_statistics"].setdefault(key, {"count": 0, "reward_sum": 0,
                                                                    "receipt_ids": []})
            statistic["count"] += 1
            statistic["reward_sum"] += example["reward"]
            statistic["receipt_ids"].append(example["receipt"]["receipt_id"])
            statistic["mean_reward"] = statistic["reward_sum"] / statistic["count"]
    current.update({"learning_kind": "VERIFIED_RECEIPT_CASE_STATISTICS", "model_weights_updated": False,
                    "graph_parameters_trained": False,
                    "training_metrics": {"train_sample_count": len(examples),
                                         "eval_sample_count": len(context_examples) - len(examples),
                                         "gradient_steps": 0, "neural_training_performed": False,
                                         "educational_efficacy_tested": False}})
    current["accepted_reason_memory"] = [{"candidate_id": example["candidate"]["candidate_id"],
                                           "concept_refs": example["candidate"]["concept_refs"],
                                           "hypothesis_type": example["candidate"]["hypothesis_type"],
                                           "verified_reason": example["receipt"]["reason"],
                                           "batch_id": example["batch_id"],
                                           "receipt_id": example["receipt"]["receipt_id"],
                                           "receipt_digest": example["receipt_digest"],
                                           "source_binding_digest": example["source_binding_digest"],
                                           "evidence_refs": sorted(candidate_used_evidence_refs(
                                               {"manifest": example["selection_manifest"]}, example["candidate"],
                                               example["receipt"])),
                                           "verification_criteria": example["receipt"]["verification_criteria"],
                                           "actual_cause_confirmed": example["receipt"].get("actual_cause_confirmed", False)}
                                          for example in examples if example["reward"] == 1][-30:]
    conn.execute("INSERT INTO candidate_policies VALUES(?,?,?,?) ON CONFLICT(session_id,context_ref) DO UPDATE SET "
                 "revision=excluded.revision,policy_json=excluded.policy_json",
                 (session_id, context_ref, current["revision"], json.dumps(current, ensure_ascii=False)))
    return {**current, "new_reward_count": len(learned - old_learned),
            "invalidated_reward_count": len(old_learned - learned)}


def valid_reward_examples(conn: sqlite3.Connection, session_id: str, source_binding_digest: str) -> list[dict]:
    examples = []
    group_splits = {}
    for row in conn.execute("SELECT * FROM candidate_rewards WHERE session_id=? ORDER BY created_at,reward_id",
                            (session_id,)).fetchall():
        outcome = json.loads(row["outcome_json"])
        digest = outcome.pop("reward_digest")
        if not secrets.compare_digest(digest, _digest(outcome)):
            raise CandidateGateError(423, "학습 결과 원장의 무결성을 확인할 수 없습니다.")
        outcome["reward_digest"] = digest
        if outcome["source_binding_digest"] != source_binding_digest or invalidated(conn, row["batch_id"], row["candidate_id"]):
            continue
        batch = get_batch(conn, session_id, row["batch_id"], source_binding_digest=source_binding_digest)
        receipt = latest_review(conn, row["batch_id"], row["candidate_id"])
        if not receipt or receipt["ground_truth_status"] != "VERIFIED" or not receipt["teacher_verified"] or \
                not receipt["learning_use_approved"] or receipt["reason_status"] != "TEACHER_CHECKED" or \
                receipt["rights_status"] != "APPROVED" or receipt["status"] != ("ACCEPTED" if row["reward"] == 1 else "REJECTED") or \
                outcome["receipt_digest"] != receipt["receipt_digest"] or outcome["receipt"] != receipt or \
                outcome["selection_manifest"] != batch["manifest"] or outcome["candidate"]["rank"] != row["original_rank"] or \
                outcome["reward"] != row["reward"]:
            raise CandidateGateError(423, "정답 근거·원래 후보·학습사용 승인과 보상 기록이 일치하지 않습니다.")
        if outcome["reward_id"] != row["reward_id"]:
            raise CandidateGateError(423, "학습 결과 식별자와 원장이 일치하지 않습니다.")
        group = outcome["split_group"]
        if group in group_splits and group_splits[group] != outcome["split"]:
            raise CandidateGateError(423, "같은 문항 가족의 학습·평가 자료가 섞여 있습니다.")
        group_splits[group] = outcome["split"]
        examples.append(outcome)
    return examples


def reward_dataset(conn: sqlite3.Connection, session_id: str, source_binding_digest: str) -> list[dict]:
    return valid_reward_examples(conn, session_id, source_binding_digest)
