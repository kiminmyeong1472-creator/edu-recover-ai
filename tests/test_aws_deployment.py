"""Offline deployment tests: no credentials, network, or resource creation."""
import hashlib
import json
import sqlite3
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

AWS_DIR = Path(__file__).resolve().parents[1] / "deployment" / "aws"
sys.path.insert(0, str(AWS_DIR))
from common import load_config
from entrypoint import decode_secret, runtime_environment
from launch import parameters
from verify_restore import MAX_SIDECAR_BYTES, verify
from restore_from_file import read_private_passphrase


def fixture_config():
    return {"region": "ap-northeast-2", "stack_name": "unit-test-stack", "vpc_id": "vpc-12345678",
            "subnet_id": "subnet-12345678", "availability_zone": "ap-northeast-2a", "ami_id": "ami-12345678",
            "ecr_image_uri": "123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/unit-test@sha256:" + "a" * 64,
            "ecr_repository_arn": "arn:aws:ecr:ap-northeast-2:123456789012:repository/unit-test",
            "application_secret_arn": "arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:unit-test-AbCdEf",
            "application_secret_version_id": "a" * 32}


class ConfigTests(unittest.TestCase):
    def load(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "configuration.json"
            path.write_text(json.dumps(value))
            return load_config(path)

    def test_defaults_are_minimal_and_no_invoke(self):
        config = self.load(fixture_config())
        self.assertEqual(config["data_volume_size_gb"], 5)
        self.assertEqual(config["outbound_mode"], "public-egress")
        self.assertFalse(config["allow_approved_bedrock_runtime"])
        self.assertEqual(config["approved_bedrock_resource_arns"], [])
        self.assertEqual({x["ParameterKey"]: x["ParameterValue"] for x in parameters(config)}["EnableApprovedBedrockRuntime"], "false")

    def test_missing_actual_identifiers_fail(self):
        value = fixture_config()
        value["vpc_id"] = None
        with self.assertRaisesRegex(ValueError, "MISSING_REQUIRED_FIELDS:vpc_id"):
            self.load(value)

    def test_digest_and_account_binding_required(self):
        for name, replacement in (("ecr_image_uri", "123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/unit-test:latest"),
                                  ("application_secret_arn", "arn:aws:secretsmanager:ap-northeast-2:999999999999:secret:unit-test-AbCdEf")):
            value = fixture_config()
            value[name] = replacement
            with self.assertRaises(ValueError):
                self.load(value)

    def test_invoke_is_explicit_and_no_wildcards(self):
        value = fixture_config()
        value["allow_approved_bedrock_runtime"] = True
        with self.assertRaisesRegex(ValueError, "APPROVED_RUNTIME_REQUIRES_EXACT_RESOURCE_ARNS"):
            self.load(value)
        value["approved_bedrock_resource_arns"] = ["*"]
        with self.assertRaises(ValueError):
            self.load(value)
        value["approved_bedrock_resource_arns"] = ["arn:aws:bedrock:ap-northeast-2:123456789012:inference-profile/unit-test"]
        self.assertTrue(self.load(value)["allow_approved_bedrock_runtime"])

    def test_nat_is_optional_and_minimum_one_gib(self):
        value = fixture_config()
        value.update(outbound_mode="existing-nat", data_volume_size_gb=1)
        self.assertEqual(self.load(value)["outbound_mode"], "existing-nat")
        for size in (0, True):
            value["data_volume_size_gb"] = size
            with self.assertRaises(ValueError):
                self.load(value)

    def test_unrecognized_fields_rejected_without_printing_values(self):
        value = fixture_config()
        value["OPENAI_API_KEY"] = "test-sensitive-value"
        with self.assertRaises(ValueError) as captured:
            self.load(value)
        self.assertNotIn("test-sensitive-value", str(captured.exception))


class EnvironmentTests(unittest.TestCase):
    def test_original_crypto_owner_and_profile_values_preserved(self):
        secret = {"LIFERECOVER_DATA_KEY": "original-test-only-key", "EDURECOVER_OWNER_PASSWORD": "original-password",
                  "EDURECOVER_AI_HOLD": "true", "EDURECOVER_ALLOWED_PROVIDERS": "claude",
                  "EDURECOVER_BEDROCK_CLAUDE_INFERENCE_PROFILE": "original-profile"}
        env = runtime_environment(secret, {})
        for name, value in secret.items():
            self.assertEqual(env[name], value)
        self.assertEqual(secret["LIFERECOVER_DATA_KEY"], "original-test-only-key")

    def test_no_invented_ai_hold_and_user_zdr_release_is_explicit(self):
        env = runtime_environment({"EDURECOVER_REQUIRE_ZDR": "true", "EDURECOVER_PROVIDER_TRANSPORT": "direct"}, {})
        self.assertNotIn("EDURECOVER_AI_HOLD", env)
        self.assertEqual(env["EDURECOVER_REQUIRE_ZDR"], "false")
        self.assertEqual(env["EDURECOVER_PROVIDER_TRANSPORT"], "bedrock")
        self.assertEqual(env["EDURECOVER_ENABLE_ACTUAL_TRAINING_RECEIPTS"], "false")

    def test_static_aws_keys_and_fake_railway_mount_are_not_injected(self):
        secret = {"AWS_ACCESS_KEY_ID": "preserved-source-only", "AWS_SECRET_ACCESS_KEY": "preserved-source-only",
                  "RAILWAY_VOLUME_MOUNT_PATH": "/wrong", "EDURECOVER_CLASSROOM_STATE_DIR": "/wrong"}
        env = runtime_environment(secret, {"AWS_SESSION_TOKEN": "source-only", "AWS_REGION": "ap-northeast-2"})
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "RAILWAY_VOLUME_MOUNT_PATH"):
            self.assertNotIn(name, env)
            self.assertIn(name, {**secret, "AWS_SESSION_TOKEN": "source-only"})
        self.assertEqual(env["EDURECOVER_PERSISTENT_MOUNT_PATH"], "/app/.edurecover_classroom_state")

    def test_deployment_region_overrides_old_secret_region_without_changing_secret(self):
        secret = {"AWS_REGION": "us-east-1", "AWS_DEFAULT_REGION": "us-east-1"}
        env = runtime_environment(secret, {"AWS_REGION": "ap-northeast-2"})
        self.assertEqual(env["AWS_REGION"], "ap-northeast-2")
        self.assertEqual(env["AWS_DEFAULT_REGION"], "ap-northeast-2")
        self.assertEqual(secret["AWS_REGION"], "us-east-1")

    def test_nonstring_or_invalid_secret_map_fails(self):
        for raw in ('{"OWNER_PASSWORD": null}', '["secret"]', '{"OWNER_PASSWORD": "bad\\u0000value"}'):
            with self.assertRaises(ValueError):
                decode_secret(raw)


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.template = json.loads(AWS_DIR.joinpath("cloudformation.json").read_text())

    def test_zero_ingress_retained_encrypted_data_and_imds_hop2(self):
        resources = self.template["Resources"]
        self.assertEqual(resources["PrivateSecurityGroup"]["Properties"]["SecurityGroupIngress"], [])
        self.assertEqual(resources["DataVolume"]["DeletionPolicy"], "Retain")
        self.assertEqual(resources["DataVolume"]["UpdateReplacePolicy"], "Retain")
        self.assertTrue(resources["DataVolume"]["Properties"]["Encrypted"])
        self.assertEqual(resources["Instance"]["Properties"]["MetadataOptions"]["HttpPutResponseHopLimit"], 2)
        self.assertEqual(resources["Instance"]["Properties"]["MetadataOptions"]["HttpTokens"], "required")

    def test_no_secret_network_or_public_domain_resources_created(self):
        types = {value["Type"] for value in self.template["Resources"].values()}
        for forbidden in ("AWS::SecretsManager::Secret", "AWS::EC2::NatGateway", "AWS::EC2::VPC", "AWS::Route53::RecordSet"):
            self.assertNotIn(forbidden, types)
        self.assertEqual(self.template["Parameters"]["EnableApprovedBedrockRuntime"]["Default"], "false")

    def test_embedded_bootstrap_matches_reviewed_source(self):
        embedded = self.template["Resources"]["Instance"]["Properties"]["UserData"]["Fn::Base64"]["Fn::Sub"][0]
        self.assertEqual(embedded, AWS_DIR.joinpath("bootstrap.sh").read_text())
        self.assertIn("127.0.0.1:8000:8000", embedded)
        self.assertIn("src=/var/lib/edurecover/state,dst=/app/.edurecover_classroom_state", embedded)
        self.assertNotIn("systemctl enable --now edurecover-aws", embedded)

    def test_real_docker_ignore_is_automatic_file(self):
        self.assertEqual(AWS_DIR.parent.parent.joinpath("Dockerfile.aws.dockerignore").read_text(),
                         AWS_DIR.parent.parent.joinpath(".dockerignore.aws").read_text())

    def test_runtime_report_and_offline_restore_are_packaged(self):
        dockerfile = AWS_DIR.parent.parent.joinpath("Dockerfile.aws").read_text()
        for copied in ("COPY SERVER_STORAGE_LOCATIONS.txt", "COPY scripts/aws_migration_snapshot.py",
                       "COPY deployment/aws/restore_from_file.py"):
            self.assertIn(copied, dockerfile)
        self.assertIn("--workers", AWS_DIR.joinpath("entrypoint.py").read_text())
        self.assertIn("-m deployment.aws.restore_from_file", AWS_DIR.joinpath("README.ko.md").read_text())


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "state"
        self.root.joinpath("liferecover").mkdir(parents=True)
        database = self.root / "liferecover/accounts.sqlite3"
        with sqlite3.connect(database) as conn:
            conn.execute("create table users(id text primary key)")
            conn.execute("insert into users values ('original-account-id')")
        self.manifest = {"format": "edurecover-offline-migration-v1", "logical_id": "DREAMKEEPER-001",
                         "accounts_state": "included", "directories": ["liferecover"],
                         "files": [{"path": "liferecover/accounts.sqlite3", "size": database.stat().st_size,
                                    "sha256": hashlib.sha256(database.read_bytes()).hexdigest(), "sqlite": True}],
                         "restore_receipt": {"operation": "verified_staging_restore", "logical_id": "DREAMKEEPER-001"}}
        self.sidecar = self.root.with_name("state.edurmig-manifest.json")
        self.sidecar.write_text(json.dumps(self.manifest))

    def tearDown(self):
        self.temp.cleanup()

    def test_original_accounts_database_and_sibling_manifest_verify(self):
        result = verify(self.root)
        self.assertEqual(result["status"], "ORIGINAL_RECORDS_VERIFIED")
        self.assertEqual(result["sqlite_database_count"], 1)
        self.assertFalse(result["full_service_restore_verified"])

    def test_sha_failure_prevents_ownership_changes(self):
        self.root.joinpath("liferecover/accounts.sqlite3").write_bytes(b"damaged")
        with patch("verify_restore.os.chown") as change:
            with self.assertRaises(ValueError):
                verify(self.root, fix_ownership=True)
            change.assert_not_called()

    def test_extra_file_and_symlink_fail(self):
        self.root.joinpath("extra.txt").write_text("unexpected")
        with self.assertRaisesRegex(ValueError, "FILE_SET_MISMATCH"):
            verify(self.root)
        self.root.joinpath("extra.txt").unlink()
        self.root.joinpath("link").symlink_to(self.sidecar)
        with self.assertRaisesRegex(ValueError, "SYMLINK_FORBIDDEN"):
            verify(self.root)

    def test_account_state_contradiction_fails(self):
        self.manifest["accounts_state"] = "operator_confirmed_not_initialized"
        self.sidecar.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError, "ACCOUNT_STATE_MANIFEST_MISMATCH"):
            verify(self.root)

    def test_ownership_only_after_verified_originals(self):
        with patch("verify_restore.os.chown") as change, patch("verify_restore.os.geteuid", return_value=0):
            self.assertTrue(verify(self.root, fix_ownership=True)["ownership_prepared"])
            self.assertEqual(change.call_count, 3)
            self.assertTrue(all(call.args[1:3] == (10001, 10001) for call in change.call_args_list))

    def test_sidecar_size_matches_archive_manifest_limit_plus_receipt_margin(self):
        encoded = json.dumps(self.manifest).encode()
        self.sidecar.write_bytes(encoded + b" " * (MAX_SIDECAR_BYTES - len(encoded)))
        self.assertEqual(verify(self.root)["status"], "ORIGINAL_RECORDS_VERIFIED")
        with self.sidecar.open("ab") as stream:
            stream.write(b" ")
        with self.assertRaisesRegex(ValueError, "MANIFEST_TOO_LARGE"):
            verify(self.root)


class RestorePassphraseTests(unittest.TestCase):
    def test_private_file_preserves_value_without_logging_or_trimming(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "passphrase"
            path.write_text("test-only-passphrase ")
            path.chmod(0o600)
            self.assertEqual(read_private_passphrase(path), "test-only-passphrase ")
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                read_private_passphrase(path)

    def test_packaged_namespace_import_works_from_application_root(self):
        result = subprocess.run([sys.executable, "-c",
            "import deployment.aws.restore_from_file; import scripts.aws_migration_snapshot"],
            cwd=AWS_DIR.parent.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
