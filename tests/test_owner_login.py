"""Owner authentication integration tests; all credentials are test-only."""
import os
import unittest
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from app.services import owner_basic_auth as auth


class OwnerLoginTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "EDURECOVER_OWNER_USER": "test-user",
            "EDURECOVER_OWNER_USER ": "",
            "EDURECOVER_OWNER_PASSWORD": "test-only-password",
        })
        self.env.start()
        self.app = FastAPI()
        self.app.include_router(auth.owner_auth_router)

        @self.app.get("/owner", dependencies=[Depends(auth.require_owner)])
        def owner():
            return {"owner": True}

        @self.app.get("/owner/ai-status", dependencies=[Depends(auth.require_owner)])
        def private_status():
            return {"private": True}

        @self.app.get("/owner/life/account-setup", dependencies=[Depends(auth.require_owner)])
        def life_account_setup():
            return {"life_account_setup": True}

        @self.app.post("/owner/test-write", dependencies=[Depends(auth.require_owner)])
        def private_write():
            return {"ok": True}

        self.client = TestClient(self.app, base_url="https://testserver", follow_redirects=False)
        self.origin = {"Origin": "https://testserver"}

    def tearDown(self):
        self.client.close()
        self.env.stop()

    def login(self, username="test-user", password="test-only-password"):
        return self.client.post("/owner/login", headers=self.origin,
                                data={"username": username, "password": password})

    def test_navigation_redirects_to_visible_login_form(self):
        response = self.client.get("/owner")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/owner/login")
        self.assertNotIn("www-authenticate", response.headers)
        page = self.client.get("/owner/login")
        self.assertEqual(page.status_code, 200)
        self.assertIn('name="username"', page.text)
        self.assertIn('name="password"', page.text)
        self.assertIn("no-store", page.headers["cache-control"])
        self.assertNotIn("test-only-password", page.text)

        life_setup = self.client.get("/owner/life/account-setup")
        self.assertEqual(life_setup.status_code, 303)
        self.assertEqual(life_setup.headers["location"], "/owner/login")
        self.assertNotIn("www-authenticate", life_setup.headers)

    def test_valid_login_and_private_requests(self):
        response = self.login()
        self.assertEqual(response.status_code, 303)
        cookie = response.headers["set-cookie"]
        for expected in ["HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=3600"]:
            self.assertIn(expected, cookie)
        self.assertNotIn("test-user", cookie)
        self.assertNotIn("test-only-password", cookie)
        self.assertEqual(self.client.get("/owner").status_code, 200)
        self.assertEqual(self.client.get("/owner/ai-status").status_code, 200)

    def test_wrong_credentials_do_not_create_session_or_echo_input(self):
        for user, password in [("other-user", "test-only-password"),
                               ("test-user", "wrong-secret"), ("", "")]:
            with self.subTest(user=user):
                response = self.login(user, password)
                self.assertIn(response.status_code, [400, 401])
                self.assertNotIn("set-cookie", response.headers)
                self.assertNotIn("wrong-secret", response.text)
        self.assertEqual(self.client.get("/owner").status_code, 303)

    def test_private_api_requires_authentication(self):
        response = self.client.get("/owner/ai-status", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/owner/login")

    def test_basic_clients_still_work(self):
        self.assertEqual(self.client.get("/owner/ai-status",
                         auth=("test-user", "test-only-password")).status_code, 200)
        response = self.client.get("/owner/ai-status",
                                   auth=("test-user", "wrong"), follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/owner/login")

    def test_unicode_credentials_work_in_form(self):
        os.environ["EDURECOVER_OWNER_USER"] = "테스트관리자"
        os.environ["EDURECOVER_OWNER_PASSWORD"] = "테스트암호전용"
        self.assertEqual(self.login("테스트관리자", "테스트암호전용").status_code, 303)
        self.assertEqual(self.client.get("/owner").status_code, 200)

    def test_optional_legacy_username_still_works(self):
        os.environ["EDURECOVER_OWNER_USER "] = "legacy-test-user"
        self.assertEqual(self.login("legacy-test-user").status_code, 303)

    def test_empty_configuration_fails_closed(self):
        for name in ["EDURECOVER_OWNER_USER", "EDURECOVER_OWNER_PASSWORD"]:
            with self.subTest(name=name), patch.dict(os.environ, {name: ""}):
                self.assertEqual(self.login().status_code, 503)
                self.assertEqual(self.client.get("/owner").status_code, 503)

    def test_cookie_tampering_fails(self):
        self.login()
        token = self.client.cookies.get(auth._COOKIE)
        self.client.cookies.clear()
        self.client.cookies.set(auth._COOKIE, token[:-1] + ("0" if token[-1] != "0" else "1"))
        self.assertEqual(self.client.get("/owner").status_code, 303)

    def test_expired_session_fails(self):
        self.login()
        with patch.object(auth.time, "time", return_value=auth.time.time() + 3601):
            self.assertEqual(self.client.get("/owner").status_code, 303)

    def test_credential_change_invalidates_session(self):
        self.login()
        os.environ["EDURECOVER_OWNER_PASSWORD"] = "new-test-password"
        self.assertEqual(self.client.get("/owner").status_code, 303)

    def test_restart_key_change_invalidates_session(self):
        self.login()
        with patch.object(auth, "_SESSION_KEY", b"x" * 32):
            self.assertEqual(self.client.get("/owner").status_code, 303)

    def test_logout_revokes_even_a_copied_session(self):
        self.login()
        token = self.client.cookies.get(auth._COOKIE)
        self.assertEqual(self.client.post("/owner/logout", headers=self.origin).status_code, 303)
        self.assertEqual(self.client.get("/owner").status_code, 303)
        self.client.cookies.clear()
        self.client.cookies.set(auth._COOKIE, token)
        self.assertEqual(self.client.get("/owner").status_code, 303)

    def test_cross_origin_login_and_cookie_writes_blocked(self):
        response = self.client.post("/owner/login", headers={"Origin": "https://other.example"},
                                    data={"username": "test-user", "password": "test-only-password"})
        self.assertEqual(response.status_code, 403)
        self.login()
        self.assertEqual(self.client.post("/owner/test-write").status_code, 403)
        self.assertEqual(self.client.post("/owner/test-write", headers=self.origin).status_code, 200)
        self.assertEqual(self.client.post("/owner/logout",
                         headers={"Origin": "https://other.example"}).status_code, 403)
        self.assertEqual(self.client.get("/owner").status_code, 200)

    def test_duplicate_and_oversized_form_input_rejected(self):
        headers = dict(self.origin, **{"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(self.client.post("/owner/login", headers=headers,
                         content="username=a&username=b&password=c").status_code, 400)
        self.assertEqual(self.client.post("/owner/login", headers=headers,
                         content="username=a&password=" + "x" * 9000).status_code, 413)


if __name__ == "__main__":
    unittest.main()
