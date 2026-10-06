"""Read an approved teaching graph as an evidence map, never a learned diagnosis.

The graph is fixed by the teacher's published revision. This module does not
train graph values, propagate mastery probabilities, or infer causal truth
from attention, similarity, an incorrect answer, or a missing observation.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.learning_path import LearningPath, path_digest, publication_issues


class ConditionObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    condition_key: str = Field(min_length=1, max_length=100)
    status: Literal["MET", "UNMET", "UNKNOWN", "CONFLICTING"] = "UNKNOWN"
    evidence_refs: list[str] = Field(default_factory=list, max_length=30)
    teacher_checked: bool = False

    @model_validator(mode="after")
    def confirmed_observation_needs_evidence(self):
        if self.status in {"MET", "UNMET"} and (not self.teacher_checked or not self.evidence_refs or
                                                  any(not ref.strip() for ref in self.evidence_refs)):
            raise ValueError("확인한 조건 상태에는 교사 확인과 실제 관찰 근거가 필요합니다.")
        return self


class CauseMapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    focus_node_ids: list[str] = Field(default_factory=list, max_length=30)
    observations: list[ConditionObservation] = Field(default_factory=list, max_length=50)


class CauseMapError(ValueError):
    pass


def approved_cause_map(path: LearningPath, request: CauseMapRequest, *, reviewed_digest: str) -> dict:
    """Collect prerequisites and joint conditions without changing graph state.

    A shared condition or similar concept supplies an alternative to inspect,
    not an inferred cause. Unknown evidence remains unknown in every row.
    """
    issues = publication_issues(path, teacher_reviewed_digest=reviewed_digest)
    if issues:
        raise CauseMapError("현재 교사가 검토·공개한 학습경로만 원인 탐색에 사용할 수 있습니다.")
    nodes = {node.node_id: node for node in path.nodes}
    focus = set(request.focus_node_ids or [path.goal_id])
    if not focus <= nodes.keys():
        raise CauseMapError("현재 학습경로에 없는 집중 개념입니다.")
    observed = {item.condition_key: item for item in request.observations}
    if len(observed) != len(request.observations) or not observed.keys() <= path.conditions.keys():
        raise CauseMapError("관찰 조건이 중복되었거나 현재 학습경로에 없습니다.")
    prerequisites = set(focus)
    frontier = set(focus)
    while frontier:
        next_nodes = {parent for node_id in frontier for parent in nodes[node_id].prerequisite_ids}
        frontier = next_nodes - prerequisites
        prerequisites |= frontier
    focused_transitions = [transition for transition in path.transitions
                           if transition.to_node in prerequisites]
    joint_keys = {key for transition in focused_transitions for key in transition.condition_keys}
    # A common condition crosses units without being converted to a prerequisite.
    joint_transitions = [transition for transition in path.transitions
                         if joint_keys & set(transition.condition_keys)]
    context_nodes = prerequisites | {node_id for transition in joint_transitions
                                    for node_id in (transition.from_node, transition.to_node)}
    relations = [relation for relation in path.concept_relations
                 if relation.status == "APPROVED" and
                 (relation.from_node in context_nodes or relation.to_node in context_nodes)]
    context_nodes |= {node_id for relation in relations for node_id in (relation.from_node, relation.to_node)}
    used_keys = joint_keys | {key for transition in joint_transitions for key in transition.condition_keys}
    conditions = []
    for key in sorted(used_keys):
        observation = observed.get(key)
        conditions.append({"condition_key": key, "condition_value": path.conditions[key],
                           "status": observation.status if observation else "UNKNOWN",
                           "evidence_refs": observation.evidence_refs if observation else [],
                           "teacher_checked": observation.teacher_checked if observation else False,
                           "missing_is_failure": False})
    evidence_refs = {transition.evidence_ref for transition in [*focused_transitions, *joint_transitions]
                     if transition.evidence_ref}
    evidence_refs |= {relation.evidence_ref for relation in relations if relation.evidence_ref}
    evidence_refs |= {ref for item in conditions for ref in item["evidence_refs"]}
    return {"path_revision_id": path.revision_id, "reviewed_path_digest": path_digest(path),
            "focus_node_ids": sorted(focus), "prerequisite_node_ids": sorted(prerequisites - focus),
            "context_nodes": [nodes[node_id].model_dump() for node_id in sorted(context_nodes)],
            "relations": [relation.model_dump() for relation in relations],
            "joint_condition_groups": [{"transition_id": transition.transition_id,
                                        "from_node": transition.from_node, "to_node": transition.to_node,
                                        "all_condition_keys": transition.condition_keys,
                                        "rule": transition.rule} for transition in joint_transitions],
            "condition_observations": conditions, "evidence_refs": sorted(evidence_refs),
            "selection_semantics": "TEACHER_APPROVED_RELATIONS_AND_JOINT_CONDITIONS_FOR_HYPOTHESIS_REVIEW",
            "causal_truth_inferred": False, "mastery_probabilities_updated": False,
            "graph_parameters_trained": False, "unknown_propagated_as_unmet": False}
