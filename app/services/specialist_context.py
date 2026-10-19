"""Evidence and goal preparation for specialist review, without provider calls.

This module does not establish truth, infer deception, issue operational authority,
admit a training job, or change model weights. Callers must authenticate users and
load source permissions/revisions from their authoritative storage.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, model_validator

Use = Literal["internal_review", "specialist_review", "training"]
Check = Literal["passed", "failed", "unknown"]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Source(Record):
    source_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    current_revision: int = Field(ge=1)
    independence_family: str = Field(min_length=1, max_length=160)
    permitted_uses: tuple[Use, ...] = ()
    revoked: StrictBool = False
    quality: Literal["usable", "uncertain", "unavailable"] = "uncertain"
    valid_until: AwareDatetime | None = None


class Fact(Record):
    fact_id: str = Field(min_length=1, max_length=160)
    source_id: str = Field(min_length=1, max_length=160)
    source_locator: str = Field(min_length=1, max_length=1000)
    event_id: str = Field(min_length=1, max_length=160)
    subject: str = Field(min_length=1, max_length=240)
    predicate: str = Field(min_length=1, max_length=160)
    value: str = Field(min_length=1, max_length=4000)
    value_type: Literal["text", "number", "boolean"] = "text"
    unit: str = Field(min_length=1, max_length=80)
    # Different observation scopes must not be silently merged.
    observation_scope: str = Field(min_length=1, max_length=240)
    kind: Literal["observation", "statement", "model_inference"]
    observed_at: AwareDatetime
    observed_until: AwareDatetime
    received_at: AwareDatetime
    clock_uncertainty_seconds: int = Field(default=0, ge=0, le=3600)

    @model_validator(mode="after")
    def check_interval_and_value(self):
        if self.observed_until < self.observed_at:
            raise ValueError("invalid_observation_interval")
        if self.value_type == "number":
            try:
                if not Decimal(self.value).is_finite():
                    raise ValueError("nonfinite_measurement")
            except InvalidOperation as exc:
                raise ValueError("invalid_measurement") from exc
        if self.value_type == "boolean" and self.value not in {"true", "false"}:
            raise ValueError("invalid_boolean_measurement")
        return self


class Goal(Record):
    goal_id: str = Field(min_length=1, max_length=160)
    purpose: str = Field(min_length=1, max_length=2000)
    last_completed: str = Field(default="", max_length=2000)
    unfinished: tuple[str, ...] = Field(default=(), max_length=100)
    parent_goal_id: str | None = Field(default=None, max_length=160)
    branch_reason: str = Field(default="", max_length=2000)


class Hypothesis(Record):
    hypothesis_id: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=2000)
    supporting_fact_ids: tuple[str, ...] = Field(default=(), max_length=100)
    opposing_fact_ids: tuple[str, ...] = Field(default=(), max_length=100)
    missing_information: tuple[str, ...] = Field(default=(), max_length=100)


class KnowledgeAccess(Record):
    actor_id: str = Field(min_length=1, max_length=160)
    fact_id: str = Field(min_length=1, max_length=160)
    reported_access_at: AwareDatetime


class ContextFrame(Record):
    frame_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    domain: Literal["daily", "education", "legal", "medical", "fire", "police"]
    current_goal_id: str = Field(min_length=1, max_length=160)
    goals: tuple[Goal, ...] = Field(min_length=1, max_length=100)
    sources: tuple[Source, ...] = Field(default=(), max_length=200)
    facts: tuple[Fact, ...] = Field(default=(), max_length=200)
    hypotheses: tuple[Hypothesis, ...] = Field(default=(), max_length=100)
    knowledge_access: tuple[KnowledgeAccess, ...] = Field(default=(), max_length=500)
    input_completeness: Literal["complete", "unknown"] = "unknown"

    @model_validator(mode="after")
    def check_references(self):
        for items, attr in [(self.goals, "goal_id"), (self.sources, "source_id"),
                            (self.facts, "fact_id"), (self.hypotheses, "hypothesis_id")]:
            ids = [getattr(item, attr) for item in items]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate_record_id")
        goals = {g.goal_id for g in self.goals}
        sources = {s.source_id for s in self.sources}
        facts = {f.fact_id for f in self.facts}
        if self.current_goal_id not in goals:
            raise ValueError("unknown_current_goal")
        for goal in self.goals:
            if goal.parent_goal_id and goal.parent_goal_id not in goals:
                raise ValueError("unknown_parent_goal")
            seen, node = set(), goal
            by_id = {g.goal_id: g for g in self.goals}
            while node.parent_goal_id:
                if node.goal_id in seen:
                    raise ValueError("cyclic_goal_branch")
                seen.add(node.goal_id)
                node = by_id[node.parent_goal_id]
        if any(f.source_id not in sources for f in self.facts):
            raise ValueError("unknown_fact_source")
        for h in self.hypotheses:
            support, opposing = set(h.supporting_fact_ids), set(h.opposing_fact_ids)
            if support & opposing or not (support | opposing) <= facts:
                raise ValueError("invalid_hypothesis_references")
        if any(k.fact_id not in facts for k in self.knowledge_access):
            raise ValueError("unknown_knowledge_fact")
        return self


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone_required")
    return value.astimezone(timezone.utc)


def _source_reasons(source: Source, purpose: Use, now: datetime) -> list[str]:
    reasons = []
    if source.revoked:
        reasons.append("revoked")
    if source.revision != source.current_revision:
        reasons.append("superseded_revision")
    if purpose not in source.permitted_uses:
        reasons.append("purpose_not_permitted")
    if source.quality != "usable":
        reasons.append("source_quality_unconfirmed")
    if source.valid_until is not None and source.valid_until <= now:
        reasons.append("source_expired")
    return reasons


def _usable_facts(frame: ContextFrame, purpose: Use, now: datetime, max_age: int | None = None):
    sources = {s.source_id: s for s in frame.sources}
    usable, excluded = [], {}
    for fact in frame.facts:
        reasons = _source_reasons(sources[fact.source_id], purpose, now)
        tolerance = timedelta(seconds=fact.clock_uncertainty_seconds)
        if fact.received_at > now or fact.observed_until > now + tolerance:
            reasons.append("future_information")
        if max_age is not None and now - fact.observed_until > timedelta(seconds=max_age) + tolerance:
            reasons.append("observation_too_old")
        if reasons:
            excluded[fact.fact_id] = reasons
        else:
            usable.append(fact)
    return usable, excluded


def _comparable_key(fact: Fact):
    return (fact.event_id, fact.subject, fact.predicate, fact.unit,
            fact.observation_scope, fact.value_type)


def _value(fact: Fact):
    return Decimal(fact.value) if fact.value_type == "number" else fact.value


def _overlap(a: Fact, b: Fact) -> bool:
    a_error = timedelta(seconds=a.clock_uncertainty_seconds)
    b_error = timedelta(seconds=b.clock_uncertainty_seconds)
    return (a.observed_at - a_error <= b.observed_until + b_error
            and b.observed_at - b_error <= a.observed_until + a_error)


def analyze_context(frame: ContextFrame, *, now: datetime) -> dict:
    """Report scoped inconsistencies, never truth or intentional deception."""
    now = _aware(now)
    facts, excluded = _usable_facts(frame, "internal_review", now)
    groups = defaultdict(list)
    for fact in facts:
        groups[_comparable_key(fact)].append(fact)
    conflicts = []
    for group in groups.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if _overlap(a, b) and _value(a) != _value(b):
                    conflicts.append({"fact_ids": [a.fact_id, b.fact_id],
                                      "state": "needs_review", "cause": "unresolved"})
    sources = {s.source_id: s for s in frame.sources}
    families = {sources[f.source_id].independence_family for f in facts if f.kind != "model_inference"}
    return {"frame_id": frame.frame_id, "revision": frame.revision,
            "current_goal_id": frame.current_goal_id,
            "goals": [g.model_dump(mode="json") for g in frame.goals],
            "conflicts": conflicts, "excluded_facts": excluded,
            "usable_fact_ids": [f.fact_id for f in facts],
            "non_model_source_family_count": len(families),
            "family_count_is_not_truth_probability": True,
            "truth_or_intent_established": False, "model_training_performed": False}


def choose_goal(frame: ContextFrame, *, goal_id: str, expected_revision: int) -> ContextFrame:
    """Apply an authenticated caller's explicit selection; preserve all branches."""
    if expected_revision != frame.revision:
        raise ValueError("stale_goal_choice")
    if goal_id not in {g.goal_id for g in frame.goals}:
        raise ValueError("unknown_selected_goal")
    return frame.model_copy(update={"current_goal_id": goal_id, "revision": frame.revision + 1})


