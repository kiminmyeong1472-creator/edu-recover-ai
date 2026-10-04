"""Structured audiovisual lessons use only verified media from their own session."""

import base64
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("EDURECOVER_OWNER_USER", "owner-test")
os.environ.setdefault("EDURECOVER_OWNER_PASSWORD", "password-test")
os.environ.setdefault("EDURECOVER_CLASSROOM_STATE_DIR", tempfile.mkdtemp(prefix="edu-material-media-"))

from fastapi import HTTPException

import app.classroom_tutor as classroom
from app.services.structured_materials import Representation, example_bundle


PNG = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\x01\0\0\0\x01" + b"test"


class MaterialMediaReleaseTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="edu-material-release-")
        self.old_state, self.old_db = classroom.STATE_DIR, classroom.DB_PATH
        classroom.STATE_DIR = Path(self.folder.name)
        classroom.DB_PATH = classroom.STATE_DIR / "classroom_tutor.sqlite3"
        self.session = classroom.create_session_record(
            classroom.SessionCreate(title="조건별 그림 수업"), product_prefix="/classroom")
        self.session_id = self.session["session_id"]
        self.teacher_token = self.session["teacher_token"]

    def tearDown(self):
        classroom.STATE_DIR, classroom.DB_PATH = self.old_state, self.old_db
        self.folder.cleanup()

    def add_media_to_bundle(self):
        uploaded = classroom.upload_classroom_media(
            self.session_id,
            classroom.MediaUpload(media_type="image/png", data_base64=base64.b64encode(PNG).decode()),
            self.teacher_token,
        )
        url = urlsplit(uploaded["media_url"])
        media_asset_id = url.path.rsplit("/", 1)[-1]
        asset_token = parse_qs(url.query)["asset_token"][0]
        bundle = example_bundle().model_copy(deep=True)
        bundle.representations.append(Representation(
            asset_id="linear-teacher-image", kind="IMAGE", scenario_id="linear",
            condition_revision_id="linear-c1", generation_id="linear-g1",
            depends_on=["l0"], content="조건별 설명 그림",
            media_asset_id=media_asset_id, media_sha256=uploaded["sha256"], media_type="image/png",
        ))
        return bundle, media_asset_id, asset_token

    def publish(self, bundle):
        draft = classroom.save_material_draft(self.session_id, bundle, self.teacher_token)
        return classroom.publish_materials(self.session_id,
            classroom.MaterialPublication(revision_id=bundle.revision_id,
                expected_digest=draft["digest"], teacher_verified_content=True), self.teacher_token)

    def test_public_media_is_bound_to_published_revision_and_verified_bytes(self):
        bundle, asset_id, asset_token = self.add_media_to_bundle()
        with self.assertRaises(HTTPException) as unauthorized_library:
            classroom.classroom_media_library(self.session_id, "wrong-teacher-token")
        self.assertEqual(unauthorized_library.exception.status_code, 403)
        library = classroom.classroom_media_library(self.session_id, self.teacher_token)
        self.assertEqual(library["assets"][0]["asset_id"], asset_id)
        self.publish(bundle)
        with classroom.db() as conn:
            row = conn.execute("SELECT lesson_json FROM sessions WHERE session_id=?", (self.session_id,)).fetchone()
            self.assertIn(asset_id, row["lesson_json"])
            path = Path(conn.execute("SELECT file_path FROM classroom_media WHERE asset_id=?",
                                     (asset_id,)).fetchone()["file_path"])

        response = classroom.read_classroom_media(self.session_id, asset_id, asset_token)
        self.assertEqual(response.body, PNG)
        self.assertEqual(response.media_type, "image/png")
        unused = classroom.upload_classroom_media(
            self.session_id, classroom.MediaUpload(
                media_type="image/png", data_base64=base64.b64encode(PNG).decode()),
            self.teacher_token,
        )
        unused_url = urlsplit(unused["media_url"])
        with self.assertRaises(HTTPException) as unpublished:
            classroom.read_classroom_media(
                self.session_id, unused_url.path.rsplit("/", 1)[-1],
                parse_qs(unused_url.query)["asset_token"][0],
            )
        self.assertEqual(unpublished.exception.status_code, 423)
        with classroom.db() as conn:
            published = classroom.row_to_session(classroom.get_session_or_404(conn, self.session_id))
        published["state"].pop("released_lesson_digest")
        with self.assertRaises(HTTPException) as missing_receipt:
            classroom.ensure_material_release(published)
        self.assertEqual(missing_receipt.exception.status_code, 423)

        path.write_bytes(PNG + b"changed")
        with self.assertRaises(HTTPException) as tampered:
            classroom.read_classroom_media(self.session_id, asset_id, asset_token)
        self.assertEqual(tampered.exception.status_code, 423)
        path.write_bytes(PNG)

        new_draft = bundle.model_copy(deep=True)
        new_draft.revision_id = "absolute-differentiability-draft-v2"
        classroom.save_material_draft(self.session_id, new_draft, self.teacher_token)
        with self.assertRaises(HTTPException) as withdrawn:
            classroom.read_classroom_media(self.session_id, asset_id, asset_token)
        self.assertEqual(withdrawn.exception.status_code, 423)
        self.assertEqual(classroom.read_classroom_media(
            self.session_id, asset_id, asset_token, teacher_token=self.teacher_token).body, PNG)

    def test_other_sessions_media_cannot_be_published(self):
        bundle, _, _ = self.add_media_to_bundle()
        other = classroom.create_session_record(
            classroom.SessionCreate(title="다른 수업"), product_prefix="/classroom")
        draft = classroom.save_material_draft(other["session_id"], bundle, other["teacher_token"])
        with self.assertRaises(HTTPException) as mismatch:
            classroom.publish_materials(other["session_id"], classroom.MaterialPublication(
                revision_id=bundle.revision_id, expected_digest=draft["digest"],
                teacher_verified_content=True), other["teacher_token"])
        self.assertEqual(mismatch.exception.status_code, 423)

    def test_saved_draft_previews_verify_bytes_with_teacher_token(self):
        bundle, asset_id, asset_token = self.add_media_to_bundle()
        classroom.save_material_draft(self.session_id, bundle, self.teacher_token)
        library = classroom.classroom_media_library(self.session_id, self.teacher_token)
        preview_url = next(a["media_url"] for a in library["assets"] if a["asset_id"] == asset_id)
        query = parse_qs(urlsplit(preview_url).query)
        self.assertEqual(query["asset_token"], [asset_token])
        self.assertEqual(query["teacher_token"], [self.teacher_token])
        with self.assertRaises(HTTPException) as not_released:
            classroom.read_classroom_media(self.session_id, asset_id, asset_token)
        self.assertEqual(not_released.exception.status_code, 423)
        self.assertEqual(classroom.read_classroom_media(self.session_id, asset_id, asset_token,
                          teacher_token=query["teacher_token"][0]).body, PNG)

    def test_legacy_upload_requires_publication_and_current_lesson_reference(self):
        uploaded = classroom.upload_classroom_media(
            self.session_id, classroom.MediaUpload(
                media_type="image/png", data_base64=base64.b64encode(PNG).decode()),
            self.teacher_token,
        )
        url = urlsplit(uploaded["media_url"])
        asset_id = url.path.rsplit("/", 1)[-1]
        asset_token = parse_qs(url.query)["asset_token"][0]

        def expect_blocked():
            with self.assertRaises(HTTPException) as blocked:
                classroom.read_classroom_media(self.session_id, asset_id, asset_token)
            self.assertEqual(blocked.exception.status_code, 423)

        expect_blocked()
        self.assertEqual(classroom.read_classroom_media(
            self.session_id, asset_id, asset_token, teacher_token=self.teacher_token).body, PNG)
        step = classroom.Segment(segment_id="lesson-image", kind="DEMONSTRATE",
                                 title="설명 그림", media_url=uploaded["media_url"],
                                 media_type="image/png")
        classroom.replace_lesson(self.session_id, classroom.LessonReplace(lesson=[step]),
                                 self.teacher_token)
        expect_blocked()
        with classroom.db() as conn:
            session = classroom.row_to_session(classroom.get_session_or_404(conn, self.session_id))
        classroom.publish_legacy_lesson(self.session_id, classroom.LessonPublication(
            expected_digest=session["lesson_digest"], teacher_verified_content=True),
            self.teacher_token)
        self.assertEqual(classroom.read_classroom_media(
            self.session_id, asset_id, asset_token).body, PNG)

        classroom.replace_lesson(self.session_id, classroom.LessonReplace(lesson=[
            classroom.Segment(segment_id="no-image", kind="EXPLAIN", title="새 설명")
        ]), self.teacher_token)
        expect_blocked()
        with classroom.db() as conn:
            session = classroom.row_to_session(classroom.get_session_or_404(conn, self.session_id))
        classroom.publish_legacy_lesson(self.session_id, classroom.LessonPublication(
            expected_digest=session["lesson_digest"], teacher_verified_content=True),
            self.teacher_token)
        expect_blocked()


if __name__ == "__main__":
    unittest.main()
