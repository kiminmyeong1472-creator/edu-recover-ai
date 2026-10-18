"""Owner/Life separation and preservation, using disposable credentials only."""
import hashlib
import os
import sqlite3
from unittest.mock import patch

from fastapi import Depends

from app.platform_core import life_auth as auth
from app.services import owner_basic_auth as owner
import test_life_login as login_fixtures


class LoginScopePreservationTests(login_fixtures.LifeLoginTests):
    # Run only the additional cases here; the base suite is collected separately.
    def setUp(self):
        super().setUp()
        self.client.app.include_router(owner.owner_auth_router)

        @self.client.app.get("/owner/test-scope")
        def owner_private(_: None = Depends(owner.require_owner)):
            return {"authorized": True}

    def digest(self):
        return hashlib.sha256((self.root / "liferecover/accounts.sqlite3").read_bytes()).hexdigest()

    def owner_form_login(self):
        return self.client.post("/owner/login", data={
            "username": "test-owner", "password": "test-only-owner-password",
        }, headers={"Origin": "https://testserver"})

    def test_additional_owner_cookie_does_not_grant_life_access(self):
        self.account()
        self.assertEqual(self.owner_form_login().status_code, 303)
        self.assertEqual(self.client.get("/owner/test-scope").status_code, 200)
        self.assertEqual(self.client.get("/test-private").status_code, 401)

    def test_additional_life_cookie_does_not_grant_owner_access(self):
        self.account(password="short123")
        self.assertEqual(self.form_login("short123").status_code, 303)
        self.assertEqual(self.client.get("/test-private").status_code, 200)
        self.assertEqual(self.client.get("/owner/test-scope").status_code, 303)

    def test_additional_missing_life_storage_does_not_break_owner_login(self):
        self.assertEqual(self.form_login().status_code, 503)
        self.assertEqual(self.owner_form_login().status_code, 303)
        self.assertEqual(self.client.get("/owner/test-scope").status_code, 200)
        self.assertFalse(self.root.exists())

    def test_additional_owner_restart_ends_only_owner_session(self):
        self.account()
        self.assertEqual(self.form_login().status_code, 303)
        self.assertEqual(self.owner_form_login().status_code, 303)
        with patch.object(owner, "_SESSION_KEY", b"different-test-process-key-32bytes"):
            self.assertEqual(self.client.get("/owner/test-scope").status_code, 303)
            self.assertEqual(self.client.get("/test-private").status_code, 200)

    def test_additional_missing_owner_config_does_not_break_life_login(self):
        self.account()
        os.environ["EDURECOVER_OWNER_PASSWORD"] = ""
        self.assertEqual(self.owner_form_login().status_code, 503)
        self.assertEqual(self.form_login().status_code, 303)

    def test_additional_reads_and_failed_logins_preserve_database_bytes(self):
        self.account(password="short123")
        before = self.digest()
        for password in ["incorrect-test-value", "short123"]:
            self.form_login(password)
        auth.login_readiness()
        self.client.get("/test-private")
        self.client.get("/owner/life/auth-status", auth=self.owner())
        self.assertEqual(self.digest(), before)

    def test_additional_countable_but_incompatible_schema_is_not_ready(self):
        path = self.root / "liferecover/accounts.sqlite3"
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE account(account_id TEXT)")
            db.execute("INSERT INTO account VALUES('test-account')")
        before = self.digest()
        self.assertFalse(auth.login_readiness()["ready"])
        self.assertEqual(self.digest(), before)

    def test_additional_startup_diagnostic_does_not_disclose_credentials(self):
        self.account()
        with self.assertLogs("uvicorn.error", level="INFO") as logs:
            auth.log_login_readiness()
        output = " ".join(logs.output)
        self.assertIn('"password_check": "not_performed"', output)
        for value in ["test-owner", "test-life", "usr_test_account", "test-only-owner-password",
                      "test-only-life-password", "test-only-signing-secret", "password_hash"]:
            self.assertNotIn(value, output)


# Avoid duplicating inherited cases in the new class's test count.
for _name in dir(login_fixtures.LifeLoginTests):
    if _name.startswith("test_") and not _name.startswith("test_additional_"):
        setattr(LoginScopePreservationTests, _name, None)
