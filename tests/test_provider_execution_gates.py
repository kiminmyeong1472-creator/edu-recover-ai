"""Network-free regression tests for owner-authenticated, isolated AI calls."""
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import main
from app.services import auto_generator as generation, provider_verifier as verification
from app.services.control_plane import assert_provider_allowed, ProviderHeld
from app.services.privacy_guard import assert_provider_zdr, ProviderZDRHeld, privacy_status
from app.services.trust_chain import _parse_worker_response, WorkerVerdict
from app.services.generation_provenance import (
    issue_origin_proof, verify_origin_proof, verify_verification_proof, ProvenanceError,
)


class ProviderExecutionTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'EDURECOVER_OWNER_USER': 'test-owner',
            'EDURECOVER_OWNER_USER ': '',
            'EDURECOVER_OWNER_PASSWORD': 'only-test-password',
            'EDURECOVER_ALLOWED_PROVIDERS': 'openai,gemini,claude',
            'EDURECOVER_REQUIRE_EXPLICIT_PROVIDER_ALLOW': 'true',
            'EDURECOVER_CONTROL_MANIFEST_JSON': '{}',
            'EDURECOVER_AI_HOLD': 'false',
            'EDURECOVER_BLOCKED_PROVIDERS': '',
            'EDURECOVER_RESTRICTED_PROVIDERS': '',
            'EDURECOVER_REQUIRE_ZDR': 'true',
            'EDURECOVER_OPENAI_ZDR_CONFIRMED': 'false',
            'EDURECOVER_OPENAI_ZDR_BASIS': '',
        })
        self.env.start()
        self.client = TestClient(main.app, base_url='https://testserver')
        self.credentials = ('test-owner', 'only-test-password')
        self.payload = {'provider': 'openai', 'explicit_authorization': True,
                        'source_text': 'Test source.', 'count': 1}
        self.problems = [{'problem_id': 1, 'question': 'Q', 'answer': 'A',
                          'explanation': 'Reason', 'passage': 'Source'}]

    def tearDown(self):
        self.client.close()
        self.env.stop()

    def test_all_provider_execution_routes_require_login_before_calling(self):
        with patch.object(main, 'generate_questions', new_callable=AsyncMock) as gen, \
                patch.object(main, 'verify_with_provider', new_callable=AsyncMock) as verify:
            for provider in ('openai', 'gemini', 'claude'):
                for action in ('generate', 'verify'):
                    response = self.client.post(f'/channel/{provider}/{action}', json={})
                    self.assertEqual(response.status_code, 401)
            self.assertEqual(self.client.post('/verify-problem', json={'problems': self.problems}).status_code, 401)
            gen.assert_not_awaited()
            verify.assert_not_awaited()

    def test_explicit_provider_and_authorization_are_required(self):
        with patch.object(main, 'generate_questions', new_callable=AsyncMock) as gen:
            payload = dict(self.payload, provider='gemini')
            self.assertEqual(self.client.post('/channel/openai/generate', json=payload, auth=self.credentials).status_code, 400)
            payload = dict(self.payload, explicit_authorization=False)
            self.assertEqual(self.client.post('/channel/openai/generate', json=payload, auth=self.credentials).status_code, 422)
            gen.assert_not_awaited()

    def test_openai_auth_failure_never_calls_gemini_or_permanently_holds_retry(self):
        failure = generation.GenerationFailed('safe message', code='PROVIDER_AUTHENTICATION_FAILED')
        good = generation.GenerationResult('openai', 'test-model', tuple(self.problems))
        with patch.object(main, 'generate_questions', new_callable=AsyncMock,
                          side_effect=[failure, good]) as gen:
            failed = self.client.post('/channel/openai/generate', json=self.payload, auth=self.credentials)
            self.assertEqual(failed.status_code, 502)
            self.assertEqual(failed.headers['X-EduRecover-Error-Code'], 'PROVIDER_AUTHENTICATION_FAILED')
            retry = self.client.post('/channel/openai/generate', json=self.payload, auth=self.credentials)
            self.assertEqual(retry.status_code, 200)
            self.assertFalse(retry.json()['fallback_used'])
            self.assertEqual([call.kwargs['provider'] for call in gen.await_args_list], ['openai', 'openai'])

    def test_zdr_missing_or_bad_flag_never_reaches_sdk(self):
        for value in ('true', 'ture', ''):
            with self.subTest(value=value), patch.dict(os.environ, {'EDURECOVER_REQUIRE_ZDR': value}), \
                    patch.object(generation, 'AsyncOpenAI') as sdk:
                response = self.client.post('/channel/openai/generate', json=self.payload, auth=self.credentials)
                self.assertEqual(response.status_code, 423)
                sdk.assert_not_called()

    def test_same_generation_provider_cannot_cross_verify(self):
        with patch.object(main, 'verify_with_provider', new_callable=AsyncMock) as verify:
            response = self.client.post('/channel/openai/verify', auth=self.credentials, json={
                'provider': 'openai', 'origin_provider': 'openai',
                'origin_invocation_id': 'test-generation-id',
                'origin_proof': issue_origin_proof('openai', 'test-generation-id', self.problems),
                'explicit_authorization': True, 'problems': self.problems,
            })
            self.assertEqual(response.status_code, 409)
            verify.assert_not_awaited()

    def test_one_selected_verifier_receives_whole_batch(self):
        problems = self.problems * 3
        with patch.object(main, 'verify_with_provider', new_callable=AsyncMock,
                          return_value={'provider': 'gemini', 'verdict': 'PASS', 'reasons': []}) as verify:
            response = self.client.post('/channel/gemini/verify', auth=self.credentials, json={
                'provider': 'gemini', 'origin_provider': 'openai',
                'origin_invocation_id': 'test-generation-id',
                'origin_proof': issue_origin_proof('openai', 'test-generation-id', problems),
                'explicit_authorization': True, 'problems': problems,
            })
            self.assertEqual(response.status_code, 200)
            verify.assert_awaited_once_with('gemini', problems)

    def test_legacy_multi_provider_route_is_retired(self):
        response = self.client.post('/verify-problem', auth=self.credentials, json={'problems': self.problems})
        self.assertEqual(response.status_code, 410)

    def test_verify_only_provider_remains_a_verification_candidate(self):
        with patch.dict(os.environ, {'EDURECOVER_RESTRICTED_PROVIDERS': 'gemini',
                                     'EDURECOVER_REQUIRE_ZDR': 'false'}), \
                patch.object(main.settings, 'gemini_api_key', SecretStr('test-not-real')):
            row = self.client.get('/ai-status').json()['providers']['gemini']
            self.assertFalse(row['ready'])
            self.assertTrue(row['verify_ready'])

    def test_cookie_execution_requires_same_origin(self):
        response = self.client.post('/owner/login', headers={'Origin': 'https://testserver'},
                                    data={'username': 'test-owner', 'password': 'only-test-password'},
                                    follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        with patch.object(main, 'generate_questions', new_callable=AsyncMock) as gen:
            response = self.client.post('/channel/openai/generate', json=self.payload,
                                        headers={'Origin': 'https://other.example'})
            self.assertEqual(response.status_code, 403)
            gen.assert_not_awaited()

    def test_owner_diagnostics_stay_private_and_teacher_page_does_not_advertise_unrestricted_ai(self):
        response = self.client.get('/owner/ai-status', follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], '/owner/login')
        public = self.client.get('/teacher-free')
        self.assertEqual(public.status_code, 200)
        self.assertIn('관리자 로그인', public.text)


    def test_forged_origin_or_changed_items_never_reach_verifier(self):
        original = issue_origin_proof('openai', 'test-generation-id', self.problems)
        good = {'provider': 'gemini', 'origin_provider': 'openai',
                'origin_invocation_id': 'test-generation-id', 'origin_proof': original,
                'explicit_authorization': True, 'problems': self.problems}
        cases = [dict(good, origin_provider='claude'),
                 dict(good, origin_invocation_id='another-generation-id'),
                 dict(good, problems=[dict(self.problems[0], answer='changed')]),
                 dict(good, origin_proof=original[:-1] + ('0' if original[-1] != '0' else '1'))]
        with patch.object(main, 'verify_with_provider', new_callable=AsyncMock) as verify:
            for payload in cases:
                with self.subTest(payload_kind=list(payload)):
                    response = self.client.post('/channel/gemini/verify', auth=self.credentials, json=payload)
                    self.assertEqual(response.status_code, 409, response.text)
            missing = dict(good)
            del missing['origin_proof']
            self.assertEqual(self.client.post('/channel/gemini/verify', auth=self.credentials, json=missing).status_code, 422)
            verify.assert_not_awaited()

    def test_teacher_revision_preserves_real_origin_and_invalidates_previous_hash(self):
        proof = issue_origin_proof('openai', 'test-generation-id', self.problems)
        edited = [dict(self.problems[0], question='Teacher edited question')]
        response = self.client.post('/owner/authoring/revise', auth=self.credentials,
                                    json={'origin_proof': proof, 'problems': edited})
        self.assertEqual(response.status_code, 200, response.text)
        receipt = response.json()
        claims = verify_origin_proof(receipt['origin_proof'], provider='openai',
                                     invocation_id=receipt['invocation_id'], problems=edited)
        self.assertEqual(claims['generation_invocation_id'], 'test-generation-id')
        with self.assertRaises(ProvenanceError):
            verify_origin_proof(receipt['origin_proof'], problems=self.problems)
        with self.assertRaises(ProvenanceError):
            verify_origin_proof(proof, problems=edited)
        self.assertEqual(self.client.post('/owner/authoring/revise', json={'origin_proof': proof, 'problems': edited}).status_code, 401)

    def test_verification_proof_is_only_for_pass_and_binds_origin_and_current_items(self):
        proof = issue_origin_proof('openai', 'test-generation-id', self.problems)
        origin = verify_origin_proof(proof, problems=self.problems)
        payload = {'provider': 'gemini', 'origin_provider': 'openai',
                   'origin_invocation_id': 'test-generation-id', 'origin_proof': proof,
                   'explicit_authorization': True, 'problems': self.problems}
        for verdict in ('PASS', 'FAIL'):
            with patch.object(main, 'verify_with_provider', new_callable=AsyncMock,
                              return_value={'provider': 'gemini', 'verdict': verdict, 'reasons': []}):
                response = self.client.post('/channel/gemini/verify', auth=self.credentials, json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            receipt = response.json()['execution_receipt']
            if verdict == 'FAIL':
                self.assertNotIn('verification_proof', receipt)
                continue
            signed = receipt['verification_proof']
            checked = verify_verification_proof(signed, problems=self.problems, origin_claims=origin)
            self.assertEqual(checked['provider'], 'gemini')
            with self.assertRaises(ProvenanceError):
                verify_verification_proof(proof, problems=self.problems, origin_claims=origin)
            with self.assertRaises(ProvenanceError):
                verify_verification_proof(signed, problems=[dict(self.problems[0], answer='changed')], origin_claims=origin)
            # A PASS token cannot be rebound as though it were a generation token.
            self.assertEqual(self.client.post('/owner/authoring/revise', auth=self.credentials,
                             json={'origin_proof': signed, 'problems': self.problems}).status_code, 409)

    def test_origin_proof_expires_and_invalidates_when_owner_credentials_change(self):
        import app.services.generation_provenance as provenance
        proof = issue_origin_proof('openai', 'test-generation-id', self.problems)
        with patch.object(provenance.time, 'time', return_value=provenance.time.time() + provenance._TTL + 2):
            with self.assertRaises(ProvenanceError):
                verify_origin_proof(proof)
        with patch.dict(os.environ, {'EDURECOVER_OWNER_PASSWORD': 'changed-test-password'}):
            with self.assertRaises(ProvenanceError):
                verify_origin_proof(proof)


class FailClosedResponseTests(unittest.TestCase):
    def test_contradictory_or_invalid_verdict_cannot_pass(self):
        for payload in ('[]', 'null', '{"verdict":"PASS","reasons":["ambiguous"]}',
                        '{"verdict":"PASS","reasons":[{}]}', '{"verdict":"PASS","reasons":"bad"}'):
            with self.subTest(payload=payload):
                with self.assertRaises(generation.GenerationFailed):
                    verification._parse_result(payload)
                self.assertEqual(_parse_worker_response('test', payload).verdict, WorkerVerdict.FAIL)
        self.assertEqual(verification._parse_result('{"verdict":"PASS","reasons":[]}')['verdict'], 'PASS')

    def test_upstream_body_and_secrets_are_not_in_diagnostics_or_logs(self):
        exc = RuntimeError('private-source-and-secret')
        exc.code = 429
        exc.status = 'RESOURCE_EXHAUSTED'
        exc.message = 'private-source-and-secret'
        failure = generation.provider_failure('gemini', exc)
        self.assertEqual(failure.code, 'PROVIDER_QUOTA_OR_RATE_LIMIT')
        self.assertEqual(failure.http_status, 429)
        self.assertNotIn('private-source-and-secret', str(failure))
        with self.assertLogs(generation.logger, level='ERROR') as captured:
            generation._log_gemini_error('generation', exc)
        self.assertNotIn('private-source-and-secret', '\n'.join(captured.output))

    def test_verification_payload_omits_unrelated_metadata(self):
        with patch.object(verification, 'build_verification_context', return_value={}):
            text = verification._serialize([{'question': 'Q', 'answer': 'A', 'access_token': 'test-secret',
                                            'internal_logs': 'private-log', 'execution_receipt': {'proof': 'no'}}])
        self.assertNotIn('test-secret', text)
        self.assertNotIn('private-log', text)
        self.assertNotIn('execution_receipt', text)
        self.assertIn('question', text)

    def test_invalid_control_configuration_cannot_authorize_execution(self):
        for value in ({'allowed_providers': 'openai'}, {'require_explicit_provider_allow': 'false'},
                      {'hold_all_ai': None}, {'allowed_providers': ['openai', 1]}):
            with self.subTest(value=value), patch.dict(os.environ, {'EDURECOVER_CONTROL_MANIFEST_JSON': json.dumps(value)}):
                with self.assertRaises(ProviderHeld):
                    assert_provider_allowed('openai', 'generate')


class OpenAIVerificationFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_verification_401_is_classified_and_client_closed(self):
        error = RuntimeError('secret-and-body')
        error.status_code = 401
        client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=error)), close=AsyncMock())
        with patch.object(verification, 'assert_provider_zdr'), \
                patch.object(verification, '_serialize', return_value='minimal'), \
                patch.object(verification.settings, 'openai_api_key', SecretStr('test-only-key')), \
                patch.object(verification, 'AsyncOpenAI', return_value=client):
            with self.assertRaises(generation.GenerationFailed) as caught:
                await verification._verify_payload('openai', [{'question': 'q'}], 'minimal')
            self.assertEqual(caught.exception.code, 'PROVIDER_AUTHENTICATION_FAILED')
            self.assertNotIn('secret-and-body', str(caught.exception))
            self.assertIs(client.responses.create.await_args.kwargs['store'], False)
            client.close.assert_awaited_once()

class GeminiClientLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_quota_error_is_classified_and_async_client_closed(self):
        error = RuntimeError('do-not-log-source')
        error.code = 429
        error.status = 'RESOURCE_EXHAUSTED'
        client = SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=AsyncMock(side_effect=error)),
                                aclose=AsyncMock()), close=Mock())
        with patch.object(generation, 'assert_provider_zdr'), \
                patch.object(generation.settings, 'gemini_api_key', SecretStr('test-only-key')), \
                patch.object(generation.genai, 'Client', return_value=client):
            with self.assertRaises(generation.GenerationFailed) as caught:
                await generation._generate_with_gemini('test-source', 1)
            self.assertEqual(caught.exception.http_status, 429)
            self.assertEqual(caught.exception.code, 'PROVIDER_QUOTA_OR_RATE_LIMIT')
            client.aio.aclose.assert_awaited_once()
            client.close.assert_called_once()


class AgentPolicyTests(unittest.TestCase):
    def test_typo_does_not_allow_unknown_agent_or_block_untagged_owner(self):
        from app.services.agent_access_guard import evaluate_agent_access
        with patch.dict(os.environ, {'EDURECOVER_REQUIRE_EXPLICIT_AGENT_ALLOW': 'ture',
                                     'EDURECOVER_ALLOWED_AGENT_IDS': ''}):
            self.assertFalse(evaluate_agent_access('unapproved-agent').allowed)
            self.assertTrue(evaluate_agent_access(None).allowed)


if __name__ == "__main__":
    unittest.main()
