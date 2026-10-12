"""Bedrock routing and privacy regressions; no network calls are performed."""

import json
import os
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

from app import main
from app.services import auto_generator as generation
from app.services import bedrock_provider as bedrock
from app.services import provider_verifier as verification
from app.services import ranked_candidate_generator as candidate_generation


CLAUDE_PROFILE = "global.anthropic.test-private-model"
OPENAI_PROFILE = "global.openai.test-private-model"


def control_client(mode="none", *, vendor="anthropic", model_name="test-private-model"):
    return SimpleNamespace(
        get_account_data_retention=Mock(return_value={"mode": mode}),
        get_inference_profile=Mock(return_value={"status": "ACTIVE", "models": [{
            "modelArn": f"arn:aws:bedrock:us-east-1::foundation-model/{vendor}.{model_name}",
        }]}),
        close=Mock(),
    )


def runtime_client(text):
    return SimpleNamespace(converse=Mock(return_value={"output": {"message": {"content": [
        {"reasoningContent": {"reasoningText": {"text": "private-reasoning"}}}, {"text": text},
    ]}}}), close=Mock())


class BedrockSettingsMixin:
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for key, value in {
            "provider_transport": "bedrock", "aws_region": "ap-northeast-2",
            "bedrock_claude_inference_profile": CLAUDE_PROFILE,
            "bedrock_openai_inference_profile": OPENAI_PROFILE,
            "anthropic_api_key": None, "openai_api_key": None,
        }.items():
            self.stack.enter_context(patch.object(bedrock.settings, key, value))
        self.stack.enter_context(patch.dict(os.environ, {
            "EDURECOVER_REQUIRE_ZDR": "false",
            "EDURECOVER_OPENAI_ZDR_CONFIRMED": "false",
            "EDURECOVER_OPENAI_ZDR_BASIS": "",
            "EDURECOVER_CLAUDE_ZDR_CONFIRMED": "false",
            "EDURECOVER_CLAUDE_ZDR_BASIS": "",
        }))


