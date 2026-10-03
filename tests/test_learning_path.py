"""Focused checks for a one-unit macro/micro map and conservative release."""

import unittest

from app.services.learning_path import (
    AssessmentAttempt, LearningPath, PathNode, ReasoningTransition, TeachingAsset,
    affected_assets, assess_attempt, learning_map, path_digest, publication_issues,
)


def reviewed_unit() -> LearningPath:
    return LearningPath(
        revision_id="unit-1", goal_id="goal", current_node_id="basic",
        conditions={"slope": "nonzero", "domain": "real"},
        nodes=[
            PathNode(node_id="basic", label="기초 개념", stage="BASIC"),
            PathNode(node_id="middle", label="조건 검토", stage="CONNECTION", prerequisite_ids=["basic"]),
            PathNode(node_id="goal", label="목표 문제", stage="GOAL", prerequisite_ids=["middle"]),
            PathNode(node_id="side", label="독립된 보충", stage="CONNECTION", prerequisite_ids=["basic"]),
        ],
        transitions=[
            ReasoningTransition(
                transition_id="first", from_node="basic", to_node="middle",
                premise="함수의 좌우 변화율", condition_keys=["slope"],
                rule="부호를 비교", conclusion="변화율이 다르다", evidence_ref="proof:first",
                exception="기울기가 0이면 다시 검사", validation_snapshot={"slope": "nonzero"},
                validation_status="PASSED", proposition_result="TRUE"),
            ReasoningTransition(
                transition_id="second", from_node="middle", to_node="goal",
                premise="앞의 결론", condition_keys=["domain"], rule="반례를 비교",
                conclusion="항상 성립한다는 명제는 거짓", evidence_ref="proof:counterexample",
                exception="해당 조건 밖에서는 결론을 확장하지 않음",
                validation_snapshot={"slope": "nonzero", "domain": "real"},
                validation_status="PASSED", proposition_result="FALSE"),
            ReasoningTransition(
                transition_id="side-rule", from_node="basic", to_node="side",
                premise="별도 정의", condition_keys=["domain"], rule="정의를 적용",
                conclusion="보충 예시", evidence_ref="proof:side",
                validation_snapshot={"domain": "real"}, validation_status="PASSED",
                proposition_result="TRUE"),
        ],
        assets=[
            TeachingAsset(asset_id="scene", kind="SCENE", depends_on=["first"],
                          condition_snapshot={"slope": "nonzero"}, content="기초 장면"),
            TeachingAsset(asset_id="video", kind="VIDEO", depends_on=["second"],
                          condition_snapshot={"slope": "nonzero", "domain": "real"}, media_id="approved-video"),
            TeachingAsset(asset_id="voice", kind="AUDIO", depends_on=["second"],
                          condition_snapshot={"slope": "nonzero", "domain": "real"}, media_id="approved-voice"),
            TeachingAsset(asset_id="transfer", kind="QUESTION", depends_on=["second"],
                          condition_snapshot={"slope": "nonzero", "domain": "real"},
                          content="조건이 바뀐 문제", question_role="TRANSFER"),
            TeachingAsset(asset_id="answer", kind="ANSWER", depends_on=["second"],
                          condition_snapshot={"slope": "nonzero", "domain": "real"}, content="검토된 답"),
            TeachingAsset(asset_id="side-audio", kind="AUDIO", depends_on=["side-rule"],
                          condition_snapshot={"domain": "real"}, content="별도 설명"),
        ],
    )


class LearningPathTests(unittest.TestCase):
    def test_map_shows_goal_prerequisites_and_false_counterexample_without_socratic_gate(self):
        path = reviewed_unit()
        self.assertEqual(publication_issues(path), ["TEACHER_REVIEW_REQUIRED"])
        self.assertEqual(publication_issues(path, teacher_reviewed_digest=path_digest(path)), [])
        projected = learning_map(path)
        self.assertEqual(projected["goal_id"], "goal")
        self.assertEqual(projected["nodes"][2]["prerequisite_ids"], ["middle"])
        self.assertEqual(projected["connections"][1]["proposition_result"], "FALSE")
        self.assertEqual(projected["connections"][1]["exception"], "해당 조건 밖에서는 결론을 확장하지 않음")

    def test_changed_upstream_condition_blocks_old_output_and_invalidates_descendants_only(self):
        old = reviewed_unit()
        changed = old.model_copy(deep=True)
        changed.revision_id = "unit-2"
        changed.conditions["slope"] = "zero"
        self.assertEqual(affected_assets(old, changed), ["answer", "scene", "transfer", "video", "voice"])
        issues = publication_issues(changed, teacher_reviewed_digest=path_digest(changed))
        self.assertIn("STALE_TRANSITION_VALIDATION:first", issues)
        self.assertIn("STALE_TRANSITION_VALIDATION:second", issues)
        self.assertIn("STALE_ASSET_CONDITION:voice", issues)
        self.assertNotIn("STALE_ASSET_CONDITION:side-audio", issues)

    def test_unknown_dependency_widens_hold_and_unreviewed_content_cannot_reuse_release(self):
        old = reviewed_unit()
        reviewed_digest = path_digest(old)
        changed = old.model_copy(deep=True)
        changed.assets[0].content = "검토하지 않은 새 장면"
        self.assertIn("TEACHER_REVIEW_REQUIRED", publication_issues(changed, teacher_reviewed_digest=reviewed_digest))
        changed = old.model_copy(deep=True)
        changed.conditions["unmapped"] = "new"
        self.assertEqual(affected_assets(old, changed), sorted(a.asset_id for a in old.assets))
        changed = old.model_copy(deep=True)
        changed.nodes[-1].label = "새 보충 설명"
        self.assertEqual(affected_assets(old, changed), sorted(a.asset_id for a in old.assets))

    def test_helped_practice_and_independent_application_are_distinct(self):
        path = reviewed_unit()
        digest = path_digest(path)
        base = AssessmentAttempt(
            path_revision_id="unit-1", question_asset_id="transfer",
            condition_snapshot={"slope": "nonzero", "domain": "real"}, scored_correct=True)
        independent = assess_attempt(path, base, teacher_reviewed_digest=digest)
        self.assertEqual(independent.status, "INDEPENDENT_APPLICATION")
        self.assertFalse(independent.mastery_confirmed)  # delayed recheck is still needed
        helped = base.model_copy(update={"hints_used": True})
        self.assertEqual(assess_attempt(path, helped, teacher_reviewed_digest=digest).status, "ASSISTED_PRACTICE")
        exposed = base.model_copy(update={"answer_disclosed": True})
        self.assertEqual(assess_attempt(path, exposed, teacher_reviewed_digest=digest).status, "ASSISTED_PRACTICE")
        stale = base.model_copy(update={"condition_snapshot": {"slope": "zero", "domain": "real"}})
        self.assertIn("STALE_RESPONSE_CONDITION", assess_attempt(path, stale, teacher_reviewed_digest=digest).reasons)

    def test_missing_evidence_and_condition_inconsistency_hold_publication(self):
        path = reviewed_unit()
        path.transitions[1].evidence_ref = ""
        path.assets[3].condition_snapshot = {"domain": "real"}
        issues = publication_issues(path, teacher_reviewed_digest=path_digest(path))
        self.assertIn("TRANSITION_UNVERIFIED:second", issues)
        self.assertIn("STALE_ASSET_CONDITION:transfer", issues)


if __name__ == "__main__":
    unittest.main()
