"""Non-secret deployment validation and bounded AWS CLI responses."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

REQUIRED = {
    "region", "stack_name", "vpc_id", "subnet_id", "availability_zone", "ami_id",
    "ecr_image_uri", "ecr_repository_arn", "application_secret_arn", "application_secret_version_id",
}
OPTIONAL = {
    "instance_type", "data_volume_size_gb", "existing_data_volume_id", "secret_kms_key_arn",
    "data_kms_key_arn", "allow_approved_bedrock_runtime", "approved_bedrock_resource_arns",
    "outbound_mode",
}
REGION_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d$")
IMAGE_RE = re.compile(r"^(\d{12})\.dkr\.ecr\.([a-z]{2}-[a-z]+-\d)\.amazonaws\.com/([a-z0-9][a-z0-9._/-]*)@sha256:([a-f0-9]{64})$")


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("CONFIG_OBJECT_REQUIRED")
    unknown = set(config) - REQUIRED - OPTIONAL
    missing = sorted(k for k in REQUIRED if not isinstance(config.get(k), str) or not config[k].strip())
    if unknown:
        raise ValueError("UNKNOWN_CONFIG_FIELDS:" + ",".join(sorted(unknown)))
    if missing:
        raise ValueError("MISSING_REQUIRED_FIELDS:" + ",".join(missing))
    for key in REQUIRED:
        if "\n" in config[key] or "\r" in config[key] or "\x00" in config[key]:
            raise ValueError("INVALID_IDENTIFIER_FIELD:" + key)
    if not REGION_RE.fullmatch(config["region"]):
        raise ValueError("INVALID_REGION")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", config["stack_name"]):
        raise ValueError("INVALID_STACK_NAME")
    for key, prefix in (("vpc_id", "vpc"), ("subnet_id", "subnet"), ("ami_id", "ami")):
        if not re.fullmatch(prefix + r"-[a-f0-9]{8,17}", config[key]):
            raise ValueError("INVALID_RESOURCE_IDENTIFIER:" + key)
    if not re.fullmatch(re.escape(config["region"]) + r"[a-z]", config["availability_zone"]):
        raise ValueError("AVAILABILITY_ZONE_REGION_MISMATCH")
    image = IMAGE_RE.fullmatch(config["ecr_image_uri"])
    if not image or image.group(2) != config["region"]:
        raise ValueError("REGIONAL_ECR_DIGEST_URI_REQUIRED")
    repository_arn = f"arn:aws:ecr:{config['region']}:{image.group(1)}:repository/{image.group(3)}"
    if config["ecr_repository_arn"] != repository_arn:
        raise ValueError("ECR_REPOSITORY_IMAGE_MISMATCH")
    if not re.fullmatch(f"arn:aws:secretsmanager:{re.escape(config['region'])}:{image.group(1)}:secret:[A-Za-z0-9/_+=.@-]+", config["application_secret_arn"]):
        raise ValueError("SAME_ACCOUNT_REGION_SECRET_ARN_REQUIRED")
    if not re.fullmatch(r"[A-Za-z0-9-]{32,64}", config["application_secret_version_id"]):
        raise ValueError("PINNED_SECRET_VERSION_ID_REQUIRED")
    config.setdefault("instance_type", "t3.small")
    if config["instance_type"] not in {"t3.small", "t3.medium", "t3.large", "m6i.large"}:
        raise ValueError("SUPPORTED_X86_NITRO_INSTANCE_REQUIRED")
    config.setdefault("data_volume_size_gb", 5)
    if type(config["data_volume_size_gb"]) is not int or not 1 <= config["data_volume_size_gb"] <= 16384:
        raise ValueError("INVALID_DATA_VOLUME_SIZE")
    config.setdefault("outbound_mode", "public-egress")
    if config["outbound_mode"] not in {"public-egress", "existing-nat"}:
        raise ValueError("INVALID_OUTBOUND_MODE")
    config.setdefault("existing_data_volume_id", "")
    if config["existing_data_volume_id"] and not re.fullmatch(r"vol-[a-f0-9]{8,17}", config["existing_data_volume_id"]):
        raise ValueError("INVALID_EXISTING_DATA_VOLUME")
    for key in ("secret_kms_key_arn", "data_kms_key_arn"):
        config.setdefault(key, "")
        if config[key] and not re.fullmatch(f"arn:aws:kms:{re.escape(config['region'])}:{image.group(1)}:key/[a-f0-9-]+", config[key]):
            raise ValueError("INVALID_KMS_KEY_ARN:" + key)
    config.setdefault("allow_approved_bedrock_runtime", False)
    config.setdefault("approved_bedrock_resource_arns", [])
    if type(config["allow_approved_bedrock_runtime"]) is not bool:
        raise ValueError("RUNTIME_PERMISSION_BOOLEAN_REQUIRED")
    arns = config["approved_bedrock_resource_arns"]
    if not isinstance(arns, list) or any(not isinstance(x, str) or not re.fullmatch(r"arn:aws:bedrock:[a-z0-9-]+:[0-9]*:[A-Za-z0-9._:/-]+", x) for x in arns):
        raise ValueError("EXACT_BEDROCK_RESOURCE_ARNS_REQUIRED")
    if config["allow_approved_bedrock_runtime"] and not arns:
        raise ValueError("APPROVED_RUNTIME_REQUIRES_EXACT_RESOURCE_ARNS")
    if not config["allow_approved_bedrock_runtime"] and arns:
        raise ValueError("RUNTIME_ARNS_REQUIRE_EXPLICIT_APPROVAL_FLAG")
    return config


def require_aws_cli() -> None:
    if not shutil.which("aws"):
        raise RuntimeError("AWS_CLI_V2_NOT_INSTALLED")
    reply = subprocess.run(["aws", "--version"], check=True, capture_output=True, text=True)
    if "aws-cli/2." not in reply.stdout + reply.stderr:
        raise RuntimeError("AWS_CLI_V2_REQUIRED")


def aws(config: dict, *args: str) -> dict:
    # Never use --debug or print stderr; callers do not fetch SecretString.
    env = dict(os.environ, AWS_PAGER="", AWS_CLI_AUTO_PROMPT="off")
    result = subprocess.run(["aws", "--region", config["region"], *args, "--output", "json"],
                            check=True, capture_output=True, text=True, env=env)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def verify_account(config: dict) -> dict:
    require_aws_cli()
    caller = aws(config, "sts", "get-caller-identity")
    if caller["Account"] != IMAGE_RE.fullmatch(config["ecr_image_uri"]).group(1):
        raise RuntimeError("AWS_CALLER_ACCOUNT_MISMATCH")
    return caller


def safe_main(function) -> None:
    try:
        function()
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from None
    except Exception as exc:
        # AWS exception payloads and user environment values are not printed.
        raise SystemExit("DEPLOYMENT_COMMAND_FAILED error_type=" + type(exc).__name__) from None
