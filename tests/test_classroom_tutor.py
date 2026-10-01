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

    def publish_lesson(self, session):
        base = f"/tutor/api/sessions/{session['session_id']}"
        params = {"teacher_token": session["teacher_token"]}
        draft = self.client.get(base, params=params).json()
        result = self.client.post(base + "/publish-lesson", params=params,
            json={"expected_digest": draft["lesson_digest"], "teacher_verified_content": True})
        self.assertEqual(result.status_code, 200, result.text)

    def test_shared_board_and_private_teacher_media(self):
        created = self.client.post("/classroom/api/sessions", headers=self.owner_headers,
            json={"title": "교사 시청각 수업"}).json()
        sid, token = created["session_id"], created["teacher_token"]
        root = f"/tutor/api/sessions/{sid}"
        params = {"teacher_token": token}
        stroke = {"stroke_id": "stroke-one", "points": [[0.1, 0.2], [0.8, 0.9]]}
        self.assertEqual(self.client.post(root + "/board-strokes", params={"teacher_token": "wrong"},
                         json=stroke).status_code, 403)
        self.assertEqual(self.client.post(root + "/board-strokes", params=params,
                         json=stroke).status_code, 200)
        self.assertEqual(self.client.post(root + "/board-strokes", params=params,
                         json=stroke).status_code, 200)
        listed = self.client.get(root + "/board-strokes", params=params).json()
        self.assertEqual(listed["strokes"], [stroke])
        self.assertEqual(self.client.get(root, params={"join_token": created["join_token"]}).status_code, 423)
        self.publish_lesson(created)
        self.assertEqual(self.client.post(root + "/command", params=params,
                         json={"action": "SET_BOARD_MODE", "board_mode": "WHITEBOARD"}).status_code, 200)
        self.assertIn("setupSharedBoard", self.client.get(created["teacher_url"]).text)
        self.assertIn("setupSharedBoard", self.client.get(created["board_url"]).text)

        import struct
        png = b"\x89PNG\r\n\x1a\n" + struct.pack(">II", 1, 1) + b"test"
        asset = self.client.post(root + "/media", params=params,
            json={"media_type": "image/png", "data_base64": base64.b64encode(png).decode()})
        self.assertEqual(asset.status_code, 200, asset.text)
        media_url = asset.json()["media_url"]
        self.assertEqual(self.client.get(media_url).content, png)
        self.assertEqual(self.client.get(media_url.split("?")[0], params={"asset_token": "wrong"}).status_code, 403)
        bad = self.client.post(root + "/media", params=params,
            json={"media_type": "video/mp4", "data_base64": base64.b64encode(png).decode()})
        self.assertEqual(bad.status_code, 400)
        draft = self.client.get(root, params=params).json()
        lesson = draft["lesson"]
        lesson.insert(-1, {"segment_id": "teacher-image", "kind": "DEMONSTRATE",
                            "title": "교사 그림", "text": "설명 그림",
                            "media_url": media_url, "media_type": "image/png"})
        self.assertEqual(self.client.put(root + "/lesson", params=params,
                         json={"lesson": lesson}).status_code, 200)
        self.assertEqual(self.client.get(root, params={"join_token": created["join_token"]}).status_code, 423)
        draft = self.client.get(root, params=params).json()
        self.assertEqual(draft["lesson"][-2]["media_type"], "image/png")
        self.client.post(root + "/publish-lesson", params=params,
            json={"expected_digest": draft["lesson_digest"], "teacher_verified_content": True})
        self.assertEqual(self.client.get(root, params={"join_token": created["join_token"]}).status_code, 200)
        self.assertEqual(self.client.delete(root + "/board-strokes", params=params).status_code, 200)
        self.assertEqual(self.client.get(root + "/board-strokes", params=params).json()["strokes"], [])

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
        self.assertEqual(self.client.get(f"/tutor/api/sessions/{session_id}",
                         params={"join_token": join_token}).status_code, 423)
        self.publish_lesson(payload)

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
        self.assertIn("구조화 AI 교수자료", page.text)
        self.assertIn("영상 주소", page.text)
        self.assertIn("한국어 음성합성", page.text)

    def test_teacher_product_is_separate_and_login_gated(self):
        unauthenticated = self.client.get("/classroom", follow_redirects=False)
        self.assertEqual(unauthenticated.status_code, 303)
        self.assertEqual(unauthenticated.headers["location"], "/classroom/login")

        page = self.client.get("/classroom", headers=self.owner_headers)
        self.assertEqual(page.status_code, 200)
        self.assertIn("EduRecover Classroom Board 교사용", page.text)
        self.assertIn("전자칠판 수업 만들기", page.text)
        self.assertNotIn("강화학습 연결 상태", page.text)

    def test_teacher_account_can_create_and_find_only_own_lessons(self):
        teacher_name = "teacher_" + os.urandom(4).hex()
        other_name = "teacher_" + os.urandom(4).hex()
        with TestClient(self.client.app, base_url="https://testserver") as teacher_client:
            for name in (teacher_name, other_name):
                result = teacher_client.post("/classroom/api/teachers", headers=self.owner_headers,
                    json={"username": name, "password": "classroom-long-secret-2026"})
                self.assertEqual(result.status_code, 200, result.text)
            login = teacher_client.post("/classroom/api/login",
                json={"username": teacher_name, "password": "classroom-long-secret-2026"})
            self.assertEqual(login.status_code, 200, login.text)
            self.assertEqual(teacher_client.get("/classroom").status_code, 200)
            created = teacher_client.post("/classroom/api/sessions",
                headers={"Origin": "https://testserver"}, json={"title": "교사 전용 수업"})
            self.assertEqual(created.status_code, 200, created.text)
            own = teacher_client.get("/classroom/api/my-sessions").json()["sessions"]
            self.assertTrue(any(x["teacher_url"] == created.json()["teacher_url"] for x in own))
            blocked = teacher_client.post("/classroom/api/sessions", json={"title": "출처 없는 요청"})
            self.assertEqual(blocked.status_code, 403)
            self.assertEqual(teacher_client.post("/classroom/api/teachers",
                headers={"Origin": "https://testserver"},
                json={"username": "should_fail", "password": "classroom-long-secret-2026"}).status_code, 401)
            teacher_client.post("/classroom/api/logout", headers={"Origin": "https://testserver"})
            teacher_client.post("/classroom/api/login",
                json={"username": other_name, "password": "classroom-long-secret-2026"})
            other = teacher_client.get("/classroom/api/my-sessions").json()["sessions"]
            self.assertFalse(any(x["teacher_url"] == created.json()["teacher_url"] for x in other))

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
        self.publish_lesson(created)
        state = self.client.get(
            f"/tutor/api/sessions/{created['session_id']}",
            params={"join_token": created["join_token"]},
        )
        self.assertEqual(state.status_code, 200, state.text)
        payload = state.json()
        self.assertNotIn("join_token", payload)
        self.assertEqual(len(payload["lesson"]), 1)
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
        self.publish_lesson(created)
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
        self.client.post(f"/tutor/api/sessions/{sid}/command", params={"teacher_token": tt},
                         json={"action": "GOTO", "segment_id": "transfer"})
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
        stopped = self.client.post(
            f"/tutor/api/sessions/{sid}/command",
            params={"teacher_token": tt},
            json={"action": "STOP"},
        )
        self.assertEqual(stopped.status_code, 200, stopped.text)
        pending_batch = stopped.json()["training_batch"]
        self.assertEqual(pending_batch["status"], "REVIEW_PENDING")
        self.assertFalse(pending_batch["gpu_started"])
        stopped_again = self.client.post(
            f"/tutor/api/sessions/{sid}/command",
            params={"teacher_token": tt},
            json={"action": "STOP"},
        )
        self.assertEqual(stopped_again.json()["training_batch"]["batch_id"], pending_batch["batch_id"])
        pre_reviewed = self.client.post(
            f"/tutor/api/sessions/{sid}/training-review",
            params={"teacher_token": tt},
            json={
                "response_id": first.json()["response_id"],
                "teacher_validated": True,
                "source_integrity_verified": True,
                "content_trust": "VERIFIED",
            },
        )
        self.assertEqual(pre_reviewed.status_code, 200, pre_reviewed.text)
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
        self.assertEqual(status["lesson_batch"]["batch_id"], pending_batch["batch_id"])
        self.assertEqual(status["lesson_batch"]["status"], "READY_FOR_EXPORT")
        self.assertEqual(status["lesson_batch"]["eligible_record_count"], 3)
        queue = self.client.get("/tutor/api/training-batches", headers=self.owner_headers)
        self.assertEqual(queue.status_code, 200, queue.text)
        queued = {row["batch_id"]: row for row in queue.json()["batches"]}
        self.assertEqual(queued[pending_batch["batch_id"]]["status"], "READY_FOR_EXPORT")
        self.assertFalse(queue.json()["automatic_gpu_rental"])


if __name__ == "__main__":
    unittest.main()
