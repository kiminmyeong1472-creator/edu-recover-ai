"""Classroom tutor session, token isolation, and audiovisual flow tests."""
import base64
import os
import tempfile
import unittest

os.environ.setdefault("EDURECOVER_OWNER_USER", "owner-test")
os.environ.setdefault("EDURECOVER_OWNER_PASSWORD", "password-test")
os.environ.setdefault("EDURECOVER_CLASSROOM_STATE_DIR", tempfile.mkdtemp(prefix="edurecover-tutor-"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.classroom_tutor import classroom_tutor_router


class ClassroomTutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(classroom_tutor_router)
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


if __name__ == "__main__":
    unittest.main()
