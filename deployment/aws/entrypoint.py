"""Load the existing pinned secret in memory, then start one application worker.

This file must not generate, rotate, persist, or print original secret values.
The existing Secrets Manager resource is external to this deployment stack.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

STATE_ROOT = "/app/.edurecover_classroom_state"
STATIC_AWS_CREDENTIALS = {
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN", "AWS_PROFILE", "AWS_DEFAULT_PROFILE",
    "AWS_SHARED_CREDENTIALS_FILE", "AWS_CONFIG_FILE",
    "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
}
NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def decode_secret(raw: str) -> dict[str, str]:
    """Reject malformed environment maps without including their content in errors."""
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("SECRET_JSON_OBJECT_REQUIRED")
    if any(not isinstance(k, str) or not NAME_PATTERN.fullmatch(k)
           or not isinstance(v, str) or "\x00" in v for k, v in value.items()):
        raise ValueError("SECRET_STRING_ENVIRONMENT_MAP_REQUIRED")
    return value


def runtime_environment(secret: dict[str, str], inherited: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in inherited.items() if k not in STATIC_AWS_CREDENTIALS
           and not k.startswith("RAILWAY_")}
    env.update({k: v for k, v in secret.items() if k not in STATIC_AWS_CREDENTIALS
                and not k.startswith("RAILWAY_")})
    # Preserve the owner's original AI hold/allow controls. The user's current
    # temporary ZDR-gate release changes no stored SecretString or AWS setting.
    env.update({
        "ENVIRONMENT": "production",
        "EDURECOVER_CLASSROOM_STATE_DIR": STATE_ROOT,
        "EDURECOVER_PERSISTENT_MOUNT_PATH": STATE_ROOT,
        "EDURECOVER_BACKUP_SOURCE_ROOT": STATE_ROOT,
        "EDURECOVER_BACKUP_SERVER_ROOT": STATE_ROOT + "/backups",
        "EDURECOVER_BACKUP_STATE_DIR": STATE_ROOT + "/backup_state",
        "EDURECOVER_PROVIDER_TRANSPORT": "bedrock",
        "EDURECOVER_REQUIRE_ZDR": "false",
        "EDURECOVER_ENABLE_ACTUAL_TRAINING_RECEIPTS": "false",
        "EDURECOVER_BACKUP_SCHEDULER_ENABLED": "false",
        "EDURECOVER_BACKUP_ENABLED": "false",
        "AWS_EC2_METADATA_DISABLED": "false",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    if inherited.get("AWS_REGION"):
        env["AWS_REGION"] = inherited["AWS_REGION"]
        env["AWS_DEFAULT_REGION"] = inherited["AWS_REGION"]
    return env


def main() -> int:
    try:
        arn = os.environ["EDURECOVER_APPLICATION_SECRET_ARN"]
        version = os.environ["EDURECOVER_APPLICATION_SECRET_VERSION_ID"]
        region = os.environ["AWS_REGION"]
        root = Path(STATE_ROOT)
        if root.is_symlink() or not root.is_mount():
            raise RuntimeError("PERSISTENT_MOUNT_REQUIRED")
        # Use only EC2 instance-role credentials to retrieve the existing secret.
        for name in STATIC_AWS_CREDENTIALS:
            os.environ.pop(name, None)
        import boto3
        reply = boto3.client("secretsmanager", region_name=region).get_secret_value(
            SecretId=arn, VersionId=version)
        if reply.get("VersionId") != version or "SecretString" not in reply:
            raise RuntimeError("PINNED_SECRET_STRING_REQUIRED")
        env = runtime_environment(decode_secret(reply["SecretString"]), dict(os.environ))
        # Exec preserves no intermediate secret file and runs exactly one worker.
        os.execvpe(sys.executable, [sys.executable, "-m", "uvicorn", "app.main:app",
                    "--host", "0.0.0.0", "--port", "8000", "--workers", "1",
                    "--no-access-log"], env)
    except Exception as exc:
        print("AWS_STARTUP_BLOCKED error_type=" + type(exc).__name__, file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
