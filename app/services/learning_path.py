"""A small, reviewable macro/micro learning path for one teaching unit.

This is a draft projection and a publication guard, not an automatic proof of
subject-matter correctness or an implementation of the Education M output
state machine. A caller must supply a *server-stored* teacher review digest;
never accept that digest as a client assertion. Media permission and scoring
are also checked by the caller before using the relevant IDs and decisions.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PathModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathNode(PathModel):
    node_id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=500)
    stage: Literal["BASIC", "CONNECTION", "GOAL"]
    prerequisite_ids: list[str] = Field(default_factory=list, max_length=30)


class ReasoningTransition(PathModel):
    transition_id: str = Field(min_length=1, max_length=100)
    from_node: str
    to_node: str
    premise: str = Field(min_length=1, max_length=3000)
    condition_keys: list[str] = Field(min_length=1, max_length=30)
    rule: str = Field(min_length=1, max_length=3000)
    conclusion: str = Field(min_length=1, max_length=3000)
    evidence_ref: str = Field(default="", max_length=500)
    exception: str = Field(default="", max_length=3000)
    # Includes inherited conditions from every upstream transition. A change
    # invalidates downstream reasoning even when its own local key is stable.
    validation_snapshot: dict[str, str] = Field(default_factory=dict, max_length=50)
    validation_status: Literal["PENDING", "PASSED", "FAILED", "UNDETERMINED"] = "PENDING"
    proposition_result: Literal["TRUE", "FALSE", "UNDETERMINED"] = "UNDETERMINED"


class TeachingAsset(PathModel):
    asset_id: str = Field(min_length=1, max_length=100)
    kind: Literal["SCENE", "IMAGE", "VIDEO", "AUDIO", "QUESTION", "ANSWER", "TEXT"]
    depends_on: list[str] = Field(min_length=1, max_length=50)
    # Only the conditions actually used by depended-on transitions belong here.
    condition_snapshot: dict[str, str] = Field(default_factory=dict, max_length=50)
    content: str = Field(default="", max_length=12000)
    media_id: str | None = Field(default=None, max_length=100)
    question_role: Literal["PRACTICE", "TRANSFER"] | None = None


class LearningPath(PathModel):
    revision_id: str = Field(min_length=1, max_length=100)
    goal_id: str
    current_node_id: str
    conditions: dict[str, str] = Field(min_length=1, max_length=50)
    nodes: list[PathNode] = Field(min_length=2, max_length=100)
    transitions: list[ReasoningTransition] = Field(min_length=1, max_length=150)
    assets: list[TeachingAsset] = Field(min_length=1, max_length=300)


class AssessmentAttempt(PathModel):
    path_revision_id: str
    question_asset_id: str
    condition_snapshot: dict[str, str]
    # This is the server's checked scoring result, not an answer from the client.
    scored_correct: bool
    hints_used: bool = False
    explanation_shown: bool = False
    answer_disclosed: bool = False
    repeated_attempt: bool = False


class ApplicationDecision(PathModel):
    status: Literal["BLOCKED", "INCORRECT", "ASSISTED_PRACTICE", "PRACTICE_ONLY", "INDEPENDENT_APPLICATION"]
    mastery_confirmed: bool = False
    reasons: list[str] = Field(default_factory=list)


def path_digest(path: LearningPath) -> str:
    """Bind teacher review to the exact complete path, including all assets."""
    encoded = json.dumps(path.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _structure_issues(path: LearningPath) -> list[str]:
    issues: list[str] = []
    for kind, identifiers in (
        ("NODE", [n.node_id for n in path.nodes]),
        ("TRANSITION", [t.transition_id for t in path.transitions]),
        ("ASSET", [a.asset_id for a in path.assets]),
    ):
        if len(identifiers) != len(set(identifiers)):
            issues.append(f"DUPLICATE_{kind}_ID")
    nodes = {n.node_id: n for n in path.nodes}
    transitions = {t.transition_id: t for t in path.transitions}
    if path.goal_id not in nodes or nodes[path.goal_id].stage != "GOAL":
        issues.append("INVALID_GOAL")
    if path.current_node_id not in nodes:
        issues.append("UNKNOWN_CURRENT_NODE")
    basic_ids = {n.node_id for n in path.nodes if n.stage == "BASIC"}
    if not basic_ids:
        issues.append("NO_BASIC_CONCEPT")
    for node in path.nodes:
        if len(node.prerequisite_ids) != len(set(node.prerequisite_ids)) or any(
            ref not in nodes or ref == node.node_id for ref in node.prerequisite_ids
        ):
            issues.append(f"INVALID_PREREQUISITE:{node.node_id}")
        for prerequisite_id in node.prerequisite_ids:
            if not any(t.from_node == prerequisite_id and t.to_node == node.node_id for t in path.transitions):
                issues.append(f"UNRESOLVED_PREREQUISITE:{node.node_id}")
    for transition in path.transitions:
        if transition.from_node not in nodes or transition.to_node not in nodes or transition.from_node == transition.to_node:
            issues.append(f"INVALID_TRANSITION_ENDPOINT:{transition.transition_id}")
        elif transition.from_node not in nodes[transition.to_node].prerequisite_ids:
            issues.append(f"UNDECLARED_PREREQUISITE:{transition.transition_id}")
        if len(transition.condition_keys) != len(set(transition.condition_keys)) or not set(transition.condition_keys) <= path.conditions.keys():
            issues.append(f"UNKNOWN_CONDITION:{transition.transition_id}")
        # A false proposition may be a valid counterexample if validation passed.
        if transition.validation_status != "PASSED" or not transition.evidence_ref.strip() or transition.proposition_result == "UNDETERMINED":
            issues.append(f"TRANSITION_UNVERIFIED:{transition.transition_id}")

    # Reject a cycle and require at least one connected path from basics to goal.
    successors: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for t in path.transitions:
        if t.from_node in nodes and t.to_node in nodes:
            successors[t.from_node].add(t.to_node)
    visit_state: dict[str, int] = {}

    def visit(node_id: str) -> bool:
        if visit_state.get(node_id) == 1:
            return True
        if visit_state.get(node_id) == 2:
            return False
        visit_state[node_id] = 1
        found = any(visit(child) for child in successors[node_id])
        visit_state[node_id] = 2
        return found

    if any(visit(node_id) for node_id in nodes if node_id not in visit_state):
        issues.append("CYCLIC_PATH")
    frontier = set(basic_ids)
    reached = set(frontier)
    while frontier:
        frontier = set().union(*(successors[node] for node in frontier)) - reached
        reached |= frontier
    if path.goal_id not in reached:
        issues.append("GOAL_UNREACHABLE")

    inherited_keys: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for _ in range(len(nodes) + 1):
        updated = False
        for transition in path.transitions:
            if transition.from_node in nodes and transition.to_node in nodes:
                keys = inherited_keys[transition.from_node] | set(transition.condition_keys)
                if not keys <= inherited_keys[transition.to_node]:
                    inherited_keys[transition.to_node] |= keys
                    updated = True
        if not updated:
            break
    transition_keys = {
        t.transition_id: inherited_keys.get(t.from_node, set()) | set(t.condition_keys)
        for t in path.transitions
    }
    for transition in path.transitions:
        keys = transition_keys[transition.transition_id]
        if set(transition.validation_snapshot) != keys or any(
            transition.validation_snapshot[key] != path.conditions.get(key) for key in keys
        ):
            issues.append(f"STALE_TRANSITION_VALIDATION:{transition.transition_id}")

    for asset in path.assets:
        if len(asset.depends_on) != len(set(asset.depends_on)) or not set(asset.depends_on) <= transitions.keys():
            issues.append(f"UNKNOWN_ASSET_DEPENDENCY:{asset.asset_id}")
            continue
        keys = set().union(*(transition_keys[tid] for tid in asset.depends_on))
        if set(asset.condition_snapshot) != keys or any(
            asset.condition_snapshot[key] != path.conditions.get(key) for key in keys
        ):
            issues.append(f"STALE_ASSET_CONDITION:{asset.asset_id}")
        if not asset.content.strip() and not asset.media_id:
            issues.append(f"EMPTY_ASSET:{asset.asset_id}")
        if asset.kind == "QUESTION" and asset.question_role is None:
            issues.append(f"QUESTION_ROLE_REQUIRED:{asset.asset_id}")
        elif asset.kind != "QUESTION" and asset.question_role is not None:
            issues.append(f"INVALID_QUESTION_ROLE:{asset.asset_id}")
    if not any(a.kind in {"SCENE", "IMAGE", "VIDEO"} for a in path.assets):
        issues.append("NO_VISUAL_SCENE")
    if not any(a.kind == "QUESTION" and a.question_role == "TRANSFER" for a in path.assets):
        issues.append("NO_TRANSFER_QUESTION")
    return sorted(set(issues))


def publication_issues(path: LearningPath, *, teacher_reviewed_digest: str | None = None) -> list[str]:
    """Gate a server-stored review and structurally current learning materials.

    This cannot certify that a cited source, reasoning rule or teacher review is
    substantively correct. The caller must verify rights to each media_id.
    """
    issues = _structure_issues(path)
    if teacher_reviewed_digest != path_digest(path):
        issues.append("TEACHER_REVIEW_REQUIRED")
    return sorted(set(issues))


def learning_map(path: LearningPath) -> dict:
    """Return a draft map for an authenticated teacher's preview."""
    return {
        "revision_id": path.revision_id,
        "goal_id": path.goal_id,
        "current_node_id": path.current_node_id,
        "nodes": [node.model_dump() for node in path.nodes],
        "connections": [{
            "transition_id": t.transition_id, "from_node": t.from_node,
            "to_node": t.to_node, "premise": t.premise,
            "condition_keys": t.condition_keys, "rule": t.rule,
            "conclusion": t.conclusion, "evidence_ref": t.evidence_ref,
            "exception": t.exception, "validation_snapshot": t.validation_snapshot,
            "validation_status": t.validation_status,
            "proposition_result": t.proposition_result,
        } for t in path.transitions],
        "issues": _structure_issues(path),
    }


