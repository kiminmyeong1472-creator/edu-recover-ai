"""Trust Chain uses Bedrock exclusively when the AWS transport is selected."""

import json
import os
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import SecretStr

from app.services import bedrock_provider as bedrock
from app.services import trust_chain as chain
from app.services.control_plane import ProviderHeld


class TrustChainTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for key, value in {"provider_transport": "bedrock", "openai_api_key": None,
                           "anthropic_api_key": None}.items():
            self.stack.enter_context(patch.object(chain.settings, key, value))
        self.stack.enter_context(patch.dict(os.environ, {
            "EDURECOVER_REQUIRE_ZDR": "false", "EDURECOVER_OPENAI_ZDR_CONFIRMED": "false",
            "EDURECOVER_CLAUDE_ZDR_CONFIRMED": "false", "EDURECOVER_OPENAI_ZDR_BASIS": "",
            "EDURECOVER_CLAUDE_ZDR_BASIS": "",
        }))
        self.problems = [{"question": "Q", "answer": "A", "passage": "source", "explanation": "reason"}]
        self.runners = (("openai", "provider-validator-A", chain._validate_with_openai),
                        ("claude", "provider-validator-B", chain._validate_with_anthropic))

    async def test_both_workers_use_bedrock_without_direct_keys_and_keep_private_names(self):
        for provider, worker, runner in self.runners:
            with self.subTest(provider=provider), patch.object(chain, "assert_provider_allowed") as allowed, \
                    patch.object(bedrock, "converse_text", new_callable=AsyncMock,
                                 return_value=('{"verdict":"PASS","reasons":[]}', "private-profile")) as call, \
                    patch.object(chain, "AsyncOpenAI") as openai, patch.object(chain, "AsyncAnthropic") as anthropic:
                result = await runner(self.problems)
                self.assertEqual(result.worker, worker)
                self.assertEqual(result.verdict, chain.WorkerVerdict.PASS)
                self.assertEqual(result.reasons, ())
                allowed.assert_called_once_with(provider, "verify")
                call.assert_awaited_once_with(provider, chain.VALIDATION_INSTRUCTIONS,
                                             chain._serialize_problems(self.problems), max_tokens=1200)
                self.assertNotIn("private-profile", str(result))
                openai.assert_not_called()
                anthropic.assert_not_called()

    async def test_bedrock_failure_and_invalid_transport_never_fall_back_despite_direct_keys(self):
        with patch.object(chain.settings, "openai_api_key", SecretStr("test-only-direct-key")), \
                patch.object(chain.settings, "anthropic_api_key", SecretStr("test-only-direct-key")):
            for provider, worker, runner in self.runners:
                with self.subTest(provider=provider), patch.object(chain, "assert_provider_allowed"), \
                        patch.object(bedrock, "converse_text", new_callable=AsyncMock,
                                     side_effect=RuntimeError("private-source-and-profile")), \
                        patch.object(chain, "AsyncOpenAI") as openai, patch.object(chain, "AsyncAnthropic") as anthropic:
                    result = await runner(self.problems)
                    self.assertEqual(result.worker, worker)
                    self.assertEqual(result.verdict, chain.WorkerVerdict.FAIL)
                    self.assertIn("RuntimeError", result.reasons[0])
                    self.assertNotIn("private-source-and-profile", result.reasons[0])
                    openai.assert_not_called()
                    anthropic.assert_not_called()
                with patch.object(chain.settings, "provider_transport", "bedrok"), \
                        patch.object(chain, "assert_provider_allowed"), \
                        patch.object(chain, "AsyncOpenAI") as openai, patch.object(chain, "AsyncAnthropic") as anthropic:
                    result = await runner(self.problems)
                    self.assertEqual(result.verdict, chain.WorkerVerdict.FAIL)
                    self.assertIn("BedrockProviderError", result.reasons[0])
                    openai.assert_not_called()
                    anthropic.assert_not_called()

    async def test_provider_verify_gate_and_future_privacy_opt_in_block_before_transport(self):
        for provider, _, runner in self.runners:
            with self.subTest(provider=provider), patch.object(chain, "assert_provider_allowed", side_effect=ProviderHeld("safe-hold")), \
                    patch.object(bedrock, "converse_text", new_callable=AsyncMock) as call, \
                    patch.object(chain, "AsyncOpenAI") as openai, patch.object(chain, "AsyncAnthropic") as anthropic:
                result = await runner(self.problems)
                self.assertEqual(result.verdict, chain.WorkerVerdict.FAIL)
                self.assertEqual(result.reasons, ("safe-hold",))
                call.assert_not_awaited()
                openai.assert_not_called()
                anthropic.assert_not_called()
            with patch.dict(os.environ, {"EDURECOVER_REQUIRE_ZDR": "true"}), \
                    patch.object(chain, "assert_provider_allowed"), \
                    patch.object(bedrock, "converse_text", new_callable=AsyncMock) as call:
                result = await runner(self.problems)
                self.assertEqual(result.verdict, chain.WorkerVerdict.FAIL)
                call.assert_not_awaited()

    async def test_direct_mode_preserves_original_sdk_and_client_cleanup(self):
        openai_client = SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(output_text='{"verdict":"PASS","reasons":[]}'))),
            close=AsyncMock(),
        )
        claude_client = SimpleNamespace(
            messages=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(content=[
                SimpleNamespace(type="text", text='{"verdict":"PASS","reasons":[]}')]))), close=AsyncMock(),
        )
        with patch.object(chain.settings, "provider_transport", "direct"), \
                patch.object(chain.settings, "openai_api_key", SecretStr("test-only-key")), \
                patch.object(chain.settings, "anthropic_api_key", SecretStr("test-only-key")), \
                patch.object(chain, "assert_provider_allowed"), \
                patch.object(chain, "AsyncOpenAI", return_value=openai_client) as openai, \
                patch.object(chain, "AsyncAnthropic", return_value=claude_client) as anthropic, \
                patch.object(bedrock, "converse_text", new_callable=AsyncMock) as call:
            first = await chain._validate_with_openai(self.problems)
            second = await chain._validate_with_anthropic(self.problems)
        self.assertEqual((first.verdict, second.verdict), (chain.WorkerVerdict.PASS, chain.WorkerVerdict.PASS))
        openai.assert_called_once()
        anthropic.assert_called_once()
        openai_client.close.assert_awaited_once()
        claude_client.close.assert_awaited_once()
        self.assertIs(openai_client.responses.create.await_args.kwargs["store"], False)
        call.assert_not_awaited()

    async def test_malformed_bedrock_response_keeps_existing_revalidation_behavior(self):
        async def reply(provider, *args, **kwargs):
            return ('{"verdict":"PASS","reasons":[]}' if provider == "openai" else "not-json", "private-profile")
        with patch.object(chain, "assert_provider_allowed"), \
                patch.object(bedrock, "converse_text", side_effect=reply):
            result = await chain.run_trust_chain(self.problems)
        self.assertEqual(result.status, chain.TrustChainStatus.REJECTED_REVALIDATION_PENDING)
        self.assertFalse(result.unanimous)
        self.assertFalse(result.majority_rule_used)
        self.assertEqual([r.worker for r in result.worker_results], ["provider-validator-A", "provider-validator-B"])
        self.assertEqual(result.worker_results[1].reasons, ("Provider returned a non-JSON validation response.",))
        self.assertNotIn("private-profile", json.dumps(result.to_dict()))


if __name__ == "__main__":
    unittest.main()
