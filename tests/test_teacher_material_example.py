"""The classroom product exposes its example to an authenticated teacher."""

import os
import tempfile
import unittest
from http.cookies import SimpleCookie
from uuid import uuid4

os.environ.setdefault("EDURECOVER_OWNER_USER", "owner-test")
os.environ.setdefault("EDURECOVER_OWNER_PASSWORD", "password-test")
os.environ.setdefault("EDURECOVER_CLASSROOM_STATE_DIR", tempfile.mkdtemp(prefix="edu-teacher-material-"))

from fastapi import HTTPException, Request, Response

from app.classroom_tutor import (
    ClassroomTeacherCredentials, classroom_teacher_login, create_classroom_teacher,
    require_classroom_user, teacher_classroom_router, teacher_material_example, teacher_product_home,
)


class TeacherStructuredMaterialExampleTests(unittest.TestCase):
    def test_teacher_can_load_example_without_owner_privileges(self):
        route = next(route for route in teacher_classroom_router.routes
                     if route.path == "/classroom/api/structured-materials/example")
        self.assertIs(route.endpoint, teacher_material_example)

        path = "/classroom/api/structured-materials/example"
        scope = {"type": "http", "method": "GET", "path": path, "headers": [],
                 "scheme": "https", "server": ("testserver", 443), "query_string": b""}
        with self.assertRaises(HTTPException) as unauthenticated:
            require_classroom_user(Request(scope), None)
        self.assertEqual(unauthenticated.exception.status_code, 401)

        credentials = ClassroomTeacherCredentials(
            username="teacher_" + uuid4().hex[:8], password="classroom-test-password-2026")
        create_classroom_teacher(credentials, None)
        login_response = Response()
        classroom_teacher_login(credentials, login_response)
        cookie = SimpleCookie()
        cookie.load(login_response.headers["set-cookie"])
        scope["headers"] = [(b"cookie", f"__Host-edurecover_teacher={cookie['__Host-edurecover_teacher'].value}".encode())]
        teacher = require_classroom_user(Request(scope), None)
        self.assertEqual(teacher, credentials.username)
        self.assertTrue(teacher_material_example(teacher)["scenarios"])

        page = teacher_product_home(teacher).body.decode("utf-8")
        self.assertIn("api('/classroom/api/structured-materials/example')", page)
        self.assertNotIn("api('/tutor/api/structured-materials/example')", page)
        self.assertIn("id='materialFiles'", page)
        self.assertIn("showMaterialMediaPreviews", page)


if __name__ == "__main__":
    unittest.main()