def knowledge_before(frame: ContextFrame, *, actor_id: str, at: datetime) -> dict:
    """Return reported information access, not a claim about someone's mind."""
    at = _aware(at)
    ids = sorted({k.fact_id for k in frame.knowledge_access
                  if k.actor_id == actor_id and k.reported_access_at <= at})
    return {"actor_id": actor_id, "fact_ids": ids,
            "basis": "reported_information_access", "truth_established": False}


def prepare_specialist_review(frame: ContextFrame, *, now: datetime,
                              max_observation_age_seconds: int = 300) -> dict:
    """Prepare authorized observations. No transport, dispatch or actuation."""
    now = _aware(now)
    if not 0 <= max_observation_age_seconds <= 86400:
        raise ValueError("invalid_freshness_window")
    if frame.input_completeness != "complete":
        return {"state": "held_input_scope_unknown", "facts": [], "hypotheses": [],
                "external_call_performed": False, "execution_authorized": False}
    facts, excluded = _usable_facts(frame, "specialist_review", now, max_observation_age_seconds)
    allowed = {f.fact_id for f in facts}
    hypotheses = []
    for h in frame.hypotheses:
        refs = set(h.supporting_fact_ids) | set(h.opposing_fact_ids)
        if refs and refs <= allowed:
            hypotheses.append({**h.model_dump(mode="json"), "state": "unverified_hypothesis"})
    return {"state": "review_package_prepared", "domain": frame.domain,
            "facts": [f.model_dump(mode="json") for f in facts], "hypotheses": hypotheses,
            "not_shared_count": len(excluded), "use": "professional_review_only",
            "absence_of_observation_is_not_safety": True,
            "external_call_performed": False, "execution_authorized": False}


