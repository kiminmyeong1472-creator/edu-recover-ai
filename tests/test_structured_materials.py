"""Condition changes, withheld publication, and independent assessment boundaries."""

import base64
import hashlib
import json
import os
import tempfile
import unittest

os.environ.setdefault("EDURECOVER_OWNER_USER", "owner-test")
os.environ.setdefault("EDURECOVER_OWNER_PASSWORD", "password-test")
os.environ.setdefault("EDURECOVER_CLASSROOM_STATE_DIR", tempfile.mkdtemp(prefix="materials-test-"))
os.environ.setdefault("EDURECOVER_TRAINING_PSEUDONYM_SALT", "test-only-training-pseudonym-salt")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.classroom_tutor import (
    classroom_tutor_router, EduRecoverQuestionBundle, build_lesson_from_bundle, db,
)
from app.services.structured_materials import (
    example_bundle, publication_issues, affected_assets, bundle_lesson,
    issue_teacher_review_receipts, scenario_manifest_digest, bundle_digest,
    Representation, SceneVisual,
)


def reviewed_example():
    return issue_teacher_review_receipts(example_bundle())


class StructuredMaterialTests(unittest.TestCase):
    def test_absent_visual_preserves_legacy_serialization_and_review_receipts(self):
        draft = example_bundle()
        prior_keys = {"asset_id", "kind", "scenario_id", "condition_revision_id",
                      "generation_id", "depends_on", "content", "choices",
                      "media_asset_id", "media_sha256", "media_type"}
        self.assertTrue(all(set(asset) == prior_keys for asset in draft.model_dump()["representations"]))
        self.assertNotIn("visual", draft.representations[0].model_dump())
        self.assertNotIn("visual", json.loads(draft.model_dump_json())["representations"][0])
        explicit_null = Representation.model_validate({**draft.representations[0].model_dump(), "visual": None})
        self.assertEqual(explicit_null.model_dump(), draft.representations[0].model_dump())
        # These fingerprints were captured before adding the optional field.
        self.assertEqual(bundle_digest(draft), "699f2fb323b780ee79eb821f6e490172f5d6c9c6520441181e5c87193cd1b20c")
        self.assertEqual(scenario_manifest_digest(draft, "linear"),
                         "6ee3711b7914ddb97ca27c975ee3582c48aa42508f663033b835bdf215637d2e")
        reviewed = issue_teacher_review_receipts(draft)
        self.assertEqual(bundle_digest(reviewed), "5c13b710b52e4b624f04a05bd70a68a8b10060fc00d9e432004a2beba686e7bb")
        lesson = bundle_lesson(reviewed)
        self.assertTrue(all("scene_visual" not in step for step in lesson))
        serialized = json.dumps(lesson, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(hashlib.sha256(serialized.encode()).hexdigest(),
                         "472dcebc5867980df3a20eed8a9309bbf890c26f08a8f9ef4982dfa0e4cd9cee")

    def test_scene_visual_kinds_round_trip_and_compile_as_plain_data(self):
        visuals = [
            {"kind": "DIAGRAM", "nodes": [{"id": "a", "label": "기초"}, {"id": "b", "label": "결론"}],
             "edges": [{"from_id": "a", "to_id": "b", "label": "조건을 확인"}]},
            {"kind": "TABLE", "columns": ["조건", "결과"], "rows": [["기준", "불성립"], ["변경", "성립"]]},
            {"kind": "STEPS", "steps": ["기초 개념 확인", "성립 조건 비교", "새 문제 적용"]},
        ]
        for visual in visuals:
            with self.subTest(kind=visual["kind"]):
                parsed = SceneVisual.model_validate(visual)
                self.assertEqual(parsed.model_dump(), visual)
                self.assertEqual(SceneVisual.model_validate_json(parsed.model_dump_json()), parsed)
                draft = example_bundle()
                scene = draft.representations[0]
                text = scene.content
                scene.visual = parsed
                self.assertNotEqual(bundle_digest(draft), bundle_digest(example_bundle()))
                reviewed = issue_teacher_review_receipts(draft)
                step = next(x for x in bundle_lesson(reviewed) if x["segment_id"] == scene.asset_id)
                self.assertEqual(step["scene_visual"], visual)
                self.assertEqual(step["text"], text)

    def test_visual_rejects_markup_fields_mixed_kinds_and_unknown_asset_kind(self):
        bad_visuals = [
            {"kind": "HTML", "html": "<b>test</b>"},
            {"kind": "STEPS", "steps": ["확인"], "html": "<script>run()</script>"},
            {"kind": "STEPS", "steps": ["확인"], "nodes": None},
            {"kind": "TABLE", "columns": ["A", "B"], "rows": [["1", "2"]], "steps": ["확인"]},
            {"kind": "DIAGRAM", "nodes": [{"id": "a", "label": "A", "url": "https://example.com"}], "edges": []},
            {"kind": "DIAGRAM", "nodes": [{"id": "a", "label": "A"}],
             "edges": [{"from_id": "a", "to_id": "a", "label": "확인", "svg": "<svg/>"}]},
        ]
        for visual in bad_visuals:
            with self.subTest(visual=visual), self.assertRaises(ValueError):
                SceneVisual.model_validate(visual)
        visual = {"kind": "STEPS", "steps": ["문자 데이터"]}
        for asset in example_bundle().representations:
            if asset.kind != "SCENE":
                with self.subTest(asset_kind=asset.kind), self.assertRaisesRegex(ValueError, "only supported for SCENE"):
                    Representation.model_validate({**asset.model_dump(), "visual": visual})
        # Tags may remain inert text for the renderer; no raw-markup field exists.
        label = '<img src=x onerror="run()"><script>run()</script>'
        self.assertEqual(SceneVisual(kind="STEPS", steps=[label]).model_dump()["steps"], [label])

    def test_visual_requires_bounded_fields_and_valid_diagram_references(self):
        invalid = [
            {"kind": "DIAGRAM", "nodes": [], "edges": []},
            {"kind": "DIAGRAM", "nodes": [{"id": "a", "label": "A"}]},
            {"kind": "DIAGRAM", "nodes": [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}], "edges": []},
            {"kind": "DIAGRAM", "nodes": [{"id": "a", "label": "A"}],
             "edges": [{"from_id": "a", "to_id": "missing", "label": "연결"}]},
            {"kind": "DIAGRAM", "nodes": [{"id": "a" * 101, "label": "A"}], "edges": []},
            {"kind": "DIAGRAM", "nodes": [{"id": "a", "label": "A" * 1001}], "edges": []},
            {"kind": "DIAGRAM", "nodes": [{"id": str(i), "label": "A"} for i in range(13)], "edges": []},
            {"kind": "DIAGRAM", "nodes": [{"id": "a", "label": "A"}],
             "edges": [{"from_id": "a", "to_id": "a", "label": "연결"}] * 21},
            {"kind": "TABLE", "columns": ["A"], "rows": [["1"]]},
            {"kind": "TABLE", "columns": ["A"] * 7, "rows": [["1"] * 7]},
            {"kind": "TABLE", "columns": ["A", "B"], "rows": []},
            {"kind": "TABLE", "columns": ["A", "B"], "rows": [["1", "2"]] * 13},
            {"kind": "TABLE", "columns": ["A", "B"], "rows": [["1", "2", "3"]]},
            {"kind": "TABLE", "columns": ["A", "B"], "rows": [["1" * 3001, "2"]]},
            {"kind": "STEPS", "steps": []},
            {"kind": "STEPS", "steps": ["확인"] * 11},
            {"kind": "STEPS", "steps": ["확인" * 1501]},
        ]
        for visual in invalid:
            with self.subTest(visual_kind=visual["kind"]), self.assertRaises(ValueError):
                SceneVisual.model_validate(visual)

    def test_visual_changes_invalidate_review_and_report_affected_assets(self):
        draft = example_bundle()
        draft.representations[0].visual = SceneVisual(kind="STEPS", steps=["검토된 단계"])
        previous = issue_teacher_review_receipts(draft)
        updated = previous.model_copy(deep=True)
        updated.representations[0].visual.steps[0] = "수정된 단계"
        self.assertNotEqual(bundle_digest(previous), bundle_digest(updated))
        self.assertIn("STALE_VALIDATION_MANIFEST:linear:l0", publication_issues(updated))
        self.assertEqual(affected_assets(previous, updated), ["linear-scene"])
        with self.assertRaises(ValueError):
            bundle_lesson(updated)
        with self.assertRaisesRegex(ValueError, "Stale passed review"):
            issue_teacher_review_receipts(updated)

    def test_example_is_a_draft_and_false_counterexample_can_pass(self):
        self.assertTrue(publication_issues(example_bundle()))
        bundle = reviewed_example()
        self.assertEqual(publication_issues(bundle), [])
        self.assertTrue(any(check.proposition_result == "FALSE" for check in bundle.validations))
        self.assertNotIn("SOCRATIC", {step["kind"] for step in bundle_lesson(bundle)})
        self.assertEqual({step["concept_key"] for step in bundle_lesson(bundle)}, {bundle.independent_assessment.concept_key})

    def test_stale_audio_and_pending_logic_cannot_publish(self):
        bundle = reviewed_example()
        bundle.representations[1].condition_revision_id = "old-version"
        bundle.validations[0].status = "PENDING"
        issues = publication_issues(bundle)
        self.assertTrue(any(x.startswith("STALE_ASSET_CONDITION") for x in issues))
        self.assertTrue(any(x.startswith("LINK_NOT_VERIFIED") for x in issues))

    def test_receipts_bind_current_generation_and_every_representation(self):
        previous = reviewed_example()
        self.assertEqual(publication_issues(previous), [])
        scenario = previous.scenarios[0]
        digest = scenario_manifest_digest(previous, scenario.scenario_id)
        self.assertTrue(all(v.manifest_digest == digest for v in previous.validations
                            if v.scenario_id == scenario.scenario_id))
        stale = previous.model_copy(deep=True)
        stale.representations[0].content = "검토 이후 바뀐 장면"
        self.assertIn("STALE_VALIDATION_MANIFEST:linear:l0", publication_issues(stale))
        with self.assertRaises(ValueError):
            bundle_lesson(stale)
        stale = previous.model_copy(deep=True)
        stale.representations[1].generation_id = "old"
        self.assertIn("STALE_ASSET_GENERATION:linear-audio", publication_issues(stale))
        stale = previous.model_copy(deep=True)
        stale.scenarios[0].generation_id = "linear-g2"
        self.assertIn("STALE_VALIDATION_MANIFEST:linear:l0", publication_issues(stale))

    def test_optional_media_is_in_reviewed_manifest(self):
        draft = example_bundle()
        draft.representations.append(Representation(
            asset_id="linear-image", kind="IMAGE", scenario_id="linear",
            condition_revision_id="linear-c1", generation_id="linear-g1",
            depends_on=["l1"], content="검토된 함수 그림",
            media_asset_id="a" * 32, media_sha256="b" * 64, media_type="image/png"))
        reviewed = issue_teacher_review_receipts(draft)
        self.assertEqual(publication_issues(reviewed), [])
        lesson = bundle_lesson(reviewed)
        scene = next(x for x in lesson if x["segment_id"] == "linear-scene")
        image = next(x for x in lesson if x["segment_id"] == "linear-image")
        self.assertEqual(scene["manifest_digest"], image["manifest_digest"])
        self.assertEqual(image["media_asset_id"], "a" * 32)
        self.assertNotIn("media_url", image)
        reviewed.representations[-1].content = "substituted-image-ref"
        self.assertIn("STALE_VALIDATION_MANIFEST:linear:l0", publication_issues(reviewed))

    def test_media_reference_must_be_valid_and_has_no_untrusted_url(self):
        draft = example_bundle()
        asset = Representation(asset_id="linear-video", kind="VIDEO", scenario_id="linear",
            condition_revision_id="linear-c1", generation_id="linear-g1",
            depends_on=["l1"], content="검토 영상")
        draft.representations.append(asset)
        self.assertIn("MEDIA_REFERENCE_INCOMPLETE:linear-video", publication_issues(draft))
        with self.assertRaises(ValueError):
            issue_teacher_review_receipts(draft)
        asset.media_asset_id, asset.media_sha256, asset.media_type = "a"*32, "b"*64, "image/png"
        self.assertIn("MEDIA_REFERENCE_INCOMPLETE:linear-video", publication_issues(draft))

    def test_stamping_requires_reviewable_evidence_and_generation(self):
        draft = example_bundle()
        draft.validations[0].evidence = ""
        with self.assertRaisesRegex(ValueError, "LINK_NOT_VERIFIED"):
            issue_teacher_review_receipts(draft)
        draft = example_bundle()
        draft.scenarios[0].generation_id = ""
        with self.assertRaisesRegex(ValueError, "OUTPUT_GENERATION_INCOMPLETE"):
            issue_teacher_review_receipts(draft)

    def test_known_failed_or_undetermined_checks_cannot_be_attested_away(self):
        failed = example_bundle()
        failed.validations[0].status = "FAILED"
        with self.assertRaisesRegex(ValueError, "Failed or undetermined"):
            issue_teacher_review_receipts(failed)
        undetermined = example_bundle()
        undetermined.validations[0].proposition_result = "UNDETERMINED"
        with self.assertRaisesRegex(ValueError, "Failed or undetermined"):
            issue_teacher_review_receipts(undetermined)

    def test_stale_passed_receipt_requires_explicit_new_review(self):
        previous = reviewed_example()
        changed = previous.model_copy(deep=True)
        changed.representations[0].content = "정정한 설명"
        changed.scenarios[0].generation_id = "linear-g2"
        for asset in changed.representations:
            if asset.scenario_id == "linear":
                asset.generation_id = "linear-g2"
        with self.assertRaisesRegex(ValueError, "Stale passed review"):
            issue_teacher_review_receipts(changed)
        for check in changed.validations:
            check.status = "PENDING"
        renewed = issue_teacher_review_receipts(changed)
        self.assertEqual(publication_issues(renewed), [])
        self.assertNotEqual(
            scenario_manifest_digest(previous, "linear"),
            scenario_manifest_digest(renewed, "linear"))

    def test_declared_changed_conditions_must_match_actual_pair(self):
        bundle = reviewed_example()
        bundle.comparisons[0].changed_keys = ["함수"]
        self.assertIn("COMPARISON_CHANGED_KEYS_MISMATCH", publication_issues(bundle))

    def test_changed_upstream_rule_invalidates_downstream_assets(self):
        previous = reviewed_example()
        updated = previous.model_copy(deep=True)
        for asset in updated.representations:
            asset.depends_on = ["l2"]
        previous = updated.model_copy(deep=True)
        updated.links[0].rule = "수정한 전제 규칙"
        self.assertEqual(len(affected_assets(previous, updated)), 8)

    def test_repeated_practice_is_not_independent_assessment(self):
        bundle = reviewed_example()
        bundle.independent_assessment.question = next(x.content for x in bundle.representations if x.kind == "QUESTION")
        self.assertIn("INDEPENDENT_ASSESSMENT_REUSES_PRACTICE", publication_issues(bundle))
        lesson = build_lesson_from_bundle(EduRecoverQuestionBundle(
            question_id="q1", stem="기준 문제", choices={"A": "1", "B": "2"}, correct_answer="B. 2"))
        self.assertFalse(any(x["kind"] == "SOCRATIC" for x in lesson))
        self.assertFalse(lesson[-1]["independent_followup"])
        self.assertEqual(lesson[-1]["kind"], "PRACTICE")

    def test_socratic_method_can_be_enabled_without_becoming_mandatory(self):
        lesson = build_lesson_from_bundle(EduRecoverQuestionBundle(
            question_id="q1", stem="기준 문제", choices={"A": "1", "B": "2"}, correct_answer="B. 2",
            use_socratic=True, socratic_questions=["왜 그 조건이 필요한가요?"],
            retest_stem="다른 상황에 적용할 문제", retest_choices=["1", "2"], retest_correct_answer="2"))
        self.assertEqual(sum(x["kind"] == "SOCRATIC" for x in lesson), 1)
        self.assertTrue(lesson[-1]["independent_followup"])


class MaterialPublicationApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(classroom_tutor_router)
        cls.client = TestClient(app)
        cls.headers = {"Authorization": "Basic " + base64.b64encode(b"owner-test:password-test").decode()}

    def setUp(self):
        response = self.client.post("/tutor/api/sessions", headers=self.headers, json={"title": "자료 검증"})
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()
        self.base = "/tutor/api/sessions/" + self.session["session_id"]
        self.teacher = {"teacher_token": self.session["teacher_token"]}
        self.student = {"join_token": self.session["join_token"]}

    def save(self, bundle):
        result = self.client.put(self.base + "/structured-materials", params=self.teacher, json=bundle.model_dump())
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def publish(self, bundle, saved):
        return self.client.post(self.base + "/structured-materials/publish", params=self.teacher, json={
            "revision_id": bundle.revision_id, "expected_digest": saved["digest"], "teacher_verified_content": True})

    def test_pending_material_blocks_display_until_explicit_teacher_review(self):
        bundle = example_bundle()
        saved = self.save(bundle)
        self.assertEqual(self.client.get(self.base, params=self.student).status_code, 423)
        self.assertEqual(self.client.get(self.base, params={**self.teacher, "display_only": True}).status_code, 423)
        self.assertEqual(self.client.post(self.base + "/command", params=self.teacher, json={"action": "PLAY"}).status_code, 423)
        # A teacher's authenticated, exact-digest acknowledgement creates the
        # current manifest receipt; PENDING is not a permanent rejection.
        self.assertEqual(self.publish(bundle, saved).status_code, 200)
        self.assertEqual(self.client.get(self.base, params=self.student).status_code, 200)
        self.assertEqual(self.client.get(self.base, params=self.teacher).status_code, 200)

    def test_complete_release_hides_future_questions_and_edit_withdraws_it(self):
        bundle = reviewed_example()
        saved = self.save(bundle)
        self.assertEqual(self.publish(bundle, saved).status_code, 200)
        view = self.client.get(self.base, params=self.student).json()
        self.assertEqual(len(view["lesson"]), 1)
        self.assertEqual(view["lesson"][0]["kind"], "MAP")
        self.assertNotIn("x⁵", str(view))
        changed = bundle.model_copy(deep=True)
        changed.revision_id = "new-draft"
        changed.validations[0].status = "PENDING"
        self.save(changed)
        self.assertEqual(self.client.get(self.base, params=self.student).status_code, 423)
        self.assertEqual(self.publish(bundle, saved).status_code, 409)
        # Legacy endpoint cannot bypass the structured bundle release.
        self.assertEqual(self.client.put(self.base + "/lesson", params=self.teacher, json={"lesson": []}).status_code, 409)

    def test_revision_reuse_and_condition_revision_reuse_are_rejected(self):
        bundle = reviewed_example()
        self.save(bundle)
        bundle.target = "changed target"
        result = self.client.put(self.base + "/structured-materials", params=self.teacher, json=bundle.model_dump())
        self.assertEqual(result.status_code, 409)
        bundle.revision_id = "v2"
        bundle.scenarios[0].conditions["f′(0)"] = "7"
        result = self.client.put(self.base + "/structured-materials", params=self.teacher, json=bundle.model_dump())
        self.assertEqual(result.status_code, 409)

    def test_only_current_question_accepts_response_and_retry_is_assisted(self):
        bundle = reviewed_example()
        saved = self.save(bundle)
        self.assertEqual(self.publish(bundle, saved).status_code, 200)
        question = bundle.revision_id + ":independent"
        body = {"student_alias": "S1", "segment_id": question, "answer": "b=0", "material_revision_id": bundle.revision_id, **self.student}
        self.assertEqual(self.client.post(self.base + "/respond", json=body).status_code, 409)
        self.client.post(self.base + "/command", params=self.teacher, json={"action": "GOTO", "segment_id": question})
        stale = {**body, "material_revision_id": "earlier-bundle"}
        self.assertEqual(self.client.post(self.base + "/respond", json=stale).status_code, 409)
        first = self.client.post(self.base + "/respond", json=body)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertIsNone(first.json()["correct"])
        second = self.client.post(self.base + "/respond", json=body)
        self.assertEqual(second.status_code, 200)
        candidates = self.client.get(self.base + "/training-review-candidates", params=self.teacher).json()["candidates"]
        by_id = {x["response_id"]: x for x in candidates}
        self.assertTrue(by_id[first.json()["response_id"]]["independent_followup"])
        self.assertFalse(by_id[second.json()["response_id"]]["independent_followup"])

    def test_old_response_snapshot_survives_material_replacement(self):
        draft = self.client.get(self.base, params=self.teacher).json()
        self.client.post(self.base + "/publish-lesson", params=self.teacher,
            json={"expected_digest": draft["lesson_digest"], "teacher_verified_content": True})
        response = self.client.post(self.base + "/respond", json={
            "student_alias": "S1", "segment_id": "seg-1", "answer": "확인", **self.student}).json()
        self.client.put(self.base + "/lesson", params=self.teacher, json={"lesson": [{
            "segment_id": "seg-1", "kind": "EXPLAIN", "title": "changed", "correct_answer": "확인"}]})
        candidates = self.client.get(self.base + "/training-review-candidates", params=self.teacher).json()["candidates"]
        candidate = next(x for x in candidates if x["response_id"] == response["response_id"])
        self.assertIsNone(candidate["correct"])
        self.assertEqual(candidate["segment_title"], "기초에서 목표까지")
        self.assertEqual(self.client.get(self.base, params=self.student).status_code, 423)

    def test_legacy_future_and_stopped_responses_are_blocked(self):
        draft = self.client.get(self.base, params=self.teacher).json()
        review = {"expected_digest": draft["lesson_digest"], "teacher_verified_content": True}
        self.assertEqual(self.client.post(self.base + "/publish-lesson", params=self.teacher, json=review).status_code, 200)
        body = {"student_alias": "S1", "segment_id": "seg-4", "answer": "future", **self.student}
        self.assertEqual(self.client.post(self.base + "/respond", json=body).status_code, 409)
        self.client.post(self.base + "/command", params=self.teacher, json={"action": "STOP"})
        body["segment_id"] = "seg-1"
        self.assertEqual(self.client.post(self.base + "/respond", json=body).status_code, 423)

    def test_generated_lesson_requires_signed_current_independent_pass(self):
        from app.services.generation_provenance import (
            issue_origin_proof, verify_origin_proof, issue_verification_proof,
        )
        items = [{"question": "두 수 1과 2의 합은?", "choices": ["2", "3"], "answer": "B",
                  "passage": "", "explanation": "1+2=3입니다."}]
        origin_proof = issue_origin_proof("openai", "generation-test", items)
        claims = verify_origin_proof(origin_proof, problems=items)
        body = {"source_items": items, "origin_proof": origin_proof, "verification_proof": "PASS"}
        self.assertEqual(self.client.post("/tutor/api/sessions", headers=self.headers, json=body).status_code, 423)
        proof = issue_verification_proof("gemini", "verification-test", items, claims, "PASS")
        body["verification_proof"] = proof
        body["lesson"] = [{"segment_id": "forged", "kind": "EXPLAIN", "title": "wrong", "text": "wrong"}]
        created = self.client.post("/tutor/api/sessions", headers=self.headers, json=body)
        self.assertEqual(created.status_code, 200, created.text)
        result = created.json()
        base = f"/tutor/api/sessions/{result['session_id']}"
        params = {"teacher_token": result["teacher_token"]}
        draft = self.client.get(base, params=params).json()
        self.assertEqual(draft["state"]["source_kind"], "AI_GENERATED")
        self.assertTrue(draft["lesson"][0]["segment_id"].startswith("verified-"))
        self.assertEqual(self.client.get(base, params={"join_token": result["join_token"]}).status_code, 423)
        self.assertEqual(self.client.post(base + "/publish-lesson", params=params, json={
            "expected_digest": draft["lesson_digest"], "teacher_verified_content": True}).status_code, 200)
        self.assertEqual(self.client.put(base + "/lesson", params=params, json={"lesson": []}).status_code, 409)
        body["source_items"][0]["answer"] = "A"
        self.assertEqual(self.client.post("/tutor/api/sessions", headers=self.headers, json=body).status_code, 423)


if __name__ == "__main__":
    unittest.main()

