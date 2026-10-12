"""Owner-disabled ZDR enforcement preserves approval facts and opt-in checks."""

import os
import unittest
from unittest.mock import patch

from app.services.privacy_guard import ProviderZDRHeld, assert_provider_zdr, privacy_status


class PrivacyGuardTests(unittest.TestCase):
    def test_unset_or_false_enforcement_does_not_block_unapproved_provider(self):
        for require in (None, "false", "", "ture"):
            env = {"EDURECOVER_CLAUDE_ZDR_CONFIRMED": "false", "EDURECOVER_CLAUDE_ZDR_BASIS": ""}
            if require is not None:
                env["EDURECOVER_REQUIRE_ZDR"] = require
            with self.subTest(require=require), patch.dict(os.environ, env, clear=True):
                assert_provider_zdr("claude")
                status = privacy_status()
                self.assertFalse(status["strict_provider_zdr_required"])
                self.assertFalse(status["fail_closed"])
                self.assertTrue(status["providers"]["claude"]["call_allowed_by_privacy_gate"])
                self.assertFalse(status["providers"]["claude"]["zdr_confirmed"])

    def test_approval_ledger_is_preserved_while_enforcement_is_disabled(self):
        with patch.dict(os.environ, {"EDURECOVER_REQUIRE_ZDR": "false", "EDURECOVER_CLAUDE_ZDR_CONFIRMED": "true",
                                     "EDURECOVER_CLAUDE_ZDR_BASIS": "test-only-prior-record"}, clear=True):
            row = privacy_status()["providers"]["claude"]
            self.assertTrue(row["zdr_confirmed"])
            self.assertTrue(row["basis_recorded"])
            self.assertTrue(row["call_allowed_by_privacy_gate"])

    def test_explicit_true_enforcement_requires_both_approval_and_basis(self):
        for flag, basis in (("false", ""), ("true", ""), ("ture", "record")):
            with self.subTest(flag=flag, basis=basis), patch.dict(os.environ, {
                "EDURECOVER_REQUIRE_ZDR": "true", "EDURECOVER_CLAUDE_ZDR_CONFIRMED": flag,
                "EDURECOVER_CLAUDE_ZDR_BASIS": basis,
            }, clear=True):
                with self.assertRaises(ProviderZDRHeld):
                    assert_provider_zdr("claude")
                self.assertTrue(privacy_status()["fail_closed"])
        with patch.dict(os.environ, {"EDURECOVER_REQUIRE_ZDR": "true", "EDURECOVER_CLAUDE_ZDR_CONFIRMED": "true",
                                    "EDURECOVER_CLAUDE_ZDR_BASIS": "test-only-record"}, clear=True):
            assert_provider_zdr("claude")


if __name__ == "__main__":
    unittest.main()