class ConditionMapping(Record):
    source_relation: str = Field(min_length=1, max_length=500)
    target_relation: str = Field(min_length=1, max_length=500)
    satisfied: StrictBool | None


class TransferPlan(Record):
    source_domain: str = Field(min_length=1, max_length=160)
    target_domain: str = Field(min_length=1, max_length=160)
    mappings: tuple[ConditionMapping, ...] = Field(min_length=1, max_length=100)
    intended_label: Literal["apply", "refuse", "request_more"]
    training_family_ids: tuple[str, ...] = Field(min_length=1, max_length=1000)
    holdout_family_ids: tuple[str, ...] = Field(min_length=1, max_length=1000)
    source_rights_check: Check = "unknown"
    expert_target_review: Check = "unknown"
    critical_safety_check: Check = "unknown"
    relationship_preservation_check: Check = "unknown"


def preflight_transfer(frame: ContextFrame, plan: TransferPlan, *, now: datetime) -> dict:
    """Build a review decision, not permission to start a training job."""
    now = _aware(now)
    conditions = [m.satisfied for m in plan.mappings]
    label = "refuse" if False in conditions else "request_more" if None in conditions else "apply"
    reasons = []
    if not frame.facts or frame.input_completeness != "complete":
        reasons.append("training_input_incomplete")
    _, excluded = _usable_facts(frame, "training", now)
    if excluded:
        reasons.append("source_not_eligible_for_training_purpose")
    sources = {s.source_id: s for s in frame.sources}
    actual_families = {sources[f.source_id].independence_family for f in frame.facts}
    if actual_families != set(plan.training_family_ids):
        reasons.append("training_family_manifest_mismatch")
    if set(plan.training_family_ids) & set(plan.holdout_family_ids):
        reasons.append("training_holdout_family_overlap")
    if plan.intended_label != label:
        reasons.append("condition_label_mismatch")
    for name in ("source_rights_check", "expert_target_review", "critical_safety_check",
                 "relationship_preservation_check"):
        if getattr(plan, name) != "passed":
            reasons.append(name + "_not_passed")
    return {"state": "held" if reasons else "ready_for_pipeline_review",
            "reasons": reasons, "condition_label": label,
            "requires_authoritative_pipeline_admission": True,
            "training_submission_performed": False, "model_weights_updated": False}
