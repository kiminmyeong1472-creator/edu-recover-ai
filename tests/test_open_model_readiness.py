"""Configuration diagnostics must never invoke providers or expose private input."""

import os
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import open_model_api as api
from app.services import open_model_transport as transport


class OpenModelReadinessTests(unittest.TestCase):
    def test_startup_records_reach_the_default_production_log_handler(self):
        # assertLogs changes logger levels, so it cannot catch records silently
        # discarded by the real Uvicorn configuration. Exercise that in a fresh
        # process with the default root WARNING level and no private environment.
        root = Path(__file__).resolve().parents[1]
        script = """
import copy
import logging
import logging.config
from uvicorn.config import LOGGING_CONFIG
logging.getLogger().setLevel(logging.WARNING)
logging.config.dictConfig(copy.deepcopy(LOGGING_CONFIG))
from app.services.open_model_api import log_open_model_readiness
log_open_model_readiness()
"""
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=root, capture_output=True,
            text=True, timeout=30,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(root),
                 "PYTHONNOUSERSITE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        records = [line for line in result.stderr.splitlines()
                   if "open_model_readiness model=" in line]
        self.assertEqual(len(records), 6, result.stderr)
        self.assertTrue(all("basis=config_only" in line for line in records))

    def test_startup_diagnostics_use_configuration_only_without_network_or_secrets(self):
        secret = "SYNTHETIC_PRIVATE_KEY"
        env = {"EDURECOVER_OPEN_MODELS_ENABLED": "true", "EDURECOVER_DEEPSEEK_DIRECT_ENABLED": "true",
               "DEEPSEEK_API_KEY": secret, "CHUTES_API_KEY": secret,
               "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": "{}"}
        with patch.dict(os.environ, env, clear=True), patch.object(
                api, "evaluate_provider", return_value=SimpleNamespace(state="ALLOWED")), patch.object(
                transport, "post_json") as http, patch.object(transport, "_new_client") as sdk, patch(
                "socket.create_connection") as socket, self.assertLogs(api._LOGGER, level="INFO") as logs:
            api.log_open_model_readiness()
        http.assert_not_called()
        sdk.assert_not_called()
        socket.assert_not_called()
        self.assertEqual(len(logs.output), 6)
        text = "\n".join(logs.output)
        for route in ("main", "learning", "chutes"):
            self.assertIn("route=" + route, text)
        self.assertIn("state=configured_not_verified permission=ALLOWED basis=config_only", text)
        self.assertIn("model=DeepSeek-V3.2 route=chutes state=configured_not_verified", text)
        self.assertIn("model=DeepSeek-V4-Flash-0731-FP8-DSpark route=chutes state=configured_not_verified", text)
        self.assertNotIn("model=DeepSeek-V4-Pro route=chutes", text)
        for private in (secret, "api.deepseek.com", "DEEPSEEK_API_KEY", "Bearer"):
            self.assertNotIn(private, text)

    def test_configuration_and_policy_failures_do_not_log_exception_messages(self):
        private = "SYNTHETIC_PRIVATE_EXCEPTION"
        with patch.object(api, "catalog_status", side_effect=RuntimeError(private)), patch.object(
                api, "evaluate_provider", side_effect=RuntimeError(private)), self.assertLogs(
                api._LOGGER, level="INFO") as logs:
            api.log_open_model_readiness()
        text = "\n".join(logs.output)
        self.assertEqual(len(logs.output), 6)
        self.assertIn("state=config_invalid permission=CONTROL_CONFIG_INVALID", text)
        self.assertNotIn(private, text)

    def test_readiness_preserves_real_owner_control_states(self):
        cases = (({"EDURECOVER_AI_HOLD": "true"}, "HOLD"),
                 ({"EDURECOVER_BLOCKED_PROVIDERS": "deepseek"}, "BLOCKED"),
                 ({"EDURECOVER_RESTRICTED_PROVIDERS": "deepseek"}, "RESTRICTED_VERIFY_ONLY"),
                 ({"EDURECOVER_ALLOWED_PROVIDERS": ""}, "OWNER_PERMISSION_REQUIRED"))
        for change, expected in cases:
            env = {"EDURECOVER_ALLOWED_PROVIDERS": "deepseek", **change}
            with self.subTest(state=expected), patch.dict(os.environ, env, clear=True), self.assertLogs(
                    api._LOGGER, level="INFO") as logs:
                api.log_open_model_readiness()
            self.assertTrue(all("permission=" + expected in line for line in logs.output))

    def test_unknown_diagnostic_state_and_identifiers_are_not_logged(self):
        private = "SYNTHETIC_PRIVATE_STATE"
        rows = [{"model_key": "DeepSeek-V4.1-Flash", "readiness_state": private},
                {"model_key": private, "readiness_state": "configured_not_verified"}]
        with patch.object(api, "catalog_status", return_value=rows), patch.object(
                api, "evaluate_provider", return_value=SimpleNamespace(state=private)), self.assertLogs(
                api._LOGGER, level="INFO") as logs:
            api.log_open_model_readiness()
            api._log_failure(private, private, private)
        text = "\n".join(logs.output)
        self.assertNotIn(private, text)
        self.assertIn("model=unregistered route=main code=OPEN_MODEL_CALL_FAILED", text)

    def test_readiness_handler_is_registered_for_startup(self):
        from app.main import app
        self.assertIn(api.log_open_model_readiness, app.router.on_startup)
