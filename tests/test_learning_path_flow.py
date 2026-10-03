"""Account scoped path editing, board projection and withdrawn media release."""

import base64
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("EDURECOVER_OWNER_USER", "owner-test")
os.environ.setdefault("EDURECOVER_OWNER_PASSWORD", "password-test")
os.environ.setdefault("EDURECOVER_CLASSROOM_STATE_DIR", tempfile.mkdtemp(prefix="edu-path-init-"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.classroom_tutor as classroom
from app.services.learning_path import TeachingAsset, example_learning_path


class LearningPathFlowTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="edu-path-flow-")
        self.old_state, self.old_db = classroom.STATE_DIR, classroom.DB_PATH
        classroom.STATE_DIR = Path(self.folder.name)
        classroom.DB_PATH = classroom.STATE_DIR / "classroom_tutor.sqlite3"
        app = FastAPI()
        app.include_router(classroom.classroom_tutor_router)
        app.include_router(classroom.teacher_classroom_router)
        self.client = TestClient(app, base_url="https://testserver")
        self.owner = {"Authorization": "Basic " + base64.b64encode(b"owner-test:password-test").decode()}
        created = self.client.post("/classroom/api/sessions", headers=self.owner,
                                   json={"title": "경로 수업"})
        self.assertEqual(created.status_code, 200, created.text)
        self.session = created.json()
        self.sid, self.token, self.join = (self.session[key] for key in
                                           ("session_id", "teacher_token", "join_token"))
        self.base = f"/classroom/api/sessions/{self.sid}/learning-path"
        self.params = {"teacher_token": self.token}

    def tearDown(self):
        self.client.close()
        classroom.STATE_DIR, classroom.DB_PATH = self.old_state, self.old_db
        self.folder.cleanup()

    def upload(self, data: bytes, content_type: str) -> tuple[str, str]:
        response = self.client.post(f"/tutor/api/sessions/{self.sid}/media", params=self.params,
                                    json={"media_type": content_type,
                                          "data_base64": base64.b64encode(data).decode()})
        self.assertEqual(response.status_code, 200, response.text)
        url = response.json()["media_url"]
        return urlsplit(url).path.rsplit("/", 1)[-1], parse_qs(urlsplit(url).query)["asset_token"][0]

    def test_save_publish_resume_and_revision_blocks_stale_response(self):
        path = example_learning_path().model_dump()
        self.assertEqual(self.client.get("/classroom/api/learning-path/example").status_code, 401)
        self.assertEqual(self.client.put(self.base, params=self.params, json=path).status_code, 401)
        self.assertEqual(self.client.get(self.base, params=self.params, headers=self.owner).status_code, 404)
        draft = self.client.put(self.base, params=self.params, headers=self.owner, json=path)
        self.assertEqual(draft.status_code, 200, draft.text)
        self.assertEqual(self.client.get(f"/tutor/api/sessions/{self.sid}",
                                         params={"join_token": self.join}).status_code, 423)
        resumed = self.client.get(self.base, params=self.params, headers=self.owner)
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(resumed.json()["path"], path)
        self.assertEqual(self.client.get(f"/classroom/learning-path/{self.sid}",
                                         params=self.params, headers=self.owner).status_code, 200)
        self.assertEqual(self.client.post(self.base + "/publish", params=self.params, headers=self.owner,
                         json={"revision_id": path["revision_id"], "expected_digest": "wrong",
                               "teacher_verified_content": True}).status_code, 409)
        published = self.client.post(self.base + "/publish", params=self.params, headers=self.owner,
                    json={"revision_id": path["revision_id"], "expected_digest": draft.json()["digest"],
                          "teacher_verified_content": True})
        self.assertEqual(published.status_code, 200, published.text)
        teacher_view = self.client.get(f"/tutor/api/sessions/{self.sid}", params=self.params).json()
        self.assertTrue(any(s["segment_id"] == "path-asset:transfer" for s in teacher_view["lesson"]))
        self.assertEqual(self.client.post(f"/tutor/api/sessions/{self.sid}/publish-lesson", params=self.params,
                         json={"expected_digest": teacher_view["lesson_digest"],
                               "teacher_verified_content": True}).status_code, 409)
        self.assertEqual(self.client.put(f"/tutor/api/sessions/{self.sid}/lesson", params=self.params,
                         json={"lesson": []}).status_code, 409)
        goto = self.client.post(f"/tutor/api/sessions/{self.sid}/command", params=self.params,
                                json={"action": "GOTO", "segment_id": "path-asset:transfer"})
        self.assertEqual(goto.status_code, 200, goto.text)
        student = self.client.get(f"/tutor/api/sessions/{self.sid}",
                                  params={"join_token": self.join})
        self.assertEqual(student.status_code, 200, student.text)
        self.assertNotIn("correct_answer", student.text)
        answer = {"student_alias": "S-1", "segment_id": "path-asset:transfer", "answer": "가능",
                  "join_token": self.join, "learning_path_revision_id": path["revision_id"]}
        self.assertEqual(self.client.post(f"/tutor/api/sessions/{self.sid}/respond",
                                          json={**answer, "learning_path_revision_id": "old"}).status_code, 409)
        result = self.client.post(f"/tutor/api/sessions/{self.sid}/respond", json=answer)
        self.assertEqual(result.status_code, 200, result.text)
        changed = example_learning_path().model_copy(deep=True)
        changed.revision_id = "path-new-condition"
        changed.conditions["point"] = "x=1"
        draft2 = self.client.put(self.base, params=self.params, headers=self.owner, json=changed.model_dump())
        self.assertEqual(draft2.status_code, 200, draft2.text)
        self.assertEqual(self.client.get(f"/tutor/api/sessions/{self.sid}",
                                         params={"join_token": self.join}).status_code, 423)
        self.assertEqual(self.client.post(f"/tutor/api/sessions/{self.sid}/respond", json=answer).status_code, 423)
        blocked = self.client.post(self.base + "/publish", params=self.params, headers=self.owner,
                    json={"revision_id": changed.revision_id, "expected_digest": draft2.json()["digest"],
                          "teacher_verified_content": True})
        self.assertEqual(blocked.status_code, 423)
        self.assertTrue(any("STALE_TRANSITION_VALIDATION" in issue for issue in blocked.json()["detail"]["issues"]))

    def test_account_owner_and_media_mime_are_checked_and_old_urls_hold(self):
        picture, picture_token = self.upload(b"\x89PNG\r\n\x1a\n" + b"\0" * 20, "image/png")
        sound, sound_token = self.upload(b"ID3" + b"\0" * 20, "audio/mpeg")
        video, video_token = self.upload(b"\0\0\0\x14ftypisom" + b"\0" * 15, "video/mp4")
        path = example_learning_path()
        for name, kind, media_id in (("picture", "IMAGE", picture), ("voice", "AUDIO", sound),
                                     ("clip", "VIDEO", video)):
            path.assets.append(TeachingAsset(asset_id=name, kind=kind, media_id=media_id,
                depends_on=["left-right"], condition_snapshot={"point": "x=0"}, content=name))
        draft = self.client.put(self.base, params=self.params, headers=self.owner,
                                json=path.model_dump())
        self.assertEqual(draft.status_code, 200, draft.text)
        self.assertEqual(self.client.get(f"/tutor/api/sessions/{self.sid}/media/{picture}",
            params={"asset_token": picture_token}).status_code, 423)
        library = self.client.get(f"/tutor/api/sessions/{self.sid}/media-library", params=self.params)
        self.assertEqual(library.status_code, 200, library.text)
        preview_url = next(a["media_url"] for a in library.json()["assets"] if a["asset_id"] == picture)
        self.assertEqual(parse_qs(urlsplit(preview_url).query)["teacher_token"], [self.token])
        self.assertEqual(self.client.get(preview_url).status_code, 200)
        result = self.client.post(self.base + "/publish", params=self.params, headers=self.owner,
            json={"revision_id": path.revision_id, "expected_digest": draft.json()["digest"],
                  "teacher_verified_content": True})
        self.assertEqual(result.status_code, 200, result.text)
        lesson = self.client.get(f"/tutor/api/sessions/{self.sid}", params=self.params).json()["lesson"]
        expected = {"picture": "image/png", "voice": "audio/mpeg", "clip": "video/mp4"}
        for asset_id, media_type in expected.items():
            item = next(step for step in lesson if step["segment_id"] == "path-asset:" + asset_id)
            self.assertEqual(item["media_type"], media_type)
        for asset_id, token in ((picture, picture_token), (sound, sound_token), (video, video_token)):
            self.assertEqual(self.client.get(f"/tutor/api/sessions/{self.sid}/media/{asset_id}",
                params={"asset_token": token}).status_code, 200)
        changed = path.model_copy(deep=True)
        changed.revision_id = "path-media-revision-2"
        changed.assets[0].content = "수정된 장면"
        self.assertEqual(self.client.put(self.base, params=self.params, headers=self.owner,
                                         json=changed.model_dump()).status_code, 200)
        self.assertEqual(self.client.get(f"/tutor/api/sessions/{self.sid}/media/{picture}",
            params={"asset_token": picture_token}).status_code, 423)

        # A token alone never grants editing rights to another teacher's account.
        with classroom.db() as conn:
            salt = "10" * 16
            conn.execute("INSERT INTO classroom_teachers VALUES(?,?,?,?,?)",
                ("teacher1", salt, classroom._teacher_password("teacher-password-123", salt), 1, classroom.now_iso()))
            conn.execute("INSERT INTO classroom_teachers VALUES(?,?,?,?,?)",
                ("teacher2", salt, classroom._teacher_password("teacher-password-234", salt), 1, classroom.now_iso()))
        owner_teacher_session = classroom.create_session_record(classroom.SessionCreate(title="교사 소유"),
            product_prefix="/classroom", owner_teacher="teacher1")
        target = f"/classroom/api/sessions/{owner_teacher_session['session_id']}/learning-path"
        tparams = {"teacher_token": owner_teacher_session["teacher_token"]}
        wrong = {"Authorization": "Basic " + base64.b64encode(b"teacher2:teacher-password-234").decode()}
        right = {"Authorization": "Basic " + base64.b64encode(b"teacher1:teacher-password-123").decode()}
        self.assertEqual(self.client.put(target, params=tparams, headers=wrong,
                                         json=example_learning_path().model_dump()).status_code, 403)
        self.assertEqual(self.client.put(target, params=tparams, headers=right,
                                         json=example_learning_path().model_dump()).status_code, 200)
        with classroom.db() as conn:
            conn.execute("UPDATE classroom_teachers SET active=0 WHERE username='teacher1'")
        self.assertEqual(self.client.get(target, params=tparams, headers=right).status_code, 401)


if __name__ == "__main__":
    unittest.main()