def affected_assets(previous: LearningPath, updated: LearningPath) -> list[str]:
    """IDs whose old display/replay must be withdrawn after a change.

    An unknown dependency or invalid graph widens the hold to the whole unit.
    Removed assets are included because old display queues may still refer to
    them. This function reports affected IDs; the caller performs revocation.
    """
    old_assets = {a.asset_id: a for a in previous.assets}
    new_assets = {a.asset_id: a for a in updated.assets}
    all_ids = set(old_assets) | set(new_assets)
    unsafe_prefixes = (
        "DUPLICATE_", "INVALID_", "UNKNOWN_CONDITION:",
        "UNKNOWN_ASSET_DEPENDENCY:", "UNDECLARED_PREREQUISITE:",
        "UNRESOLVED_PREREQUISITE:", "CYCLIC_PATH", "GOAL_UNREACHABLE", "NO_BASIC_CONCEPT",
    )
    if any(issue.startswith(unsafe_prefixes) for issue in [*_structure_issues(previous), *_structure_issues(updated)]):
        return sorted(all_ids)
    if (previous.goal_id != updated.goal_id or previous.nodes != updated.nodes):
        return sorted(all_ids)
    old_transitions = {t.transition_id: t for t in previous.transitions}
    new_transitions = {t.transition_id: t for t in updated.transitions}
    changed_keys = {key for key in set(previous.conditions) | set(updated.conditions)
                    if previous.conditions.get(key) != updated.conditions.get(key)}
    if changed_keys - set().union(*(set(t.condition_keys) for t in [*previous.transitions, *updated.transitions])):
        return sorted(all_ids)
    changed = {tid for tid in set(old_transitions) | set(new_transitions)
               if tid not in old_transitions or tid not in new_transitions
               or old_transitions[tid] != new_transitions[tid]}
    changed |= {tid for tid, t in {**old_transitions, **new_transitions}.items()
                if changed_keys & set(t.condition_keys)}
    # A changed conclusion can alter downstream transitions and their scenes.
    all_transitions = [*previous.transitions, *updated.transitions]
    while True:
        reached_nodes = {t.to_node for t in all_transitions if t.transition_id in changed}
        expanded = changed | {t.transition_id for t in all_transitions if t.from_node in reached_nodes}
        if expanded == changed:
            break
        changed = expanded
    return sorted(asset_id for asset_id in all_ids if asset_id not in old_assets or asset_id not in new_assets
                  or old_assets[asset_id] != new_assets[asset_id]
                  or bool(set(old_assets[asset_id].depends_on) & changed))


