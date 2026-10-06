"""Focused checks for a one-unit macro/micro map and conservative release."""

import unittest
import hashlib
import json

from app.services.learning_path import (
    AssessmentAttempt, ConceptRelation, RelationSource, LearningPath, PathNode, ReasoningTransition, TeachingAsset,
    affected_assets, assess_attempt, learning_map, path_digest, publication_issues,
    example_learning_path, path_lesson, relation_issues,
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
                          content="조건이 바뀐 문제", question_role="TRANSFER",
                          choices=["가능", "불가능"], correct_answer="가능"),
            TeachingAsset(asset_id="answer", kind="ANSWER", depends_on=["second"],
                          condition_snapshot={"slope": "nonzero", "domain": "real"}, content="검토된 답"),
            TeachingAsset(asset_id="side-audio", kind="AUDIO", depends_on=["side-rule"],
                          condition_snapshot={"domain": "real"}, content="별도 설명"),
        ],
    )


class LearningPathTests(unittest.TestCase):
    def relation_path(self) -> LearningPath:
        path = reviewed_unit()
        path.assets = [asset for asset in path.assets if not asset.media_id]
        path.nodes[0].subject = "수학"
        path.nodes[0].curriculum_level = "중등"
        path.nodes[0].concept_ref = "math:rate"
        path.nodes[1].subject = "과학"
        path.relation_sources = [RelationSource(source_id="teacher-source", reference="교사가 대조한 교과 원자료", location="조건 비교")]
        return path

    def approved_relation(self, **changes) -> ConceptRelation:
        values = {"relation_id": "relation-1", "from_node": "basic", "to_node": "side",
                  "relation_type": "SIMILARITY", "source_refs": ["teacher-source"],
                  "evidence_ref": "두 표현의 대응을 교사가 비교", "status": "APPROVED"}
        values.update(changes)
        return ConceptRelation(**values)

    def test_legacy_path_json_preserves_existing_review_digest(self):
        legacy = reviewed_unit().model_dump()
        legacy.pop("concept_relations")
        legacy.pop("relation_sources")
        for node in legacy["nodes"]:
            for name in ("subject", "curriculum_level", "concept_ref"):
                node.pop(name)
        original_digest = hashlib.sha256(json.dumps(legacy, ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")).encode("utf-8")).hexdigest()
        loaded = LearningPath.model_validate(legacy)
        self.assertEqual(path_digest(loaded), original_digest)
        self.assertEqual(path_digest(LearningPath.model_validate_json(loaded.model_dump_json())), original_digest)
        self.assertEqual(publication_issues(loaded, teacher_reviewed_digest=original_digest), [])

    def test_similarity_has_no_prerequisite_direction_or_gate(self):
        path = self.relation_path()
        # There is no side -> goal prerequisite/transition in this unit.
        path.concept_relations = [self.approved_relation(from_node="side", to_node="goal")]
        self.assertEqual(publication_issues(path, teacher_reviewed_digest=path_digest(path)), [])
        self.assertEqual(path.nodes[2].prerequisite_ids, ["middle"])
        self.assertIn("유사: 독립된 보충 ↔ 목표 문제", path_lesson(path)[0]["text"])
        # Symmetric duplicate is detected even when supplied in reverse order.
        path.concept_relations.append(self.approved_relation(relation_id="reverse", from_node="goal", to_node="side"))
        self.assertIn("DUPLICATE_RELATION_PAIR:reverse", publication_issues(path, teacher_reviewed_digest=path_digest(path)))

    def test_approved_prerequisite_must_match_declared_direction_and_transition(self):
        path = self.relation_path()
        path.concept_relations = [self.approved_relation(relation_type="PREREQUISITE", to_node="middle")]
        self.assertEqual(publication_issues(path, teacher_reviewed_digest=path_digest(path)), [])
        path.concept_relations[0].from_node, path.concept_relations[0].to_node = "middle", "basic"
        self.assertIn("UNRESOLVED_RELATION_PREREQUISITE:relation-1",
                      publication_issues(path, teacher_reviewed_digest=path_digest(path)))

    def test_unknown_source_missing_node_and_self_relation_hold_approved_output(self):
        for changes, expected in (
            ({"source_refs": ["not-registered"]}, "UNKNOWN_RELATION_SOURCE:relation-1"),
            ({"to_node": "missing"}, "INVALID_RELATION_ENDPOINT:relation-1"),
            ({"to_node": "basic"}, "INVALID_RELATION_ENDPOINT:relation-1"),
            ({"evidence_ref": " "}, "RELATION_EVIDENCE_REQUIRED:relation-1"),
        ):
            with self.subTest(changes=changes):
                path = self.relation_path()
                path.concept_relations = [self.approved_relation(**changes)]
                self.assertIn(expected, publication_issues(path, teacher_reviewed_digest=path_digest(path)))

    def test_inferred_candidate_is_preview_only_and_cannot_change_active_path(self):
        path = self.relation_path()
        path.concept_relations = [self.approved_relation(relation_id="candidate-only", from_node="goal",
                   to_node="basic", relation_type="PREREQUISITE", source_refs=[], evidence_ref="",
                   origin="AI_PROPOSED", status="CANDIDATE")]
        self.assertEqual(publication_issues(path, teacher_reviewed_digest=path_digest(path)), [])
        self.assertTrue(relation_issues(path))  # Draft warnings remain visible.
        self.assertEqual(learning_map(path)["concept_relations"][0]["status"], "CANDIDATE")
        self.assertNotIn("concept_relations", path_lesson(path)[0])
        self.assertNotIn("candidate-only", json.dumps(path_lesson(path)))

    def test_populated_metadata_and_relation_approval_are_bound_to_exact_review(self):
        path = self.relation_path()
        path.concept_relations = [self.approved_relation()]
        reviewed = path_digest(path)
        path.nodes[0].subject = "다른 과목"
        self.assertIn("TEACHER_REVIEW_REQUIRED", publication_issues(path, teacher_reviewed_digest=reviewed))
        path.nodes[0].subject = "수학"
        path.concept_relations[0].status = "CANDIDATE"
        self.assertIn("TEACHER_REVIEW_REQUIRED", publication_issues(path, teacher_reviewed_digest=reviewed))

    def test_student_relation_projection_excludes_teacher_sources_and_notes(self):
        path = self.relation_path()
        path.concept_relations = [self.approved_relation(evidence_ref="teacher-only-note-secret")]
        projected = path_lesson(path)[0]["concept_relations"]
        self.assertEqual(set(projected[0]), {"relation_id", "from_node", "to_node", "relation_type"})
        serialized = json.dumps(path_lesson(path))
        self.assertNotIn("teacher-only-note-secret", serialized)
        self.assertNotIn("teacher-source", serialized)

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

    def test_example_projects_answerable_transfer_and_verified_media_mime(self):
        example = example_learning_path()
        self.assertEqual(publication_issues(example, teacher_reviewed_digest=path_digest(example)), [])
        transfer = next(s for s in path_lesson(example) if s["segment_id"] == "path-asset:transfer")
        self.assertEqual(transfer["choices"], ["가능", "불가능"])
        self.assertEqual(transfer["correct_answer"], "가능")
        self.assertTrue(transfer["independent_followup"])
        path = reviewed_unit()
        media = path_lesson(path, media_urls={"video": "/video", "voice": "/voice"},
                            media_types={"video": "video/mp4", "voice": "audio/mpeg"},
                            media_hashes={"video": "0" * 64, "voice": "1" * 64})
        self.assertEqual(next(s for s in media if s["segment_id"] == "path-asset:voice")["media_type"], "audio/mpeg")
        self.assertEqual(next(s for s in media if s["segment_id"] == "path-asset:video")["media_type"], "video/mp4")
        path.assets.append(TeachingAsset(asset_id="picture", kind="IMAGE", depends_on=["first"],
            condition_snapshot={"slope": "nonzero"}, media_id="approved-image"))
        media = path_lesson(path, media_urls={"video": "/video", "voice": "/voice", "picture": "/image"},
                            media_types={"video": "video/mp4", "voice": "audio/mpeg", "picture": "image/png"},
                            media_hashes={"video": "0" * 64, "voice": "1" * 64, "picture": "2" * 64})
        self.assertEqual(next(s for s in media if s["segment_id"] == "path-asset:picture")["media_type"], "image/png")


if __name__ == "__main__":
    unittest.main()
