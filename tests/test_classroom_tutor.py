"""Classroom tutor session, token isolation, and audiovisual flow tests."""
import base64
import os
import tempfile
import unittest

os.environ.setdefault("EDURECOVER_OWNER_USER", "owner-test")
os.environ.setdefault("EDURECOVER_OWNER_PASSWORD", "password-test")
os.environ.setdefault("EDURECOVER_CLASSROOM_STATE_DIR", tempfile.mkdtemp(prefix="edurecover-tutor-"))
os.environ.setdefault("EDURECOVER_TRAINING_PSEUDONYM_SALT", "test-only-training-pseudonym-salt")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.classroom_tutor import classroom_tutor_router, teacher_classroom_router


class ClassroomTutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(classroom_tutor_router)
        app.include_router(teacher_classroom_router)
        cls.client = TestClient(app)
        token = base64.b64encode(b"owner-test:password-test").decode("ascii")
        cls.owner_headers = {"Authorization": f"Basic {token}"}

    def test_owner_can_create_and_run_token_scoped_lesson(self):
        lesson = [
            {
                "segment_id": "media",
                "kind": "VIDEO",
                "title": "짧은 설명",
                "text": "조건을 자기 말로 설명해 보세요.",
                "media_url": None,
                "pause_at_sec": 8,
            },
            {
                "segment_id": "question",
                "kind": "SOCRATIC",
                "title": "산파식 질문",
                "text": "문제에서 실제로 주어진 조건은 무엇인가요?",
                "choices": ["조건을 확인했다", "아직 헷갈린다"],
                "correct_answer": "조건을 확인했다",
                "branch_map": {"조건을 확인했다": "retest", "아직 헷갈린다": "media"},
            },
            {
                "segment_id": "retest",
                "kind": "RETEST",
                "title": "재시험",
                "text": "다시 답해 보세요.",
                "choices": ["A", "B"],
                "correct_answer": "B",
            },
        ]
        created = self.client.post(
            "/tutor/api/sessions",
            headers=self.owner_headers,
            json={"title": "통합 시험", "room_alias": "TEST", "lesson": lesson},
        )
        self.assertEqual(created.status_code, 200, created.text)
        payload = created.json()
        session_id = payload["session_id"]
        teacher_token = payload["teacher_token"]
        join_token = payload["join_token"]

        self.assertEqual(self.client.get(f"/tutor/api/sessions/{session_id}").status_code, 403)
        teacher_state = self.client.get(
            f"/tutor/api/sessions/{session_id}", params={"teacher_token": teacher_token}
        )
        self.assertEqual(teacher_state.status_code, 200)
        self.assertEqual(len(teacher_state.json()["lesson"]), 3)

        response = self.client.post(
            f"/tutor/api/sessions/{session_id}/respond",
            json={
                "student_alias": "S12",
                "segment_id": "media",
                "answer": "확인",
                "join_token": join_token,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        results = self.client.get(
            f"/tutor/api/sessions/{session_id}/results",
            params={"teacher_token": teacher_token},
        )
        self.assertEqual(results.status_code, 200)
        self.assertEqual(results.json()["counts"][0]["n"], 1)

    def test_tutor_home_is_owner_only_and_contains_media_controls(self):
        self.assertEqual(self.client.get("/tutor").status_code, 401)
        page = self.client.get("/tutor", headers=self.owner_headers)
        self.assertEqual(page.status_code, 200)
        self.assertIn("산파식 AI 튜터", page.text)
        self.assertIn("영상 주소", page.text)
        self.assertIn("한국어 음성합성", page.text)

    def test_teacher_product_is_separate_and_owner_gated(self):
        unauthenticated = self.client.get("/classroom", follow_redirects=False)
        self.assertEqual(unauthenticated.status_code, 303)
        self.assertEqual(unauthenticated.headers["location"], "/owner/login")

        page = self.client.get("/classroom", headers=self.owner_headers)
        self.assertEqual(page.status_code, 200)
        self.assertIn("EduRecover Classroom Board 교사용", page.text)
        self.assertIn("전자칠판 수업 만들기", page.text)
        self.assertNotIn("강화학습 연결 상태", page.text)

    def test_teacher_product_creates_product_scoped_links(self):
        created = self.client.post(
            "/classroom/api/sessions",
            headers=self.owner_headers,
            json={"title": "교사용 상품 시험", "room_alias": "T-01"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        payload = created.json()
        self.assertTrue(payload["board_url"].startswith("/classroom/board/"))
        self.assertTrue(payload["teacher_url"].startswith("/classroom/teacher/"))
        self.assertTrue(payload["join_url"].startswith("/classroom/join/"))

        board = self.client.get(payload["board_url"])
        self.assertEqual(board.status_code, 200)
        self.assertEqual(board.headers["referrer-policy"], "no-referrer")
        self.assertIn('PFX="/classroom"', board.text)

        teacher = self.client.get(payload["teacher_url"])
        self.assertEqual(teacher.status_code, 200)
        self.assertIn("교사 콘솔", teacher.text)
        self.assertNotIn("강화학습 연결 상태", teacher.text)

    def test_student_session_hides_answers_and_internal_routing(self):
        created = self.client.post(
            "/classroom/api/sessions",
            headers=self.owner_headers,
            json={"title": "학생 정보경계 시험", "room_alias": "SAFE"},
        ).json()
        state = self.client.get(
            f"/tutor/api/sessions/{created['session_id']}",
            params={"join_token": created["join_token"]},
        )
        self.assertEqual(state.status_code, 200, state.text)
        payload = state.json()
        self.assertNotIn("join_token", payload)
        for segment in payload["lesson"]:
            self.assertNotIn("correct_answer", segment)
            self.assertNotIn("branch_map", segment)
            self.assertNotIn("misconception_path", segment)
            self.assertNotIn("content_revision_id", segment)

    def test_tutor_responses_feed_fail_closed_training_eligibility(self):
        lesson = [
            {
                "segment_id": "pre",
                "kind": "EXPLAIN",
                "title": "최초응답",
                "text": "먼저 답해 보세요.",
                "choices": ["A", "B"],
                "correct_answer": "B",
                "concept_key": "TEST:CONCEPT",
                "content_revision_id": "test:problem:v1",
                "feedback_phase": "PRE_FEEDBACK",
            },
            {
                "segment_id": "transfer",
                "kind": "RETEST",
                "title": "독립 전이",
                "text": "다른 맥락에서 다시 답하세요.",
                "choices": ["A", "B"],
                "correct_answer": "B",
                "concept_key": "TEST:CONCEPT",
                "content_revision_id": "test:transfer:v1",
                "independent_followup": True,
                "tutor_action_content_revision": "test:hint:v1",
            },
        ]
        created = self.client.post(
            "/tutor/api/sessions",
            headers=self.owner_headers,
            json={"title": "학습자격 시험", "room_alias": "RL", "lesson": lesson},
        ).json()
        sid, tt, jt = created["session_id"], created["teacher_token"], created["join_token"]
        first = self.client.post(
            f"/tutor/api/sessions/{sid}/respond",
            json={
                "student_alias": "S1",
                "segment_id": "pre",
                "answer": "A",
                "reason_text": "조건을 전체에 적용했다.",
                "join_token": jt,
            },
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertIsNone(first.json()["correct"], "최초응답 정오를 학생에게 즉시 누출하면 안 됩니다.")
        transfer = self.client.post(
            f"/tutor/api/sessions/{sid}/respond",
            json={
                "student_alias": "S1",
                "segment_id": "transfer",
                "answer": "B",
                "reason_text": "같은 개념을 다른 조건에 적용했다.",
                "join_token": jt,
            },
        )
        self.assertEqual(transfer.status_code, 200, transfer.text)
        reviewed = self.client.post(
            f"/tutor/api/sessions/{sid}/training-review",
            params={"teacher_token": tt},
            json={
                "response_id": transfer.json()["response_id"],
                "teacher_validated": True,
                "source_integrity_verified": True,
                "content_trust": "VERIFIED",
                "hint_or_explanation_confirmed": True,
            },
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        status = reviewed.json()["training_status"]
        states = {row["learning_purpose"]: row["state"] for row in status["current_eligibility"]}
        self.assertEqual(states["MASTERY_MODEL"], "ELIGIBLE")
        self.assertEqual(states["ERROR_PATTERN_MODEL"], "ELIGIBLE")
        self.assertEqual(states["TUTOR_POLICY_MODEL"], "ELIGIBLE")
        self.assertEqual(states["REVIEW_TIMING_MODEL"], "HOLD")
        self.assertFalse(status["automatic_weight_update"])


if __name__ == "__main__":
    unittest.main()
