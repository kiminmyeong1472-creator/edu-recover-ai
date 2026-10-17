"""Offline checks of the exact 25/27-name diagnostic command; no paid calls."""
import asyncio
import contextlib
import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.control_plane import ProviderHeld
from app.services.open_model_catalog import (
    AWS_ADDITIONAL_MODEL_CATALOG, CHUTES_MODEL_CATALOG, SOURCE_MODEL_CATALOG,
)
from app.services.open_model_transport import OpenModelError, catalog_status
from app.services.training_model_policy import permitted_model_catalog
from scripts.check_open_model_connections import check_models, main


MODULE = "scripts.check_open_model_connections"


def status_rows(*, configured=True, enabled=True):
    return [dict(entry, configured=configured, enabled=enabled,
                 readiness_state="CONFIGURED_NOT_TESTED",
                 endpoint_url="https://private-deployment.invalid/v1/chat/completions",
                 api_key="private-api-secret", source="private-source-material")
            for entry in SOURCE_MODEL_CATALOG + AWS_ADDITIONAL_MODEL_CATALOG]


class OpenModelConnectionCheckTests(unittest.TestCase):
    def setUp(self):
        self.rows = status_rows()
        self.status = self.enterContext(patch(MODULE + ".catalog_status", return_value=self.rows))
        self.evaluate = self.enterContext(patch(
            MODULE + ".evaluate_provider", return_value=SimpleNamespace(allowed=True)))
        self.allowed = self.enterContext(patch(MODULE + ".assert_provider_allowed"))
        self.invoke = self.enterContext(patch(
            MODULE + ".call_model", new_callable=AsyncMock, return_value="2"))

    def enterContext(self, cm):
        # unittest.TestCase.enterContext is not available on every supported
        # Python version; register cleanup before returning the patched value.
        value = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        return value

    def run_check(self, keys=None, invoke=False, route=None):
        return asyncio.run(check_models([] if keys is None else keys, invoke=invoke, route=route))

    def run_cli(self, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(args)
        return code, output.getvalue()

    def assert_private_values_absent(self, value):
        output = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        for private in ("private-deployment", "private-api-secret", "private-source-material",
                        "private-upstream-response", "private-error-detail"):
            self.assertNotIn(private, output)

    def test_default_reports_exactly_25_selected_names_and_eight_families_without_calls(self):
        result = self.run_check()
        self.assertEqual(result["mode"], "configuration_only")
        self.assertTrue(result["configuration_is_not_live_success"])
        self.assertEqual([row["model_key"] for row in result["models"]],
                         [entry["model_key"] for entry in SOURCE_MODEL_CATALOG])
        self.assertEqual(len(result["models"]), 25)
        self.assertEqual(len({row["family"] for row in result["models"]}), 8)
        for row in result["models"]:
            self.assertTrue(row["configured"])
            self.assertTrue(row["enabled"])
            self.assertTrue(row["permitted"])
            self.assertEqual(row["readiness_state"], "CONFIGURED_NOT_TESTED")
            self.assertFalse(row["live_call_attempted"])
            self.assertNotIn("response_received", row)
            self.assertNotIn("answer_matches_expected", row)
        self.invoke.assert_not_awaited()
        self.allowed.assert_not_called()
        self.assert_private_values_absent(result)

    def test_chutes_exact_nine_can_be_checked_without_legacy_expansion(self):
        self.status.return_value = [dict(row, configured=True, enabled=True)
                                    for row in CHUTES_MODEL_CATALOG]
        keys = [row["model_key"] for row in CHUTES_MODEL_CATALOG]
        result = self.run_check(keys, invoke=True, route="chutes")
        self.assertEqual(result["route"], "chutes")
        self.assertEqual([row["model_key"] for row in result["models"]], keys)
        self.assertEqual(len(keys), 9)
        self.assertEqual(self.invoke.await_count, 9)
        self.assertTrue(all(row["response_received"] for row in result["models"]))
        for call in self.invoke.await_args_list:
            self.assertEqual(call.kwargs, {"max_tokens": 512, "route": "chutes"})
        self.assert_private_values_absent(result)

    def test_chutes_checks_need_explicit_names_and_reject_original_aliases(self):
        with self.assertRaises(ValueError):
            self.run_check(invoke=True, route="chutes")
        with self.assertRaises(ValueError):
            self.run_check(["Qwen3.6-27B"], invoke=True, route="chutes")
        self.status.assert_not_called()
        self.invoke.assert_not_awaited()

    def test_all_25_can_be_individually_selected_and_invoke_exactly_one_synthetic_request(self):
        for entry in SOURCE_MODEL_CATALOG:
            with self.subTest(model_key=entry["model_key"]):
                self.invoke.reset_mock()
                self.allowed.reset_mock()
                result = self.run_check([entry["model_key"]], invoke=True)
                self.assertEqual(len(result["models"]), 1)
                row = result["models"][0]
                self.assertEqual(row["model_key"], entry["model_key"])
                self.assertTrue(row["live_call_attempted"])
                self.assertTrue(row["response_received"])
                self.assertTrue(row["answer_matches_expected"])
                self.allowed.assert_called_once_with(entry["family"], "generate")
                self.invoke.assert_awaited_once_with(
                    entry["model_key"], "답은 숫자 하나만 출력하십시오.",
                    "1 더하기 1은 얼마입니까?", max_tokens=32,
                )
                self.assert_private_values_absent(result)

    def test_repeated_model_options_select_only_explicit_names(self):
        keys = [SOURCE_MODEL_CATALOG[0]["model_key"], SOURCE_MODEL_CATALOG[-1]["model_key"]]
        code, output = self.run_cli(["--invoke", "--model-key", keys[0], "--model-key", keys[1]])
        self.assertEqual(code, 0)
        self.assertEqual([row["model_key"] for row in json.loads(output)["models"]], keys)
        self.assertEqual(self.invoke.await_count, 2)

    def test_invoke_without_explicit_names_is_blocked_before_default_resolution(self):
        with self.assertRaises(ValueError):
            self.run_check(invoke=True)
        self.status.assert_not_called()
        self.invoke.assert_not_awaited()
        code, output = self.run_cli(["--invoke"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output), {"error_code": "INVALID_CHECK_REQUEST"})

    def test_invalid_duplicate_case_changed_and_aws_extra_names_are_blocked(self):
        key = SOURCE_MODEL_CATALOG[0]["model_key"]
        invalid_cases = [["unknown-model"], [key, key], [key.lower()], [""], [key + " "], [None]]
        invalid_cases.extend([[entry["model_key"]] for entry in AWS_ADDITIONAL_MODEL_CATALOG])
        for keys in invalid_cases:
            for live in (False, True):
                with self.subTest(keys=keys, invoke=live), self.assertRaises(ValueError):
                    self.run_check(keys, invoke=live)
        self.status.assert_not_called()
        self.invoke.assert_not_awaited()

    def test_policy_hold_prevents_call_even_when_binding_is_enabled(self):
        self.evaluate.return_value = SimpleNamespace(allowed=False)
        self.allowed.side_effect = ProviderHeld("private-error-detail")
        result = self.run_check([SOURCE_MODEL_CATALOG[0]["model_key"]], invoke=True)
        row = result["models"][0]
        self.assertFalse(row["permitted"])
        self.assertFalse(row["live_call_attempted"])
        self.assertFalse(row["response_received"])
        self.assertEqual(row["error_code"], "OPEN_MODEL_POLICY_HELD")
        self.invoke.assert_not_awaited()
        self.assert_private_values_absent(result)

    def test_unconfigured_or_disabled_rows_do_not_invoke(self):
        for configured, enabled, error in (
            (False, False, "OPEN_MODEL_NOT_CONFIGURED"),
            (True, False, "OPEN_MODEL_DISABLED"),
        ):
            with self.subTest(configured=configured, enabled=enabled):
                self.status.return_value = status_rows(configured=configured, enabled=enabled)
                row = self.run_check([SOURCE_MODEL_CATALOG[0]["model_key"]], invoke=True)["models"][0]
                self.assertFalse(row["live_call_attempted"])
                self.assertFalse(row["response_received"])
                self.assertEqual(row["error_code"], error)
        self.invoke.assert_not_awaited()

    def test_readiness_state_is_optional_and_missing_runtime_rows_still_report_all_25(self):
        self.status.return_value = [dict(entry, configured=False, enabled=False)
                                    for entry in SOURCE_MODEL_CATALOG[:-1]]
        result = self.run_check()
        self.assertEqual(len(result["models"]), 25)
        self.assertFalse(result["models"][-1]["configured"])
        self.assertTrue(all("readiness_state" not in row for row in result["models"]))

    def test_missing_credentials_state_is_reported_without_invocation(self):
        self.status.return_value = [dict(entry, configured=False, enabled=False,
                                        readiness_state="missing_credentials")
                                    for entry in SOURCE_MODEL_CATALOG]
        code, output = self.run_cli(
            ["--invoke", "--model-key", SOURCE_MODEL_CATALOG[0]["model_key"]])
        self.assertEqual(code, 1)
        row = json.loads(output)["models"][0]
        self.assertEqual(row["readiness_state"], "missing_credentials")
        self.assertEqual(row["error_code"], "OPEN_MODEL_CREDENTIALS_UNAVAILABLE")
        self.assertFalse(row["live_call_attempted"])
        self.assertFalse(row["response_received"])
        self.invoke.assert_not_awaited()

    def test_response_received_is_distinct_from_correct_answer_and_raw_answer_is_hidden(self):
        self.invoke.return_value = "private-upstream-response"
        code, output = self.run_cli(["--invoke", "--model-key", SOURCE_MODEL_CATALOG[0]["model_key"]])
        self.assertEqual(code, 1)
        row = json.loads(output)["models"][0]
        self.assertTrue(row["response_received"])
        self.assertFalse(row["answer_matches_expected"])
        self.assertNotIn("answer", row)
        self.assert_private_values_absent(output)

    def test_model_errors_and_unexpected_errors_are_redacted(self):
        cases = (
            (OpenModelError("private-error-detail", "OPEN_MODEL_AUTHENTICATION_FAILED", 403),
             "OPEN_MODEL_AUTHENTICATION_FAILED"),
            (OpenModelError("private-error-detail", "https://private-deployment.invalid/private-api-secret", 500),
             "OPEN_MODEL_CHECK_FAILED"),
            (RuntimeError("private-error-detail private-api-secret private-upstream-response"),
             "OPEN_MODEL_CHECK_FAILED"),
        )
        for error, expected_code in cases:
            with self.subTest(error=type(error).__name__, code=expected_code):
                self.invoke.side_effect = error
                code, output = self.run_cli(
                    ["--invoke", "--model-key", SOURCE_MODEL_CATALOG[0]["model_key"]])
                self.assertEqual(code, 1)
                row = json.loads(output)["models"][0]
                self.assertEqual(row["error_code"], expected_code)
                self.assertFalse(row["response_received"])
                self.assertNotIn("answer_matches_expected", row)
                self.assert_private_values_absent(output)

    def test_configuration_failure_is_redacted_without_invocation(self):
        self.status.side_effect = OpenModelError(
            "private-error-detail private-api-secret", "OPEN_MODEL_CONFIG_INVALID", 503)
        code, output = self.run_cli([])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output), {"error_code": "OPEN_MODEL_CONFIG_INVALID"})
        self.assert_private_values_absent(output)
        self.invoke.assert_not_awaited()

    def test_invalid_owner_control_never_invokes(self):
        self.evaluate.side_effect = RuntimeError("private-error-detail")
        self.allowed.side_effect = ProviderHeld("private-error-detail")
        row = self.run_check([SOURCE_MODEL_CATALOG[0]["model_key"]], invoke=True)["models"][0]
        self.assertFalse(row["permitted"])
        self.assertFalse(row["live_call_attempted"])
        self.invoke.assert_not_awaited()

    def test_learning_reports_all_27_including_added_models_without_calls(self):
        self.status.return_value = [dict(entry, configured=True, enabled=True)
                                    for entry in permitted_model_catalog()]
        code, output = self.run_cli(["--route", "learning"])
        result = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(result["route"], "learning")
        keys = [row["model_key"] for row in result["models"]]
        self.assertEqual(keys, [entry["model_key"] for entry in permitted_model_catalog()])
        self.assertEqual(len(keys), 27)
        self.assertTrue({"Qwen3-32B", "DeepSeek-V3.2"} <= set(keys))
        self.status.assert_called_once_with(route="learning")
        self.invoke.assert_not_awaited()

    def test_all_27_explicit_learning_names_use_the_learning_route_once_each(self):
        entries = permitted_model_catalog()
        self.status.return_value = [dict(entry, configured=True, enabled=True) for entry in entries]
        keys = [entry["model_key"] for entry in entries]
        result = self.run_check(keys, invoke=True, route="learning")
        self.assertEqual(len(result["models"]), 27)
        self.assertTrue(all(row["answer_matches_expected"] for row in result["models"]))
        self.assertEqual([call.args[0] for call in self.invoke.await_args_list], keys)
        self.assertTrue(all(call.kwargs == {"max_tokens": 32, "route": "learning"}
                            for call in self.invoke.await_args_list))
        self.assertEqual(self.allowed.call_count, 27)
        self.assert_private_values_absent(result)

    def test_learning_never_uses_legacy_bindings_or_mistakes_listing_for_connection(self):
        self.status.side_effect = catalog_status
        keys = [entry["model_key"] for entry in permitted_model_catalog()]
        with patch.dict(os.environ, {
            "EDURECOVER_OPEN_MODELS_ENABLED": "true",
            "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": "{invalid-legacy-settings",
            "DEEPSEEK_API_KEY": "private-api-secret",
        }, clear=True):
            result = self.run_check(keys, invoke=True, route="learning")
        self.assertEqual(len(result["models"]), 27)
        attempted = {row["model_key"] for row in result["models"] if row["live_call_attempted"]}
        self.assertEqual(attempted, {"DeepSeek-V4.1-Flash", "DeepSeek-V4-Pro"})
        blocked = [row for row in result["models"] if not row["live_call_attempted"]]
        self.assertEqual(len(blocked), 25)
        self.assertTrue(all(row["error_code"] == "OPEN_MODEL_NOT_CONFIGURED" for row in blocked))
        self.assertEqual(self.invoke.await_count, 2)
        self.assertTrue(all(call.kwargs["route"] == "learning" for call in self.invoke.await_args_list))
        self.assert_private_values_absent(result)

    def test_learning_invoke_without_names_cannot_expand_to_27_requests(self):
        code, output = self.run_cli(["--route", "learning", "--invoke"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output), {"error_code": "INVALID_CHECK_REQUEST"})
        self.status.assert_not_called()
        self.invoke.assert_not_awaited()

    def test_learning_rejects_duplicates_and_unselected_aws_models(self):
        cases = [["Qwen3-32B", "Qwen3-32B"], ["qwen3-32b"], ["unknown-model"]]
        learning_keys = {entry["model_key"] for entry in permitted_model_catalog()}
        cases.extend([[entry["model_key"]] for entry in AWS_ADDITIONAL_MODEL_CATALOG
                      if entry["model_key"] not in learning_keys])
        for keys in cases:
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                self.run_check(keys, invoke=True, route="learning")
        self.status.assert_not_called()
        self.invoke.assert_not_awaited()

    def test_added_learning_names_cannot_leak_into_default_route(self):
        for key in ("Qwen3-32B", "DeepSeek-V3.2"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.run_check([key], invoke=True)
        self.status.assert_not_called()
        self.invoke.assert_not_awaited()

    def test_unsupported_route_is_rejected_before_status_or_invocation(self):
        with self.assertRaises(ValueError):
            self.run_check(["Qwen3-32B"], invoke=True, route="unreviewed-provider")
        self.status.assert_not_called()
        self.invoke.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()


