"""Life login regressions; all accounts and secrets are disposable test values."""
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.platform_core import life_auth as auth


class LifeLoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "state"
        self.env = patch.dict(os.environ, {
            "EDURECOVER_CLASSROOM_STATE_DIR": str(self.root),
            "LIFERECOVER_SESSION_SECRET": "test-only-signing-secret-" * 2,
            "LIFERECOVER_REGISTRATION_INVITE": "test-only-invite",
            "EDURECOVER_OWNER_USER": "test-owner",
            "EDURECOVER_OWNER_USER ": "",
            "EDURECOVER_OWNER_PASSWORD": "test-only-owner-password",
        })
        self.env.start()
        app = FastAPI()
        app.include_router(auth.router)
        app.include_router(auth.owner_router)

        @app.get("/test-private")
        def private(account_id: str = Depends(auth.require_life_user)):
            return {"account_id": account_id}

        self.client = TestClient(app, base_url="https://testserver", follow_redirects=False)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def account(self, password="test-only-life-password", username="test-life"):
        salt = b"test-only-salt-16"
        with auth._db() as conn:
            conn.execute("INSERT INTO account VALUES(?,?,?,?,?)", (
                "usr_test_account", username, auth._hash_password(password, salt),
                salt.hex(), int(time.time()),
            ))
            conn.commit()

    def form_login(self, password="test-only-life-password", username="test-life"):
        return self.client.post("/life/auth/login-form", data={
            "username": username, "password": password,
        })

    def owner(self):
        return ("test-owner", "test-only-owner-password")

    def test_existing_short_password_works_in_form_and_json(self):
        self.account(password="short123")
        form = self.form_login(password="short123")
        self.assertEqual(form.status_code, 303)
        self.assertEqual(form.headers["location"], "/life")
        self.assertEqual(self.client.get("/test-private").json(), {"account_id": "usr_test_account"})
        cookie = form.headers["set-cookie"]
        for flag in ["HttpOnly", "Secure", "SameSite=strict", "Path=/"]:
            self.assertIn(flag, cookie)
        self.client.cookies.clear()
        api = self.client.post("/life/auth/login", json={"username": "test-life", "password": "short123"})
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json()["account_id"], "usr_test_account")
        self.assertEqual(api.headers["cache-control"], "no-store")

    def test_browser_login_accepts_existing_passwords_and_explains_separate_account(self):
        page = self.client.get("/life/auth/login")
        self.assertIn('minlength="1" maxlength="256"', page.text)
        self.assertIn('for="password"', page.text)
        self.assertIn("관리자 계정과는 별도", page.text)
        self.assertIn('/owner/life/account-setup', page.text)

    def test_new_accounts_still_require_ten_character_password(self):
        response = self.client.post("/life/auth/register", json={
            "username": "new-life", "password": "short123", "invite_code": "test-only-invite",
        })
        self.assertEqual(response.status_code, 422)
        setup = self.client.get("/owner/life/account-setup", auth=self.owner())
        self.assertIn('minlength="10" maxlength="256"', setup.text)
        response = self.client.post("/owner/life/account-setup", auth=self.owner(), data={
            "username": "new-life", "password": "short123",
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.root.exists())

    def test_invalid_credentials_are_401_without_cookie_or_secret_echo(self):
        self.account()
        for username, password in [("test-life", "wrong-password"), ("missing-life", "wrong-password")]:
            with self.subTest(username=username):
                with self.assertLogs(auth.logger, level="WARNING") as logs:
                    response = self.form_login(password, username)
                self.assertEqual(response.status_code, 401)
                self.assertNotIn("set-cookie", response.headers)
                self.assertNotIn(password, response.text)
                self.assertNotIn(username, " ".join(logs.output))
                self.assertNotIn(password, " ".join(logs.output))

    def test_missing_signing_secret_is_503_in_both_login_paths(self):
        self.account()
        os.environ["LIFERECOVER_SESSION_SECRET"] = "short"
        form = self.form_login()
        self.assertEqual(form.status_code, 503)
        self.assertIn("로그인 서버 설정", form.text)
        self.assertNotIn("set-cookie", form.headers)
        api = self.client.post("/life/auth/login", json={
            "username": "test-life", "password": "test-only-life-password",
        })
        self.assertEqual(api.status_code, 503)

    def test_registration_failure_does_not_leave_an_unusable_account(self):
        os.environ["LIFERECOVER_SESSION_SECRET"] = "short"
        payload = {"username": "new-life", "password": "test-only-life-password", "invite_code": "test-only-invite"}
        response = self.client.post("/life/auth/register", json=payload)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(self.root.exists())
        os.environ["LIFERECOVER_SESSION_SECRET"] = "test-only-signing-secret-" * 2
        self.assertEqual(self.client.post("/life/auth/register", json=payload).status_code, 200)

    def test_missing_database_is_reported_without_creating_one(self):
        self.assertEqual(self.form_login().status_code, 503)
        self.assertEqual(self.client.post("/life/auth/login", json={
            "username": "test-life", "password": "test-only-life-password",
        }).status_code, 503)
        self.assertFalse(self.root.exists())

    def test_database_with_missing_schema_is_503(self):
        self.root.joinpath("liferecover").mkdir(parents=True)
        path = self.root / "liferecover" / "accounts.sqlite3"
        sqlite3.connect(path).close()
        original = path.read_bytes()
        response = self.form_login()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(path.read_bytes(), original)
        self.assertNotIn(str(self.root), response.text)

    def test_corrupt_database_is_503_and_is_preserved(self):
        self.root.joinpath("liferecover").mkdir(parents=True)
        path = self.root / "liferecover" / "accounts.sqlite3"
        path.write_bytes(b"not-a-sqlite-database")
        self.assertEqual(self.form_login().status_code, 503)
        self.assertEqual(path.read_bytes(), b"not-a-sqlite-database")

    def test_duplicate_form_fields_are_rejected(self):
        self.account()
        response = self.client.post("/life/auth/login-form", content=
            "username=test-life&password=test-only-life-password&password=other",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("set-cookie", response.headers)

    def test_malformed_and_oversized_forms_keep_their_error_status(self):
        self.assertEqual(self.client.post("/life/auth/login-form", json={}).status_code, 400)
        oversized = self.client.post("/life/auth/login-form", content="x" * 9000,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(oversized.status_code, 413)

    def test_unicode_invite_can_register_and_log_in(self):
        os.environ["LIFERECOVER_REGISTRATION_INVITE"] = "테스트 초대 코드"
        registered = self.client.post("/life/auth/register", json={
            "username": "테스트사용자", "password": "테스트용으로만쓰는비밀번호", "invite_code": "테스트 초대 코드",
        })
        self.assertEqual(registered.status_code, 200)
        self.client.cookies.clear()
        self.assertEqual(self.form_login("테스트용으로만쓰는비밀번호", "테스트사용자").status_code, 303)

    def test_malformed_unicode_session_is_rejected(self):
        self.assertIsNone(auth._parse_session(f"사용자.{int(time.time()) + 100}.{'a' * 32}.{'0' * 64}"))

    def test_existing_session_reports_database_loss_as_service_error(self):
        self.account()
        self.assertEqual(self.form_login().status_code, 303)
        path = self.root / "liferecover" / "accounts.sqlite3"
        path.rename(path.with_suffix(".saved"))
        self.assertEqual(self.client.get("/test-private").status_code, 503)
        self.assertFalse(path.exists())

    def test_diagnostic_requires_owner_auth_and_hides_account_values(self):
        self.account()
        anonymous = self.client.get("/owner/life/auth-status")
        self.assertEqual(anonymous.status_code, 303)
        response = self.client.get("/owner/life/auth-status", auth=self.owner())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ready"])
        self.assertEqual(response.json()["account_count"], 1)
        self.assertEqual(response.json()["password_check"], "not_performed")
        self.assertEqual(response.headers["cache-control"], "no-store")
        for secret in ["test-life", "test-only-life-password", "test-only-signing-secret", "password_hash", "salt"]:
            self.assertNotIn(secret, response.text)

    def test_diagnostic_does_not_create_storage(self):
        result = auth.login_readiness()
        self.assertFalse(result["ready"])
        self.assertIn("LIFE_ACCOUNT_DATABASE_UNAVAILABLE", result["blockers"])
        self.assertFalse(self.root.exists())

    def test_empty_account_database_has_explicit_setup_blocker(self):
        with auth._db():
            pass
        result = auth.login_readiness()
        self.assertEqual(result["account_count"], 0)
        self.assertIn("LIFE_ACCOUNT_SETUP_REQUIRED", result["blockers"])

    def test_owner_credentials_do_not_bypass_life_accounts(self):
        self.account()
        self.assertEqual(self.form_login("test-only-owner-password", "test-owner").status_code, 401)

    def test_invalid_account_record_reports_503(self):
        self.account()
        with auth._db() as conn:
            conn.execute("UPDATE account SET salt = 'invalid-hex'")
            conn.commit()
        self.assertEqual(self.form_login().status_code, 503)

    def test_account_setup_escapes_username(self):
        response = self.client.post("/owner/life/account-setup", auth=self.owner(), data={
            "username": "<b>test-life</b>", "password": "test-only-life-password",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("&lt;b&gt;test-life&lt;/b&gt;", response.text)
        self.assertNotIn("<b><b>test-life</b></b>", response.text)


if __name__ == "__main__":
    unittest.main()