def assess_attempt(path: LearningPath, attempt: AssessmentAttempt, *,
                   teacher_reviewed_digest: str | None = None) -> ApplicationDecision:
    """Classify a trusted scored attempt; one correct answer never proves mastery."""
    issues = publication_issues(path, teacher_reviewed_digest=teacher_reviewed_digest)
    question = next((a for a in path.assets if a.asset_id == attempt.question_asset_id), None)
    if question is None or question.kind != "QUESTION":
        issues.append("UNKNOWN_QUESTION")
    elif attempt.condition_snapshot != question.condition_snapshot:
        issues.append("STALE_RESPONSE_CONDITION")
    if attempt.path_revision_id != path.revision_id:
        issues.append("STALE_RESPONSE_REVISION")
    if issues:
        return ApplicationDecision(status="BLOCKED", reasons=sorted(set(issues)))
    if not attempt.scored_correct:
        return ApplicationDecision(status="INCORRECT")
    if attempt.hints_used or attempt.explanation_shown or attempt.answer_disclosed or attempt.repeated_attempt:
        return ApplicationDecision(status="ASSISTED_PRACTICE")
    if question.question_role != "TRANSFER":
        return ApplicationDecision(status="PRACTICE_ONLY")
    return ApplicationDecision(status="INDEPENDENT_APPLICATION")