class BedrockPrivacyTests(BedrockSettingsMixin, unittest.IsolatedAsyncioTestCase):
    async def test_profile_checked_before_runtime_without_mandatory_retention_check(self):
        events = []
        control = control_client()
        runtime = runtime_client('{"verdict":"PASS","reasons":[]}')
        control.get_account_data_retention.side_effect = lambda: events.append("retention") or {"mode": "none"}
        control.get_inference_profile.side_effect = lambda **kw: events.append("profile") or {
            "status": "ACTIVE", "models": [{"modelArn": "arn:aws:bedrock:us-east-1::foundation-model/anthropic.test"}],
        }
        runtime.converse.side_effect = lambda **kw: events.append("invoke") or {
            "output": {"message": {"content": [{"text": "answer"}]}}}
        with patch.object(bedrock, "_client", side_effect=[control, runtime]) as factory:
            text, profile = await bedrock.converse_text("claude", "instructions", "prompt", max_tokens=1200)
        self.assertEqual(events, ["profile", "invoke"])
        control.get_account_data_retention.assert_not_called()
        self.assertEqual(text, "answer")
        self.assertEqual(profile, CLAUDE_PROFILE)
        self.assertEqual(factory.call_args_list[0].args, ("bedrock", "ap-northeast-2"))
        self.assertEqual(factory.call_args_list[1].args, ("bedrock-runtime", "ap-northeast-2"))
        self.assertEqual(runtime.converse.call_args.kwargs["modelId"], CLAUDE_PROFILE)
        self.assertEqual(runtime.converse.call_args.kwargs["inferenceConfig"], {"maxTokens": 1200})
        self.assertNotIn("additionalModelRequestFields", runtime.converse.call_args.kwargs)
        control.close.assert_called_once()
        runtime.close.assert_called_once()

    async def test_non_none_missing_or_unreadable_retention_does_not_block_runtime(self):
        for mode in ("default", "inherit", "aws_review", "provider_data_share", None):
            control = control_client(mode)
            runtime = runtime_client("ok")
            with self.subTest(mode=mode), patch.object(bedrock, "_client", side_effect=[control, runtime]) as factory:
                text, _ = await bedrock.converse_text("claude", "s", "p", max_tokens=1)
                self.assertEqual(text, "ok")
                self.assertEqual(factory.call_count, 2)
                control.get_account_data_retention.assert_not_called()
                control.get_inference_profile.assert_called_once()
        control = control_client()
        control.get_account_data_retention.side_effect = RuntimeError("private-body-and-key")
        runtime = runtime_client("ok")
        with patch.object(bedrock, "_client", side_effect=[control, runtime]):
            self.assertEqual((await bedrock.converse_text("claude", "s", "p", max_tokens=1))[0], "ok")
        control.get_account_data_retention.assert_not_called()

    async def test_sdk_without_retention_operation_can_invoke(self):
        control = control_client()
        del control.get_account_data_retention
        runtime = runtime_client("ok")
        with patch.object(bedrock, "_client", side_effect=[control, runtime]):
            self.assertEqual((await bedrock.converse_text("claude", "s", "p", max_tokens=1))[0], "ok")

    async def test_explicit_future_opt_in_still_requires_recorded_approval(self):
        from app.services.privacy_guard import ProviderZDRHeld
        with patch.dict(os.environ, {"EDURECOVER_REQUIRE_ZDR": "true", "EDURECOVER_CLAUDE_ZDR_CONFIRMED": "false"}), \
                patch.object(bedrock, "_client") as factory:
            with self.assertRaises(ProviderZDRHeld):
                await bedrock.converse_text("claude", "s", "p", max_tokens=1)
        factory.assert_not_called()

    async def test_base_model_id_and_foundation_arn_cannot_be_invoked(self):
        for profile in (None, "anthropic.claude-opus-5-5", "arn:aws:bedrock:ap-northeast-2::foundation-model/anthropic.claude-opus-5-5"):
            with self.subTest(profile=profile), patch.object(bedrock.settings, "bedrock_claude_inference_profile", profile), \
                    patch.object(bedrock, "_client") as factory:
                with self.assertRaises(bedrock.BedrockProviderError) as caught:
                    await bedrock.converse_text("claude", "s", "p", max_tokens=1)
                self.assertEqual(caught.exception.code, "BEDROCK_INFERENCE_PROFILE_NOT_CONFIGURED")
                factory.assert_not_called()

    async def test_inactive_wrong_vendor_and_luna_application_profiles_are_held(self):
        cases = [("claude", {"status": "CREATING", "models": []}, "BEDROCK_PROFILE_NOT_ACTIVE"),
                 ("claude", {"status": "ACTIVE", "models": [{"modelArn": "arn:aws:bedrock:us-east-1::foundation-model/openai.test"}]}, "BEDROCK_PROFILE_PROVIDER_MISMATCH"),
                 ("openai", {"status": "ACTIVE", "models": [{"modelArn": "arn:aws:bedrock:us-east-1::foundation-model/openai.gpt-6-luna"}]}, "BEDROCK_MODEL_TASK_NOT_ALLOWED")]
        for provider, response, code in cases:
            control = control_client()
            control.get_inference_profile.return_value = response
            with self.subTest(code=code), patch.object(bedrock, "_client", return_value=control) as factory:
                with self.assertRaises(bedrock.BedrockProviderError) as caught:
                    await bedrock.converse_text(provider, "s", "p", max_tokens=1)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(factory.call_count, 1)

    async def test_runtime_failure_is_bounded_and_no_retry_or_fallback(self):
        control = control_client()
        runtime = runtime_client("unused")
        error = RuntimeError("private-profile-and-source")
        error.response = {"Error": {"Code": "ValidationException", "Message": "private-profile-and-source"}}
        runtime.converse.side_effect = error
        with patch.object(bedrock, "_client", side_effect=[control, runtime]), \
                patch.object(generation, "AsyncAnthropic") as direct:
            with self.assertRaises(generation.GenerationFailed) as caught:
                await generation._generate_with_anthropic("private-source", 1)
        self.assertEqual(caught.exception.code, "BEDROCK_MODEL_OR_POLICY_UNAVAILABLE")
        self.assertNotIn("private-profile-and-source", str(caught.exception))
        direct.assert_not_called()
        runtime.converse.assert_called_once()

    async def test_each_request_checks_profile_and_does_not_mutate_retention(self):
        first = control_client("none")
        second = control_client("default")
        first.put_account_data_retention = Mock()
        second.put_account_data_retention = Mock()
        runtime = runtime_client("ok")
        with patch.object(bedrock, "_client", side_effect=[first, runtime, second, runtime]) as factory:
            self.assertEqual((await bedrock.converse_text("claude", "s", "p", max_tokens=1))[0], "ok")
            self.assertEqual((await bedrock.converse_text("claude", "s", "p", max_tokens=1))[0], "ok")
        self.assertEqual(factory.call_count, 4)
        for control in (first, second):
            control.get_account_data_retention.assert_not_called()
            control.put_account_data_retention.assert_not_called()
            control.get_inference_profile.assert_called_once()


