"""AWS ZDR preflight checks using fake clients only; no AWS credentials needed."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "aws_zdr_preflight.py"
SPEC = importlib.util.spec_from_file_location("aws_zdr_preflight", SCRIPT)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)

REGION = "ap-northeast-2"
ACCOUNT = "123456789012"
PROFILE = "global.anthropic.claude-opus-5-5"


class FakeAwsError(Exception):
    def __init__(self):
        super().__init__("Do not expose secret-token or private model ARN")
        self.response = {
            "Error": {"Code": "AccessDeniedException", "Message": "secret-token"},
            "ResponseMetadata": {"RequestId": "request-123", "HTTPHeaders": {"Authorization": "secret-token"}},
        }


class FakeSts:
    def __init__(self, account=ACCOUNT, region=REGION):
        self.meta = SimpleNamespace(region_name=region)
        self.account = account
        self.calls = []

    def get_caller_identity(self):
        self.calls.append("GetCallerIdentity")
        return {"Account": self.account, "Arn": "private-identity", "ResponseMetadata": {"RequestId": "sts-request"}}


class FakeBedrock:
    def __init__(self, mode="none", region=REGION, profile_region=REGION, profile_account=ACCOUNT, status="ACTIVE"):
        self.meta = SimpleNamespace(region_name=region)
        self.mode, self.profile_region, self.profile_account, self.status = mode, profile_region, profile_account, status
        self.calls = []
        self.failure = None

    def get_account_data_retention(self):
        self.calls.append("GetAccountDataRetention")
        if self.failure:
            raise self.failure
        return {"mode": self.mode, "ResponseMetadata": {"RequestId": "retention-request"}}

    def get_inference_profile(self, **kwargs):
        self.calls.append(("GetInferenceProfile", kwargs))
        return {
            "inferenceProfileArn": f"arn:aws:bedrock:{self.profile_region}:{self.profile_account}:inference-profile/{PROFILE}",
            "inferenceProfileId": PROFILE,
            "status": self.status,
            "models": [{"modelArn": "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-opus-5-5"}],
        }


def run(sts=None, bedrock=None, **kwargs):
    return preflight.run_preflight(sts or FakeSts(), bedrock or FakeBedrock(), region=REGION, expected_account_id=ACCOUNT, inference_profiles=[PROFILE], **kwargs)


class AwsZdrPreflightTests(unittest.TestCase):
    def test_none_is_informational_without_model_policy_verification(self):
        sts, bedrock = FakeSts(), FakeBedrock()
        report = run(sts, bedrock)
        self.assertEqual(report["status"], "INFORMATIONAL_READ_COMPLETE")
        self.assertIn("makes no model-block or approval decision", report["reportNote"])
        self.assertIn("0 means requested reads completed", report["exitCodeMeaning"])
        self.assertEqual(report["model_specific_zdr_basis"], "UNCHECKED")
        self.assertEqual(report["account_data_retention_mode"], "none")
        self.assertEqual(report["reasonCodes"], [])
        self.assertEqual(sts.calls, ["GetCallerIdentity"])
        self.assertEqual(bedrock.calls, ["GetAccountDataRetention", ("GetInferenceProfile", {"inferenceProfileIdentifier": PROFILE})])

    def test_all_documented_retention_modes_are_reported_without_a_gate(self):
        for mode in ("none", "inherit", "default", "aws_review", "provider_data_share"):
            with self.subTest(mode=mode):
                bedrock = FakeBedrock(mode=mode)
                report = run(bedrock=bedrock)
                self.assertEqual(report["status"], "INFORMATIONAL_READ_COMPLETE")
                self.assertEqual(report["account_data_retention_mode"], mode)
                self.assertEqual(report["reasonCodes"], [])
                self.assertEqual(len(bedrock.calls), 2)

    def test_wrong_account_stops_before_bedrock_calls(self):
        bedrock = FakeBedrock()
        report = run(FakeSts(account="999999999999"), bedrock)
        self.assertIn("ACCOUNT_MISMATCH", report["reasonCodes"])
        self.assertEqual(bedrock.calls, [])

    def test_wrong_client_region_stops_before_any_api_call(self):
        sts, bedrock = FakeSts(), FakeBedrock(region="us-east-1")
        report = run(sts, bedrock)
        self.assertIn("CLIENT_REGION_MISMATCH", report["reasonCodes"])
        self.assertEqual(sts.calls, [])
        self.assertEqual(bedrock.calls, [])

    def test_missing_sdk_method_requests_upgrade_without_api_calls(self):
        sts = FakeSts()
        old_sdk = SimpleNamespace(meta=SimpleNamespace(region_name=REGION), get_inference_profile=lambda **kwargs: {})
        report = run(sts, old_sdk)
        self.assertIn("SDK_OPERATION_UNAVAILABLE", report["reasonCodes"])
        self.assertIn("requirements-aws.txt", report["sdkAction"])
        self.assertEqual(sts.calls, [])

    def test_profile_scope_mismatch_is_a_read_verification_error(self):
        for kwargs in ({"profile_region": "us-east-1"}, {"profile_account": "999999999999"}):
            with self.subTest(kwargs=kwargs):
                report = run(bedrock=FakeBedrock(**kwargs))
                self.assertEqual(report["status"], "INFORMATIONAL_READ_INCOMPLETE")
                self.assertIn("PROFILE_ACCOUNT_OR_REGION_MISMATCH", report["reasonCodes"])

    def test_aws_errors_output_only_code_and_request_id(self):
        bedrock = FakeBedrock()
        bedrock.failure = FakeAwsError()
        report = run(bedrock=bedrock)
        self.assertEqual(report["retentionCheck"], {"errorCode": "AccessDeniedException", "requestId": "request-123"})
        self.assertNotIn("secret-token", json.dumps(report))

    def test_private_identifiers_and_raw_response_are_not_echoed(self):
        encoded = json.dumps(run())
        self.assertNotIn(ACCOUNT, encoded)
        self.assertNotIn(PROFILE, encoded)
        self.assertNotIn("private-identity", encoded)
        self.assertNotIn("modelArn", encoded)

    def test_cli_environment_region_mismatch_never_creates_clients(self):
        with patch.dict("os.environ", {"AWS_REGION": REGION}), patch("builtins.print") as output:
            exit_code = preflight.main(["--region", "us-east-1", "--expected-account-id", ACCOUNT, "--inference-profile", PROFILE])
        self.assertEqual(exit_code, preflight.EXIT_READ_ERROR)
        self.assertIn("ENVIRONMENT_REGION_MISMATCH", json.loads(output.call_args.args[0])["reasonCodes"])

    def test_invalid_configuration_is_rejected_without_sdk_setup(self):
        with patch.dict("os.environ", {"AWS_REGION": REGION}), patch("builtins.print") as output:
            exit_code = preflight.main(["--expected-account-id", "not-an-account", "--inference-profile", PROFILE])
        self.assertEqual(exit_code, preflight.EXIT_READ_ERROR)
        self.assertEqual(json.loads(output.call_args.args[0])["reasonCodes"], ["INVALID_CONFIGURATION"])

    def test_cli_uses_fake_sdk_with_one_attempt_and_only_read_operations(self):
        sts, bedrock = FakeSts(), FakeBedrock()
        config_calls = []
        client_calls = []

        def config_factory(**kwargs):
            config_calls.append(kwargs)
            return kwargs

        def client_factory(service, **kwargs):
            client_calls.append((service, kwargs["region_name"]))
            return {"sts": sts, "bedrock": bedrock}[service]

        fake_modules = {
            "boto3": SimpleNamespace(client=client_factory),
            "botocore.config": SimpleNamespace(Config=config_factory),
        }
        with patch.dict("os.environ", {"AWS_REGION": REGION}), patch.dict("sys.modules", fake_modules), patch("builtins.print") as output:
            exit_code = preflight.main(["--expected-account-id", ACCOUNT, "--inference-profile", PROFILE])
        report = json.loads(output.call_args.args[0])
        self.assertEqual(exit_code, preflight.EXIT_READ_COMPLETE)
        self.assertEqual(report["status"], "INFORMATIONAL_READ_COMPLETE")
        self.assertEqual(client_calls, [("sts", REGION), ("bedrock", REGION)])
        self.assertEqual(config_calls[0]["retries"], {"mode": "standard", "total_max_attempts": 1})
        self.assertEqual(sts.calls, ["GetCallerIdentity"])
        self.assertEqual(len(bedrock.calls), 2)

    def test_own_ai_metadata_does_not_create_a_block_or_approval(self):
        bedrock = FakeBedrock()
        original = bedrock.get_inference_profile

        def own_ai_profile(**kwargs):
            response = original(**kwargs)
            response["models"] = [{"modelArn": "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-lite-v1:0"}]
            return response

        bedrock.get_inference_profile = own_ai_profile
        report = run(bedrock=bedrock)
        self.assertEqual(report["status"], "INFORMATIONAL_READ_COMPLETE")
        self.assertEqual(report["reasonCodes"], [])
        self.assertEqual(report["model_specific_zdr_basis"], "UNCHECKED")
        self.assertNotIn("amazon.nova", json.dumps(report))

    def test_inactive_profile_is_reported_without_a_model_block(self):
        report = run(bedrock=FakeBedrock(status="UNAVAILABLE"))
        self.assertEqual(report["status"], "INFORMATIONAL_READ_COMPLETE")
        self.assertFalse(report["profileChecks"][0]["active"])
        self.assertEqual(report["reasonCodes"], [])

    def test_unrecognized_retention_response_is_a_read_error(self):
        for mode in ("HOLD", None):
            with self.subTest(mode=mode):
                report = run(bedrock=FakeBedrock(mode=mode))
                self.assertEqual(report["status"], "INFORMATIONAL_READ_INCOMPLETE")
                self.assertEqual(report["account_data_retention_mode"], "UNKNOWN")
                self.assertIn("RETENTION_RESPONSE_UNRECOGNIZED", report["reasonCodes"])


if __name__ == "__main__":
    unittest.main()
