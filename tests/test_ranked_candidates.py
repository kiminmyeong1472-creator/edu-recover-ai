"""Teacher-reviewed exploration preserves low ranks without treating guesses as truth."""

import base64
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("EDURECOVER_OWNER_USER", "owner-test")
os.environ.setdefault("EDURECOVER_OWNER_PASSWORD", "password-test")
os.environ.setdefault("EDURECOVER_CLASSROOM_STATE_DIR", tempfile.mkdtemp(prefix="edu-ranked-init-"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.classroom_tutor as classroom
from app.services import ranked_candidate_generator as candidate_generator


def basic(username, password):
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": "Basic " + encoded}


def provider_environment(count=14):
    """A real adapter invocation with SDK and provider controls replaced locally."""
    class ProviderHeld(Exception):
        pass

    class GeneratorNotConfigured(Exception):
        pass

    output = json.dumps({"candidates": [{"text": "후보 설명 " + str(rank),
        "self_reported_rank": rank, "evidence_refs": ["supplied-source:line-1"],
        "concept_refs": ["plural_subject", "plural_verb_are"] if rank == 14 else ["past_tense_only"],
        "hypothesis_type": "AGREEMENT_CORRECTION" if rank == 14 else "TENSE_ONLY"}
        for rank in range(1, count + 1)]}, ensure_ascii=False)
    sdk = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(
        return_value=SimpleNamespace(output_text=output))), close=AsyncMock())
    generator = SimpleNamespace(
        settings=SimpleNamespace(openai_api_key=SimpleNamespace(get_secret_value=lambda: "test-only-key"),
                                 openai_model="test-only-model"),
        build_authoring_context=Mock(return_value={"source_scope": "authorized-test-source"}),
        AI_ACCESS_NOTICE="Test authorization. ", AsyncOpenAI=Mock(return_value=sdk),
        GeneratorNotConfigured=GeneratorNotConfigured)
    environment = SimpleNamespace(generator=generator,
        assert_provider_allowed=Mock(return_value=SimpleNamespace(control_epoch="test-control-epoch")),
        assert_provider_zdr=Mock(), held_errors=(ProviderHeld,))
    return environment, sdk


class RankedCandidateFlowTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="edu-ranked-flow-")
        self.old_state, self.old_db = classroom.STATE_DIR, classroom.DB_PATH
        classroom.STATE_DIR = Path(self.folder.name)
        classroom.DB_PATH = classroom.STATE_DIR / "classroom_tutor.sqlite3"
        app = FastAPI()
        app.include_router(classroom.classroom_tutor_router)
        app.include_router(classroom.teacher_classroom_router)
        self.client = TestClient(app, base_url="https://testserver")
        owner = basic("owner-test", "password-test")
        for username, password in (("teacher1", "teacher-password-123"),
                                   ("teacher2", "teacher-password-234")):
            response = self.client.post("/classroom/api/teachers", headers=owner,
                                        json={"username": username, "password": password})
            self.assertEqual(response.status_code, 200, response.text)
        self.teacher = basic("teacher1", "teacher-password-123")
        self.other_teacher = basic("teacher2", "teacher-password-234")
        response = self.client.post("/classroom/api/sessions", headers=self.teacher,
                                    json={"title": "낮은 순위 후보의 근거 검증"})
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()
        self.sid, self.token, self.join = (self.session[key] for key in
                                          ("session_id", "teacher_token", "join_token"))
        self.params = {"teacher_token": self.token}
        self.base = f"/classroom/api/sessions/{self.sid}/candidate-batches"
        self.policy_base = f"/classroom/api/sessions/{self.sid}/candidate-policy"
        # Start with a reviewed existing lesson. Candidate exploration must not
        # reveal an unreviewed alternative through the ordinary student API.
        current = self.teacher_session()
        released = self.client.post(f"/tutor/api/sessions/{self.sid}/publish-lesson",
            params=self.params, json={"expected_digest": current["lesson_digest"],
                                     "teacher_verified_content": True})
        self.assertEqual(released.status_code, 200, released.text)

    def tearDown(self):
        self.client.close()
        classroom.STATE_DIR, classroom.DB_PATH = self.old_state, self.old_db
        self.folder.cleanup()

    def teacher_session(self):
        response = self.client.get(f"/tutor/api/sessions/{self.sid}", params=self.params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def batch_payload(self, *, search=True):
        payload = {"context_ref": "grammar:agreement-tense-voice:v1", "candidates": [
            {"candidate_id": "rank-1", "rank": 1, "model_score": 0.93,
             "text": "There is two books on the table.", "evidence_refs": []},
            {"candidate_id": "rank-2", "rank": 2, "model_score": 0.50,
             "text": "There was two books on the table.", "evidence_refs": []},
            {"candidate_id": "rank-14", "rank": 14, "model_score": 0.06,
             "text": "There are two books on the table.", "evidence_refs": []},
        ]}
        if search:
            payload["search"] = {"max_rank": 14, "score_margin": 1.0,
                                  "include_candidate_ids": ["rank-14"]}
        return payload

    def create_batch(self, payload=None):
        response = self.client.post(self.base, params=self.params, headers=self.teacher,
                                    json=payload or self.batch_payload())
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result["batch_id"])
        self.assertEqual(len(result["digest"]), 64)
        return result

    def review(self, batch, candidate_id="rank-14", outcome="ACCEPTED", **changes):
        payload = {"expected_digest": batch["digest"], "candidate_id": candidate_id,
                   "outcome": outcome, "reason": "teacher-only-reason-secret: 복수 주어의 수일치",
                   "evidence_refs": ["teacher-only-evidence-secret: 원문과 문법 근거 대조"],
                   "teacher_verified": True, "ground_truth_status": "VERIFIED",
                   "reason_status": "TEACHER_CHECKED", "learning_use_approved": True,
                   "rights_status": "APPROVED"}
        payload.update(changes)
        return self.client.post(self.base + f"/{batch['batch_id']}/review", params=self.params,
                                 headers=self.teacher, json=payload)

    def publish(self, batch, candidate_id="rank-14", **changes):
        payload = {"expected_digest": batch["digest"], "candidate_id": candidate_id,
                   "expected_lesson_digest": self.teacher_session()["lesson_digest"],
                   "teacher_verified_content": True}
        payload.update(changes)
        return self.client.post(self.base + f"/{batch['batch_id']}/publish", params=self.params,
                                 headers=self.teacher, json=payload)

    def get_policy(self, context_ref=None):
        params = {**self.params, **({"context_ref": context_ref} if context_ref else {})}
        response = self.client.get(self.policy_base, params=params, headers=self.teacher)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_verified_rank14_can_be_published_and_trains_exploration_policy(self):
        batch = self.create_batch()
        snapshot = copy.deepcopy(batch["manifest"])
        self.assertIn("rank-14", str(snapshot))
        self.assertIsNone(snapshot["exploration_probability"])
        self.assertEqual(snapshot["selection_mode"], "DETERMINISTIC_ENUMERATION")
        unpublished = self.client.get(f"/tutor/api/sessions/{self.sid}",
                                      params={"join_token": self.join})
        self.assertEqual(unpublished.status_code, 200, unpublished.text)
        self.assertNotIn("There are two books", unpublished.text)
        rejected = self.review(batch, candidate_id="rank-1", outcome="REJECTED")
        self.assertEqual(rejected.status_code, 200, rejected.text)
        accepted = self.review(batch)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        # Review alone does not put the alternative on the electronic board.
        self.assertNotIn("There are two books", self.teacher_session()["lesson"][-1]["text"])
        published = self.publish(batch)
        self.assertEqual(published.status_code, 200, published.text)
        teacher_view = self.teacher_session()
        lesson = [step for step in teacher_view["lesson"]
                  if step["text"] == "There are two books on the table."]
        self.assertEqual(len(lesson), 1)
        self.assertEqual(published.json()["lesson_digest"], teacher_view["lesson_digest"])
        moved = self.client.post(f"/tutor/api/sessions/{self.sid}/command", params=self.params,
            json={"action": "GOTO", "segment_id": lesson[0]["segment_id"]})
        self.assertEqual(moved.status_code, 200, moved.text)
        student = self.client.get(f"/tutor/api/sessions/{self.sid}",
                                  params={"join_token": self.join})
        self.assertEqual(student.status_code, 200, student.text)
        self.assertIn("There are two books", student.text)
        self.assertNotIn("teacher-only-reason-secret", student.text)
        self.assertNotIn("teacher-only-evidence-secret", student.text)
        self.assertNotIn("There is two books", student.text)
        policy = self.get_policy()
        learned = self.client.post(self.policy_base + "/learn", params=self.params,
                                   headers=self.teacher,
                                   json={"expected_revision": policy["revision"]})
        self.assertEqual(learned.status_code, 200, learned.text)
        updated = self.get_policy()
        self.assertGreaterEqual(updated["effective_max_rank"], 14)
        self.assertFalse(updated["base_model_updated"])
        stale_learning = self.client.post(self.policy_base + "/learn", params=self.params,
            headers=self.teacher, json={"expected_revision": policy["revision"]})
        self.assertEqual(stale_learning.status_code, 409, stale_learning.text)
        next_batch = self.create_batch(self.batch_payload(search=False))
        self.assertIn("rank-14", str(next_batch["manifest"]))
        # Reward changes exploration; it must not rewrite the original ranking.
        saved = self.client.get(self.base, params=self.params, headers=self.teacher)
        self.assertEqual(saved.status_code, 200, saved.text)
        stored = saved.json()
        items = stored if isinstance(stored, list) else stored["batches"]
        original = next(item for item in items if item["batch_id"] == batch["batch_id"])
        self.assertEqual(original["manifest"], snapshot)
        self.assertEqual(original["digest"], batch["digest"])

    def test_unknown_evidence_is_held_and_never_rewarded_or_published(self):
        batch = self.create_batch()
        initial = self.get_policy()
        for updates in ({"evidence_refs": []}, {"evidence_refs": [" "]},
                        {"teacher_verified": False}, {"reason": " "},
                        {"ground_truth_status": "UNKNOWN"},
                        {"ground_truth_status": "CONFLICTING"}):
            with self.subTest(updates=updates):
                refused = self.review(batch, **updates)
                self.assertEqual(refused.status_code, 423, refused.text)
        unresolved = self.review(batch, outcome="UNRESOLVED", ground_truth_status="UNKNOWN",
                                 evidence_refs=[])
        self.assertEqual(unresolved.status_code, 200, unresolved.text)
        refused_publication = self.publish(batch)
        self.assertEqual(refused_publication.status_code, 423, refused_publication.text)
        learned = self.client.post(self.policy_base + "/learn", params=self.params,
            headers=self.teacher, json={"expected_revision": initial["revision"]})
        self.assertEqual(learned.status_code, 200, learned.text)
        unchanged = self.get_policy()
        self.assertEqual(unchanged["effective_max_rank"], initial["effective_max_rank"])
        self.assertEqual(unchanged["learned_reward_count"], 0)

    def test_digest_and_lesson_changes_block_stale_review_and_publication(self):
        batch = self.create_batch()
        stale_review = self.review(batch, expected_digest="0" * 64)
        self.assertEqual(stale_review.status_code, 409, stale_review.text)
        self.assertEqual(self.publish(batch).status_code, 423)
        accepted = self.review(batch)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        stale_manifest = self.publish(batch, expected_digest="0" * 64)
        self.assertEqual(stale_manifest.status_code, 409, stale_manifest.text)
        stale_lesson = self.publish(batch, expected_lesson_digest="0" * 64)
        self.assertEqual(stale_lesson.status_code, 409, stale_lesson.text)
        self.assertFalse(any("There are two books" in step["text"]
                             for step in self.teacher_session()["lesson"]))

    def test_review_retries_are_idempotent_and_do_not_multiply_reward(self):
        batch = self.create_batch()
        first = self.review(batch)
        self.assertEqual(first.status_code, 200, first.text)
        second = self.review(batch)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["receipt_id"], first.json()["receipt_id"])
        self.assertEqual(second.json()["status"], first.json()["status"])
        self.assertTrue(second.json()["idempotent"])
        self.assertFalse(second.json()["reward_created"])
        changed_judgment = self.review(batch, outcome="REJECTED")
        self.assertEqual(changed_judgment.status_code, 409, changed_judgment.text)
        before = self.get_policy()
        learned = self.client.post(self.policy_base + "/learn", params=self.params,
            headers=self.teacher, json={"expected_revision": before["revision"]})
        self.assertEqual(learned.status_code, 200, learned.text)
        after = self.get_policy()
        self.assertEqual(after["learned_reward_count"], 1)
        self.assertFalse(after["base_model_updated"])
        learned_again = self.client.post(self.policy_base + "/learn", params=self.params,
            headers=self.teacher, json={"expected_revision": after["revision"]})
        self.assertEqual(learned_again.status_code, 200, learned_again.text)
        self.assertEqual(self.get_policy()["learned_reward_count"], 1)

    def test_account_scope_is_required_even_when_session_token_is_known(self):
        batch = self.create_batch()
        policy = self.get_policy()
        routes = [("get", self.base, None), ("post", self.base, self.batch_payload()),
                  ("get", self.policy_base, None),
                  ("post", self.policy_base + "/learn", {"expected_revision": policy["revision"]}),
                  ("post", self.base + f"/{batch['batch_id']}/review",
                   {"expected_digest": batch["digest"], "candidate_id": "rank-14",
                    "outcome": "UNRESOLVED", "reason": "미확정", "evidence_refs": [],
                    "teacher_verified": True, "ground_truth_status": "UNKNOWN"}),
                  ("post", self.base + f"/{batch['batch_id']}/publish",
                   {"expected_digest": batch["digest"], "candidate_id": "rank-14",
                    "expected_lesson_digest": self.teacher_session()["lesson_digest"],
                    "teacher_verified_content": True})]
        for method, url, payload in routes:
            with self.subTest(method=method, url=url):
                kwargs = {"params": self.params}
                if payload is not None:
                    kwargs["json"] = payload
                unsigned = getattr(self.client, method)(url, **kwargs)
                self.assertEqual(unsigned.status_code, 401, unsigned.text)
                another = getattr(self.client, method)(url, headers=self.other_teacher, **kwargs)
                self.assertEqual(another.status_code, 403, another.text)
        wrong_token = self.client.get(self.base, params={"teacher_token": "incorrect"},
                                      headers=self.teacher)
        self.assertEqual(wrong_token.status_code, 403, wrong_token.text)

    def test_duplicate_rank_or_candidate_ids_do_not_make_a_usable_manifest(self):
        for field in ("candidate_id", "rank"):
            payload = self.batch_payload()
            payload["candidates"][1][field] = payload["candidates"][0][field]
            with self.subTest(field=field):
                response = self.client.post(self.base, params=self.params, headers=self.teacher,
                                            json=payload)
                self.assertEqual(response.status_code, 422, response.text)
        without_rank_source = self.batch_payload()
        without_rank_source["candidates"][0]["rank_basis"] = "NOT_APPLICABLE"
        refused = self.client.post(self.base, params=self.params, headers=self.teacher,
                                   json=without_rank_source)
        self.assertEqual(refused.status_code, 422, refused.text)

    def test_human_insight_keeps_null_model_rank_and_score(self):
        payload = self.batch_payload()
        payload["search"] = {"max_rank": 1, "score_margin": 0,
                              "include_candidate_ids": []}
        payload["candidates"].append({"candidate_id": "teacher-insight", "origin": "HUMAN_INSIGHT",
            "text": "문장의 실제 주어를 찾아 수일치를 다시 판단한다.",
            "rank": None, "model_score": None, "source_ref": "teacher-classroom-observation"})
        batch = self.create_batch(payload)
        insight = next(c for c in batch["manifest"]["candidates"]
                       if c["candidate_id"] == "teacher-insight")
        self.assertIsNone(insight["rank"])
        self.assertIsNone(insight["model_score"])
        accepted = self.review(batch, candidate_id="teacher-insight")
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertIsNone(accepted.json()["original_rank"])
        policy = self.get_policy()
        learned = self.client.post(self.policy_base + "/learn", params=self.params,
            headers=self.teacher, json={"expected_revision": policy["revision"]})
        self.assertEqual(learned.status_code, 200, learned.text)
        updated = self.get_policy()
        self.assertEqual(updated["rank_statistics"]["HUMAN_INSIGHT"]["accepted"], 1)
        self.assertEqual(updated["effective_max_rank"], policy["effective_max_rank"])
        fake_rank = copy.deepcopy(payload)
        fake_rank["candidates"][-1]["rank"] = 20
        invalid = self.client.post(self.base, params=self.params, headers=self.teacher, json=fake_rank)
        self.assertEqual(invalid.status_code, 422, invalid.text)

    def test_numeric_error_bound_is_fixed_before_exploration(self):
        payload = self.batch_payload()
        payload["verification"] = {"kind": "NUMERIC", "expected_numeric_value": 100,
            "numeric_absolute_tolerance": 1, "criteria_ref": "measurement-protocol-v1"}
        batch = self.create_batch(payload)
        self.assertEqual(batch["manifest"]["verification_criteria"]["numeric_absolute_tolerance"], 1)
        missing = self.review(batch)
        self.assertEqual(missing.status_code, 423, missing.text)
        outside = self.review(batch, measured_numeric_value=102)
        self.assertEqual(outside.status_code, 423, outside.text)
        falsely_rejected = self.review(batch, outcome="REJECTED", measured_numeric_value=100.7)
        self.assertEqual(falsely_rejected.status_code, 423, falsely_rejected.text)
        accepted = self.review(batch, measured_numeric_value=100.7)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["verification_criteria"],
                         batch["manifest"]["verification_criteria"])
        self.assertEqual(accepted.json()["measured_numeric_value"], 100.7)

    def test_correct_low_rank_does_not_expand_an_unrelated_context(self):
        batch = self.create_batch()
        self.assertEqual(self.review(batch).status_code, 200)
        first = self.get_policy()
        reinforced = self.client.post(self.policy_base + "/learn", params=self.params,
            headers=self.teacher, json={"expected_revision": first["revision"],
                                      "context_ref": "grammar:agreement-tense-voice:v1"})
        self.assertEqual(reinforced.status_code, 200, reinforced.text)
        other = self.batch_payload(search=False)
        other["context_ref"] = "math:derivative:v1"
        other_batch = self.create_batch(other)
        self.assertNotIn("rank-14", other_batch["manifest"]["selected_candidate_ids"])
        policy = self.get_policy()
        self.assertEqual(policy["context_ref"], "math:derivative:v1")
        self.assertEqual(policy["learned_reward_count"], 0)
        self.assertEqual(policy["effective_max_rank"], 3)
        isolated = self.client.post(self.policy_base + "/learn", params=self.params,
            headers=self.teacher, json={"expected_revision": policy["revision"],
                                      "context_ref": "math:derivative:v1"})
        self.assertEqual(isolated.status_code, 200, isolated.text)
        self.assertEqual(isolated.json()["new_reward_count"], 0)
        self.assertEqual(isolated.json()["learned_reward_count"], 0)

    def test_tampered_review_receipt_blocks_publication_and_policy_learning(self):
        batch = self.create_batch()
        accepted = self.review(batch)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        receipt_id = accepted.json()["receipt_id"]
        with classroom.db() as conn:
            saved = conn.execute("SELECT receipt_json FROM candidate_review_receipts WHERE receipt_id=?",
                                 (receipt_id,)).fetchone()
            receipt = json.loads(saved["receipt_json"])
            receipt["reason"] = "수정된 검증 사유"
            conn.execute("UPDATE candidate_review_receipts SET receipt_json=? WHERE receipt_id=?",
                         (json.dumps(receipt, ensure_ascii=False), receipt_id))
        blocked = self.publish(batch)
        self.assertEqual(blocked.status_code, 423, blocked.text)
        policy = self.get_policy()
        learning = self.client.post(self.policy_base + "/learn", params=self.params,
            headers=self.teacher, json={"expected_revision": policy["revision"]})
        self.assertEqual(learning.status_code, 423, learning.text)
        self.assertEqual(self.get_policy()["learned_reward_count"], 0)

    def generation_payload(self):
        return {"provider": "openai", "explicit_authorization": True,
                "context_ref": "grammar:agreement-tense-voice:v1",
                "source_text": "There are two books on the table.",
                "task_text": "수일치·시제·태를 대조하여 대안 설명 14개를 작성한다.",
                "student_response": "There is two books.", "count": 14,
                "subject": "영어", "grade": "1학년",
                "search": {"max_rank": 14, "score_margin": 1.0}}

    def test_unavailable_provider_does_not_invent_candidates_or_model_scores(self):
        endpoint = self.base + "/generate"
        payload = self.generation_payload()
        anonymous = self.client.post(endpoint, params=self.params, json=payload)
        self.assertEqual(anonymous.status_code, 401, anonymous.text)
        another = self.client.post(endpoint, params=self.params, headers=self.other_teacher, json=payload)
        self.assertEqual(another.status_code, 403, another.text)
        with patch.object(candidate_generator, "runtime_environment", side_effect=
                candidate_generator.CandidateGenerationError(503, "NO_RUNTIME_PROVIDER_MODULES", "test-runtime-missing")):
            unavailable = self.client.post(endpoint, params=self.params, headers=self.teacher, json=payload)
        self.assertEqual(unavailable.status_code, 503, unavailable.text)
        self.assertEqual(unavailable.json()["detail"]["code"], "NO_RUNTIME_PROVIDER_MODULES")
        self.assertTrue(unavailable.json()["detail"]["manual_input_available"])
        batches = self.client.get(self.base, params=self.params, headers=self.teacher)
        self.assertEqual(batches.status_code, 200, batches.text)
        self.assertEqual(batches.json()["batches"], [])

    def test_provider_adapter_keeps_self_reported_rank_separate_from_probability(self):
        environment, sdk = provider_environment()
        with patch.object(candidate_generator, "runtime_environment", return_value=environment):
            result = self.client.post(self.base + "/generate", params=self.params,
                                      headers=self.teacher, json=self.generation_payload())
        self.assertEqual(result.status_code, 200, result.text)
        batch = result.json()
        self.assertEqual(len(batch["candidates"]), 14)
        environment.assert_provider_allowed.assert_called_once_with("openai", "generate")
        environment.assert_provider_zdr.assert_called_once_with("openai")
        sdk.responses.create.assert_awaited_once()
        self.assertFalse(sdk.responses.create.await_args.kwargs["store"])
        sdk.close.assert_awaited_once()
        self.assertTrue(all(candidate["model_score"] is None for candidate in batch["candidates"]))
        self.assertTrue(all(candidate["rank_basis"] == "SELF_REPORTED_RANK"
                            for candidate in batch["candidates"]))
        correct = next(candidate for candidate in batch["candidates"] if candidate["rank"] == 14)
        self.assertEqual(correct["concept_refs"], ["plural_subject", "plural_verb_are"])
        self.assertEqual(correct["hypothesis_type"], "AGREEMENT_CORRECTION")
        reviewed = self.review(batch, candidate_id=correct["candidate_id"])
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertIsNone(reviewed.json()["original_model_score"])
        policy = self.get_policy()
        learned = self.client.post(self.policy_base + "/learn", params=self.params,
            headers=self.teacher, json={"expected_revision": policy["revision"]})
        self.assertEqual(learned.status_code, 200, learned.text)
        self.assertEqual(learned.json()["effective_max_rank"], 14)
        self.assertFalse(learned.json()["base_model_updated"])

    def test_unapproved_learning_use_does_not_make_a_training_reward(self):
        batch = self.create_batch()
        accepted = self.review(batch, learning_use_approved=False, rights_status="UNKNOWN",
                               reason_status="UNVERIFIED")
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertFalse(accepted.json()["reward_created"])
        published = self.publish(batch)
        self.assertEqual(published.status_code, 200, published.text)
        dataset = self.client.get(f"/classroom/api/sessions/{self.sid}/candidate-reward-dataset",
                                  params=self.params, headers=self.teacher)
        self.assertEqual(dataset.status_code, 200, dataset.text)
        self.assertEqual(dataset.json()["examples"], [])
        current = self.get_policy()
        learned = self.client.post(self.policy_base + "/learn", params=self.params,
            headers=self.teacher, json={"expected_revision": current["revision"]})
        self.assertEqual(learned.status_code, 200, learned.text)
        self.assertEqual(learned.json()["learned_reward_count"], 0)

    def test_source_change_holds_prior_candidates_and_removes_old_rewards(self):
        batch = self.create_batch()
        self.assertEqual(self.review(batch).status_code, 200)
        current = self.get_policy()
        learned = self.client.post(self.policy_base + "/learn", params=self.params,
            headers=self.teacher, json={"expected_revision": current["revision"]})
        self.assertEqual(learned.status_code, 200, learned.text)
        lesson = self.teacher_session()["lesson"]
        lesson[0]["text"] = "새 개정의 원자료: 수일치 문제 조건이 바뀌었습니다."
        changed = self.client.put(f"/tutor/api/sessions/{self.sid}/lesson", params=self.params,
                                  json={"lesson": lesson})
        self.assertEqual(changed.status_code, 200, changed.text)
        stale_review = self.review(batch)
        self.assertEqual(stale_review.status_code, 423, stale_review.text)
        stale_publication = self.publish(batch)
        self.assertEqual(stale_publication.status_code, 423, stale_publication.text)
        self.assertEqual(self.get_policy()["learned_reward_count"], 0)
        dataset = self.client.get(f"/classroom/api/sessions/{self.sid}/candidate-reward-dataset",
                                  params=self.params, headers=self.teacher)
        self.assertEqual(dataset.status_code, 200, dataset.text)
        self.assertEqual(dataset.json()["examples"], [])

    def test_invalidation_withdraws_output_and_rebuilds_policy_without_bad_reward(self):
        batch = self.create_batch()
        self.assertEqual(self.review(batch).status_code, 200)
        published = self.publish(batch)
        self.assertEqual(published.status_code, 200, published.text)
        current = self.get_policy()
        learned = self.client.post(self.policy_base + "/learn", params=self.params,
            headers=self.teacher, json={"expected_revision": current["revision"]})
        self.assertEqual(learned.status_code, 200, learned.text)
        self.assertEqual(self.get_policy()["learned_reward_count"], 1)
        invalidation = self.client.post(self.base + f"/{batch['batch_id']}/invalidate",
            params=self.params, headers=self.teacher,
            json={"expected_digest": batch["digest"], "candidate_id": "rank-14",
                  "reason": "새 근거로 기존 판정 오류를 확인했습니다.",
                  "evidence_refs": ["corrected-source:revision-2"], "teacher_verified": True})
        self.assertEqual(invalidation.status_code, 200, invalidation.text)
        self.assertEqual(invalidation.json()["withdrawn_segment_count"], 1)
        self.assertEqual(self.get_policy()["learned_reward_count"], 0)
        self.assertEqual(self.get_policy()["effective_max_rank"], 3)
        student = self.client.get(f"/tutor/api/sessions/{self.sid}", params={"join_token": self.join})
        self.assertEqual(student.status_code, 200, student.text)
        self.assertFalse(any("There are two books" in step["text"]
                             for step in self.teacher_session()["lesson"]))
        self.assertEqual(self.publish(batch).status_code, 423)
        dataset = self.client.get(f"/classroom/api/sessions/{self.sid}/candidate-reward-dataset",
                                  params=self.params, headers=self.teacher)
        self.assertEqual(dataset.status_code, 200, dataset.text)
        self.assertEqual(dataset.json()["examples"], [])

    def test_same_question_family_cannot_mix_training_and_evaluation(self):
        batch = self.create_batch()
        train = self.review(batch, candidate_id="rank-1", outcome="REJECTED",
                            split="TRAIN", split_group="same-question-family")
        self.assertEqual(train.status_code, 200, train.text)
        evaluate = self.review(batch, split="EVAL", split_group="same-question-family")
        self.assertEqual(evaluate.status_code, 423, evaluate.text)
        policy = self.get_policy()
        train_only = self.client.post(self.policy_base + "/learn", params=self.params,
            headers=self.teacher, json={"expected_revision": policy["revision"]})
        self.assertEqual(train_only.status_code, 200, train_only.text)
        self.assertEqual(self.get_policy()["learned_reward_count"], 1)
        dataset = self.client.get(f"/classroom/api/sessions/{self.sid}/candidate-reward-dataset",
                                  params=self.params, headers=self.teacher)
        self.assertEqual(dataset.status_code, 200, dataset.text)
        self.assertEqual(len(dataset.json()["examples"]), 1)
        self.assertEqual(dataset.json()["examples"][0]["split"], "TRAIN")

    def test_verified_reason_features_promote_new_rank20_without_rewriting_model_rank(self):
        payload = self.batch_payload()
        payload["candidates"][0].update({"concept_refs": ["past_tense_only"],
                                          "hypothesis_type": "TENSE_ONLY"})
        payload["candidates"][2].update({"concept_refs": ["plural_subject", "plural_verb_are"],
                                          "hypothesis_type": "AGREEMENT_CORRECTION"})
        batch = self.create_batch(payload)
        rejected = self.review(batch, candidate_id="rank-1", outcome="REJECTED")
        self.assertEqual(rejected.status_code, 200, rejected.text)
        accepted = self.review(batch)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        policy = self.get_policy()
        learned = self.client.post(self.policy_base + "/learn", params=self.params,
            headers=self.teacher, json={"expected_revision": policy["revision"]})
        self.assertEqual(learned.status_code, 200, learned.text)
        self.assertEqual(learned.json()["training_metrics"]["gradient_steps"], 0)
        self.assertFalse(learned.json()["training_metrics"]["neural_training_performed"])
        self.assertEqual(learned.json()["learning_kind"], "VERIFIED_RECEIPT_CASE_STATISTICS")
        alternative = {"context_ref": payload["context_ref"], "candidates": [
            {"candidate_id": "new-wrong-rank1", "rank": 1, "model_score": 0.93,
             "text": "There was two books yesterday.", "concept_refs": ["past_tense_only"],
             "hypothesis_type": "TENSE_ONLY"},
            {"candidate_id": "new-right-rank20", "rank": 20, "model_score": 0.01,
             "text": "There are three books now.", "concept_refs": ["plural_subject", "plural_verb_are"],
             "hypothesis_type": "AGREEMENT_CORRECTION"}],
            "search": {"max_rank": 1, "score_margin": 0, "include_candidate_ids": []}}
        next_batch = self.create_batch(alternative)
        manifest = next_batch["manifest"]
        self.assertIn("new-right-rank20", manifest["selected_candidate_ids"])
        priorities = {item["candidate_id"]: item["policy_priority"]
                      for item in manifest["learned_content_priorities"]}
        self.assertGreater(priorities["new-right-rank20"], priorities["new-wrong-rank1"])
        preserved = next(candidate for candidate in manifest["candidates"]
                         if candidate["candidate_id"] == "new-right-rank20")
        self.assertEqual(preserved["rank"], 20)
        self.assertEqual(preserved["model_score"], 0.01)
        student = self.client.get(f"/tutor/api/sessions/{self.sid}", params={"join_token": self.join})
        self.assertEqual(student.status_code, 200, student.text)
        self.assertNotIn("There are three books", student.text)
        # A teacher explicitly opting in can supply checked reasons to the
        # provider; they remain source-bound and do not imply base-model tuning.
        environment, sdk = provider_environment()
        request = self.generation_payload()
        request["use_verified_reason_context"] = True
        with patch.object(candidate_generator, "runtime_environment", return_value=environment):
            generated = self.client.post(self.base + "/generate", params=self.params,
                                          headers=self.teacher, json=request)
        self.assertEqual(generated.status_code, 200, generated.text)
        provider_input = json.loads(sdk.responses.create.await_args.kwargs["input"])
        memory = provider_input["teacher_approved_verified_reason_context"]
        self.assertTrue(memory)
        self.assertEqual(memory[0]["concept_refs"], ["plural_subject", "plural_verb_are"])
        self.assertIn("teacher-only-reason-secret", memory[0]["verified_reason"])
        self.assertFalse(self.get_policy()["base_model_updated"])

    def test_changed_reward_ledger_cannot_train_policy(self):
        batch = self.create_batch()
        self.assertEqual(self.review(batch).status_code, 200)
        with classroom.db() as conn:
            row = conn.execute("SELECT reward_id,outcome_json FROM candidate_rewards WHERE batch_id=?",
                               (batch["batch_id"],)).fetchone()
            changed = json.loads(row["outcome_json"])
            changed["candidate"]["text"] = "변조된 후보"
            conn.execute("UPDATE candidate_rewards SET outcome_json=? WHERE reward_id=?",
                         (json.dumps(changed, ensure_ascii=False), row["reward_id"]))
        policy = self.get_policy()
        learned = self.client.post(self.policy_base + "/learn", params=self.params,
            headers=self.teacher, json={"expected_revision": policy["revision"]})
        self.assertEqual(learned.status_code, 423, learned.text)
        self.assertEqual(self.get_policy()["learned_reward_count"], 0)
        dataset = self.client.get(f"/classroom/api/sessions/{self.sid}/candidate-reward-dataset",
                                  params=self.params, headers=self.teacher)
        self.assertEqual(dataset.status_code, 423, dataset.text)


    def test_answer_explanation_and_actual_cause_are_separate_predeclared_gates(self):
        payload = self.batch_payload()
        payload["verification"] = {"kind": "EVIDENCE", "required_dimensions": ["ANSWER", "EXPLANATION", "ACTUAL_CAUSE"]}
        batch = self.create_batch(payload)
        answer = {"status": "PASSED", "teacher_checked": True, "evidence_refs": ["answer-check:v1"]}
        explanation = {"status": "PASSED", "teacher_checked": True, "evidence_refs": ["explanation-check:v1"]}
        blocked = self.review(batch, answer_check=answer)
        self.assertEqual(blocked.status_code, 423, blocked.text)
        uncertain = self.review(batch, outcome="UNRESOLVED", answer_check=answer,
                                explanation_check={"status": "UNKNOWN"}, actual_cause_check={"status": "UNKNOWN"})
        self.assertEqual(uncertain.status_code, 200, uncertain.text)
        self.assertEqual(uncertain.json()["dimension_checks"]["ANSWER"]["status"], "PASSED")
        self.assertFalse(uncertain.json()["actual_cause_confirmed"])
        self.assertFalse(uncertain.json()["reward_created"])
        false_cause = self.review(batch, answer_check=answer, explanation_check=explanation,
            actual_cause_check={"status": "PASSED", "teacher_checked": True,
                               "evidence_refs": ["correct-answer-is-not-cause"], "method": "SOURCE_CHECK"})
        self.assertEqual(false_cause.status_code, 423, false_cause.text)
        cause = {"status": "PASSED", "teacher_checked": True, "evidence_refs": ["cause-comparison:v1"],
                 "method": "INDEPENDENT_RETEST", "observation_ref": "student-observation:v1",
                 "contrast_ref": "separate-unaided-attempt:v1"}
        confirmed = self.review(batch, answer_check=answer, explanation_check=explanation, actual_cause_check=cause)
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertTrue(confirmed.json()["actual_cause_confirmed"])
        self.assertTrue(confirmed.json()["reward_created"])
        self.assertEqual(confirmed.json()["original_rank"], 14)

    def test_evidence_revocation_withdraws_multiple_uses_and_preserves_unrelated_context(self):
        batches = []
        for context, text, proof in (("task:A", "approved-A", "shared-proof:v1"),
                                     ("task:B", "approved-B", "shared-proof:v1"),
                                     ("task:C", "approved-C", "other-proof:v1")):
            payload = self.batch_payload()
            payload["context_ref"] = context
            payload["candidates"][2]["text"] = text
            batch = self.create_batch(payload)
            reviewed = self.review(batch, evidence_refs=[proof])
            self.assertEqual(reviewed.status_code, 200, reviewed.text)
            current = self.get_policy(context)
            learned = self.client.post(self.policy_base + "/learn", params=self.params, headers=self.teacher,
                                      json={"expected_revision": current["revision"], "context_ref": context})
            self.assertEqual(learned.status_code, 200, learned.text)
            published = self.publish(batch)
            self.assertEqual(published.status_code, 200, published.text)
            batches.append(batch)
        preserved = self.get_policy("task:C")
        revoked = self.client.post(f"/classroom/api/sessions/{self.sid}/candidate-evidence/revoke",
            params=self.params, headers=self.teacher,
            json={"revoked_evidence_refs": ["shared-proof:v1"],
                  "correction_evidence_refs": ["corrected-proof:v2"],
                  "reason": "공유 근거의 오류를 확인했습니다.", "teacher_verified": True})
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(revoked.json()["withdrawn_segment_count"], 2)
        self.assertEqual(len(revoked.json()["revocation"]["affected_candidate_uses"]), 2)
        self.assertEqual(self.get_policy("task:A")["learned_reward_count"], 0)
        self.assertEqual(self.get_policy("task:B")["learned_reward_count"], 0)
        self.assertEqual(self.get_policy("task:C"), preserved)
        student = self.client.get(f"/tutor/api/sessions/{self.sid}", params={"join_token": self.join})
        self.assertEqual(student.status_code, 200, student.text)
        self.assertNotIn("approved-A", student.text)
        self.assertNotIn("approved-B", student.text)
        self.assertTrue(any(segment["text"] == "approved-C" for segment in self.teacher_session()["lesson"]))
        remaining_id = next(segment["segment_id"] for segment in self.teacher_session()["lesson"] if segment["text"] == "approved-C")
        goto = self.client.post(f"/tutor/api/sessions/{self.sid}/command", params=self.params,
                                json={"action": "GOTO", "segment_id": remaining_id})
        self.assertEqual(goto.status_code, 200, goto.text)
        student = self.client.get(f"/tutor/api/sessions/{self.sid}", params={"join_token": self.join})
        self.assertIn("approved-C", student.text)
        self.assertEqual(self.publish(batches[0]).status_code, 423)
        with classroom.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM candidate_rewards").fetchone()[0], 3)
        fresh = self.batch_payload()
        fresh["candidates"][2]["evidence_refs"] = ["shared-proof:v1"]
        rejected_reuse = self.client.post(self.base, params=self.params, headers=self.teacher, json=fresh)
        self.assertEqual(rejected_reuse.status_code, 423, rejected_reuse.text)
        dataset = self.client.get(f"/classroom/api/sessions/{self.sid}/candidate-reward-dataset",
                                  params=self.params, headers=self.teacher)
        self.assertEqual(len(dataset.json()["examples"]), 1)
        self.assertEqual(dataset.json()["examples"][0]["context_ref"], "task:C")

    def test_cause_context_requires_published_path_and_keeps_unknown_observations(self):
        from app.services.learning_path import ConceptRelation, RelationSource, example_learning_path
        map_url = f"/classroom/api/sessions/{self.sid}/candidate-cause-map"
        request = {"focus_node_ids": ["goal"], "observations": [{"condition_key": "point", "status": "UNKNOWN"}]}
        unapproved = self.client.post(map_url, params=self.params, headers=self.teacher, json=request)
        self.assertEqual(unapproved.status_code, 423, unapproved.text)
        path = example_learning_path()
        path.relation_sources = [RelationSource(source_id="teacher-source", reference="teacher-approved-curriculum:v1")]
        path.concept_relations = [ConceptRelation(relation_id="similar-exception", from_node="basic", to_node="goal",
            relation_type="SIMILARITY", source_refs=["teacher-source"], evidence_ref="similarity-check:v1", status="APPROVED")]
        path_url = f"/classroom/api/sessions/{self.sid}/learning-path"
        draft = self.client.put(path_url, params=self.params, headers=self.teacher, json=path.model_dump())
        self.assertEqual(draft.status_code, 200, draft.text)
        published = self.client.post(path_url + "/publish", params=self.params, headers=self.teacher,
            json={"revision_id": path.revision_id, "expected_digest": draft.json()["digest"], "teacher_verified_content": True})
        self.assertEqual(published.status_code, 200, published.text)
        before = self.client.get(path_url, params=self.params, headers=self.teacher).json()
        mapped = self.client.post(map_url, params=self.params, headers=self.teacher, json=request)
        self.assertEqual(mapped.status_code, 200, mapped.text)
        mapped = mapped.json()
        self.assertEqual(mapped["prerequisite_node_ids"], ["basic", "compare"])
        self.assertEqual(mapped["condition_observations"][1]["status"], "UNKNOWN")
        self.assertFalse(mapped["unknown_propagated_as_unmet"])
        self.assertFalse(mapped["graph_parameters_trained"])
        self.assertFalse(mapped["causal_truth_inferred"])
        payload = self.batch_payload()
        payload["cause_map_request"] = request
        batch = self.create_batch(payload)
        self.assertEqual(batch["manifest"]["approved_cause_context"], mapped)
        after = self.client.get(path_url, params=self.params, headers=self.teacher).json()
        self.assertEqual(after, before)
        environment, sdk = provider_environment()
        generation = self.generation_payload()
        generation["cause_map_request"] = request
        with patch.object(candidate_generator, "runtime_environment", return_value=environment):
            generated = self.client.post(self.base + "/generate", params=self.params,
                                          headers=self.teacher, json=generation)
        self.assertEqual(generated.status_code, 200, generated.text)
        provider_input = json.loads(sdk.responses.create.await_args.kwargs["input"])
        self.assertEqual(provider_input["teacher_approved_fixed_graph_context"], mapped)
        self.assertEqual(generated.json()["manifest"]["approved_cause_context"], mapped)
        self.assertEqual(self.client.post(map_url, params=self.params, headers=self.other_teacher,
                                          json=request).status_code, 403)


    def test_answer_purpose_preserves_verified_answer_when_actual_cause_is_unknown(self):
        batch = self.create_batch()
        checked = self.review(batch, actual_cause_check={"status": "UNKNOWN"})
        self.assertEqual(checked.status_code, 200, checked.text)
        self.assertTrue(checked.json()["reward_created"])
        self.assertFalse(checked.json()["actual_cause_confirmed"])
        self.assertEqual(checked.json()["verification_criteria"]["required_dimensions"], ["ANSWER"])
        self.assertEqual(checked.json()["dimension_checks"]["ACTUAL_CAUSE"]["status"], "UNKNOWN")

    def test_numeric_answer_pass_does_not_override_failed_explanation(self):
        payload = self.batch_payload()
        payload["verification"] = {"kind": "NUMERIC", "expected_numeric_value": 10,
            "numeric_absolute_tolerance": 0, "required_dimensions": ["ANSWER", "EXPLANATION"]}
        batch = self.create_batch(payload)
        reviewed = self.review(batch, outcome="REJECTED", measured_numeric_value=10,
            answer_check={"status": "PASSED", "teacher_checked": True, "evidence_refs": ["calculated:10"]},
            explanation_check={"status": "FAILED", "teacher_checked": True, "evidence_refs": ["explanation-counterexample:v1"]})
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertEqual(reviewed.json()["dimension_checks"]["ANSWER"]["status"], "PASSED")
        self.assertEqual(reviewed.json()["status"], "REJECTED")
        self.assertTrue(reviewed.json()["reward_created"])

    def _make_generated_descendant_chain(self):
        parent = self.create_batch()
        self.assertEqual(self.review(parent, evidence_refs=["root-proof:v1"]).status_code, 200)
        children = []
        for _ in range(2):
            current = self.get_policy()
            learned = self.client.post(self.policy_base + "/learn", params=self.params, headers=self.teacher,
                                      json={"expected_revision": current["revision"]})
            self.assertEqual(learned.status_code, 200, learned.text)
            environment, sdk = provider_environment()
            request = self.generation_payload()
            request["use_verified_reason_context"] = True
            with patch.object(candidate_generator, "runtime_environment", return_value=environment):
                generated = self.client.post(self.base + "/generate", params=self.params, headers=self.teacher, json=request)
            self.assertEqual(generated.status_code, 200, generated.text)
            child = generated.json()
            candidate_id = child["candidates"][-1]["candidate_id"]
            self.assertEqual(self.review(child, candidate_id=candidate_id, evidence_refs=["child-own-check:v1"]).status_code, 200)
            self.assertEqual(self.publish(child, candidate_id=candidate_id).status_code, 200)
            dependencies = child["manifest"]["generation_receipt"]["verified_reason_dependencies"]
            self.assertTrue(dependencies)
            self.assertTrue(any("root-proof:v1" in dependency["evidence_refs"] for dependency in dependencies))
            children.append((child, candidate_id))
        current = self.get_policy()
        self.assertEqual(self.client.post(self.policy_base + "/learn", params=self.params, headers=self.teacher,
                                          json={"expected_revision": current["revision"]}).status_code, 200)
        return parent, children

    def test_direct_parent_withdrawal_reaches_consumed_reason_descendants(self):
        parent, children = self._make_generated_descendant_chain()
        withdrawn = self.client.post(self.base + f"/{parent['batch_id']}/invalidate", params=self.params, headers=self.teacher,
            json={"expected_digest": parent["digest"], "candidate_id": "rank-14", "reason": "parent-check-corrected",
                  "evidence_refs": ["root-proof:v2"], "teacher_verified": True})
        self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
        self.assertEqual(withdrawn.json()["withdrawn_segment_count"], 2)
        self.assertEqual(withdrawn.json()["affected_candidate_count"], 29)
        self.assertEqual(self.get_policy()["learned_reward_count"], 0)
        self.assertEqual(self.get_policy()["accepted_reason_memory"], [])
        for child, candidate_id in children:
            self.assertEqual(self.publish(child, candidate_id=candidate_id).status_code, 423)
        historical = self.client.get(self.base, params=self.params, headers=self.teacher)
        self.assertEqual(historical.status_code, 200, historical.text)
        self.assertFalse(any(segment["segment_id"].startswith("candidate-") for segment in self.teacher_session()["lesson"]))

    def test_evidence_withdrawal_reaches_transitive_reason_memory_consumers(self):
        parent, children = self._make_generated_descendant_chain()
        withdrawn = self.client.post(f"/classroom/api/sessions/{self.sid}/candidate-evidence/revoke", params=self.params,
            headers=self.teacher, json={"revoked_evidence_refs": ["root-proof:v1"],
                "correction_evidence_refs": ["root-proof:v2"], "reason": "leaf-evidence-corrected", "teacher_verified": True})
        self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
        self.assertEqual(withdrawn.json()["withdrawn_segment_count"], 2)
        self.assertEqual(len(withdrawn.json()["revocation"]["affected_candidate_uses"]), 29)
        self.assertEqual(self.get_policy()["learned_reward_count"], 0)
        self.assertEqual(self.get_policy()["accepted_reason_memory"], [])

    def test_reset_responses_withdraws_response_bound_candidates_but_keeps_audit_readable(self):
        response = self.client.post(f"/tutor/api/sessions/{self.sid}/respond", json={
            "join_token": self.join, "student_alias": "ANON-1", "segment_id": "seg-1",
            "answer": "There is two books.", "reason_text": "student-test-reason"})
        self.assertEqual(response.status_code, 200, response.text)
        environment, sdk = provider_environment()
        request = self.generation_payload()
        request["response_id"] = response.json()["response_id"]
        with patch.object(candidate_generator, "runtime_environment", return_value=environment):
            generated = self.client.post(self.base + "/generate", params=self.params, headers=self.teacher, json=request)
        self.assertEqual(generated.status_code, 200, generated.text)
        batch = generated.json()
        candidate_id = batch["candidates"][-1]["candidate_id"]
        self.assertEqual(self.review(batch, candidate_id=candidate_id).status_code, 200)
        self.assertEqual(self.publish(batch, candidate_id=candidate_id).status_code, 200)
        reset = self.client.post(f"/tutor/api/sessions/{self.sid}/command", params=self.params, json={"action": "RESET_RESPONSES"})
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertFalse(any(segment["segment_id"].startswith("candidate-") for segment in self.teacher_session()["lesson"]))
        historical = self.client.get(self.base, params=self.params, headers=self.teacher)
        self.assertEqual(historical.status_code, 200, historical.text)
        manual = self.client.post(self.base + f"/{batch['batch_id']}/invalidate", params=self.params, headers=self.teacher,
            json={"expected_digest": batch["digest"], "candidate_id": candidate_id, "reason": "audit-confirmation",
                  "evidence_refs": ["teacher-reset-confirmation:v1"], "teacher_verified": True})
        self.assertEqual(manual.status_code, 200, manual.text)
        self.assertEqual(self.publish(batch, candidate_id=candidate_id).status_code, 423)
        self.assertEqual(self.get_policy()["learned_reward_count"], 0)


if __name__ == "__main__":
    unittest.main()