class BedrockIntegrationTests(BedrockSettingsMixin, unittest.IsolatedAsyncioTestCase):
    async def test_generation_and_verification_use_bedrock_without_direct_keys(self):
        items = {"items": [{"question": "Q", "answer": "A", "explanation": "Reason"}]}
        for provider, runner in (("openai", generation._generate_with_openai), ("claude", generation._generate_with_anthropic)):
            with self.subTest(provider=provider), patch.object(bedrock, "converse_text", new_callable=AsyncMock,
                    return_value=(json.dumps(items), "private-profile")) as call, \
                    patch.object(generation, "AsyncOpenAI") as openai, patch.object(generation, "AsyncAnthropic") as anthropic:
                result = await runner("supplied-source", 1)
                self.assertEqual(result.to_dict()["provider"], provider)
                self.assertNotIn("private-profile", json.dumps(result.to_dict()))
                call.assert_awaited_once()
                openai.assert_not_called()
                anthropic.assert_not_called()
            with patch.object(bedrock, "converse_text", new_callable=AsyncMock,
                    return_value=('{"verdict":"PASS","reasons":[]}', "private-profile")) as call, \
                    patch.object(verification, "AsyncOpenAI") as openai, patch.object(verification, "AsyncAnthropic") as anthropic:
                result = await verification._verify_payload(provider, [{"question": "Q"}], "p")
                self.assertEqual(result, {"provider": provider, "verdict": "PASS", "reasons": []})
                openai.assert_not_called()
                anthropic.assert_not_called()

    async def test_invalid_transport_does_not_fall_through_to_direct_api(self):
        with patch.object(bedrock.settings, "provider_transport", "bedrok"), \
                patch.object(generation, "AsyncAnthropic") as direct:
            with self.assertRaises(generation.GenerationFailed) as caught:
                await generation._generate_with_anthropic("p", 1)
        self.assertEqual(caught.exception.code, "PROVIDER_TRANSPORT_CONFIGURATION_INVALID")
        direct.assert_not_called()

    async def test_candidate_generation_uses_bedrock_and_omits_profile_from_receipt(self):
        req = candidate_generation.CandidateGenerationRequest(
            provider="claude", explicit_authorization=True, context_ref="test", source_text="source", task_text="task", count=1,
        )
        environment = SimpleNamespace(
            generator=generation,
            assert_provider_allowed=Mock(return_value=SimpleNamespace(control_epoch="test")),
            assert_provider_zdr=Mock(), held_errors=(),
        )
        output = json.dumps({"candidates": [{"text": "answer", "self_reported_rank": 1, "evidence_refs": ["source"]}]})
        with patch.object(candidate_generation, "runtime_environment", return_value=environment), \
                patch.object(generation, "build_authoring_context", return_value={}), \
                patch.object(bedrock, "converse_text", new_callable=AsyncMock,
                             return_value=(output, "private-profile")), patch.object(generation, "AsyncAnthropic") as direct:
            candidates, receipt = await candidate_generation.generate_candidates(req)
        self.assertEqual(len(candidates), 1)
        self.assertNotIn("model", receipt)
        self.assertNotIn("private-profile", json.dumps(receipt))
        direct.assert_not_called()


class BedrockReadinessTests(BedrockSettingsMixin, unittest.TestCase):
    def test_disabled_zdr_enforcement_does_not_hide_configured_provider(self):
        with patch.object(main, "evaluate_provider", return_value=SimpleNamespace(allowed=True)):
            self.assertTrue(main._provider_ready_for_teacher("claude"))
            with patch.dict(os.environ, {"EDURECOVER_REQUIRE_ZDR": "true"}):
                self.assertFalse(main._provider_ready_for_teacher("claude"))

    def test_iam_role_configuration_does_not_require_direct_provider_secrets(self):
        self.assertTrue(main._provider_secret_configured("claude"))
        self.assertTrue(main._provider_secret_configured("openai"))
        with patch.object(bedrock.settings, "bedrock_claude_inference_profile", None):
            self.assertFalse(main._provider_secret_configured("claude"))
        with patch.object(bedrock.settings, "provider_transport", "invalid"):
            self.assertFalse(main._provider_secret_configured("openai"))
        with patch.object(bedrock.settings, "aws_region", "invalid"):
            self.assertFalse(main._provider_secret_configured("claude"))

    def test_public_status_never_discloses_profiles_or_claims_live_success(self):
        with TestClient(main.app, base_url="https://testserver") as client:
            response = client.get("/ai-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["readiness_basis"], "configuration_only_not_live_provider_success")
        self.assertNotIn(CLAUDE_PROFILE, response.text)
        self.assertNotIn(OPENAI_PROFILE, response.text)

    def test_boto_client_uses_sdk_credentials_and_no_automatic_retry(self):
        import boto3
        with patch.object(boto3, "client", return_value=object()) as factory:
            bedrock._client("bedrock-runtime", "ap-northeast-2")
        self.assertEqual(set(factory.call_args.kwargs), {"region_name", "config"})
        self.assertEqual(factory.call_args.kwargs["config"].retries["total_max_attempts"], 1)


if __name__ == "__main__":
    unittest.main()
