#!/usr/bin/env python3
"""Build/push an AWS image, verify originals/start one private host, or use SSM."""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from common import aws, load_config, require_aws_cli, safe_main, verify_account

ROOT = Path(__file__).resolve().parent
REPO_PATTERN = re.compile(r"^(\d{12})\.dkr\.ecr\.([a-z]{2}-[a-z]+-\d)\.amazonaws\.com/([a-z0-9][a-z0-9._/-]*)$")
KEY_PATTERN = re.compile(rb"(?:\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\bAIza[A-Za-z0-9_-]{35}\b|\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{40,})")
HOST_STATE = "/var/lib/edurecover/state"


def verify_build_source(root: Path) -> None:
    for relative in ("Dockerfile.aws", "Dockerfile.aws.dockerignore", "requirements.txt",
                     "requirements-aws.txt", "app/main.py", "SERVER_STORAGE_LOCATIONS.txt",
                     "scripts/aws_migration_snapshot.py", "deployment/aws/entrypoint.py",
                     "deployment/aws/restore_from_file.py"):
        if not root.joinpath(relative).is_file():
            raise ValueError("BUILD_SOURCE_REQUIRED:" + relative)
    ignore = root.joinpath("Dockerfile.aws.dockerignore").read_text()
    if not all(p in ignore.splitlines() for p in (".git", ".env", ".env.*", ".edurecover_classroom_state", "*.sqlite3")):
        raise ValueError("AWS_DOCKER_SECRET_EXCLUSIONS_REQUIRED")
    # Only code/runtime asset locations copied by Dockerfile are scanned. Report
    # filenames only; never print the bytes that resemble an original credential.
    copied = [*root.joinpath("app").rglob("*"), *root.joinpath("training").rglob("*"),
              root.joinpath("requirements.txt"), root.joinpath("requirements-aws.txt"),
              root.joinpath("SERVER_STORAGE_LOCATIONS.txt"), root.joinpath("scripts/aws_migration_snapshot.py"),
              root.joinpath("deployment/aws/entrypoint.py"), root.joinpath("deployment/aws/restore_from_file.py")]
    for path in copied:
        if path.is_symlink():
            raise ValueError("BUILD_SYMLINK_FORBIDDEN:" + path.relative_to(root).as_posix())
        if path.is_file() and path.suffix.lower() in {".py", ".txt", ".json", ".html", ".yaml", ".yml"}:
            if KEY_PATTERN.search(path.read_bytes()):
                raise ValueError("BUILD_PLAINTEXT_CREDENTIAL_DETECTED:" + path.relative_to(root).as_posix())


def build_and_push(args) -> None:
    repository = REPO_PATTERN.fullmatch(args.repository_uri or "")
    if not repository or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", args.image_tag or ""):
        raise ValueError("EXACT_ECR_REPOSITORY_URI_AND_IMAGE_TAG_REQUIRED")
    region = repository.group(2)
    require_aws_cli()
    if not shutil.which("docker"):
        raise RuntimeError("DOCKER_NOT_INSTALLED")
    config = {"region": region}
    if aws(config, "sts", "get-caller-identity")["Account"] != repository.group(1):
        raise RuntimeError("AWS_CALLER_ACCOUNT_MISMATCH")
    aws(config, "ecr", "describe-repositories", "--repository-names", repository.group(3))
    source = args.source_root.resolve()
    verify_build_source(source)
    image = args.repository_uri + ":" + args.image_tag
    # Temporary Docker credentials contain only an ECR token; originals never
    # enter the build context and no credential content is logged.
    with tempfile.TemporaryDirectory(prefix="edurecover-ecr-") as docker_config:
        os.chmod(docker_config, 0o700)
        env = dict(os.environ, DOCKER_CONFIG=docker_config, AWS_PAGER="", AWS_CLI_AUTO_PROMPT="off")
        token = subprocess.run(["aws", "--region", region, "ecr", "get-login-password"],
                               check=True, capture_output=True, env=env).stdout
        subprocess.run(["docker", "login", "--username", "AWS", "--password-stdin", args.repository_uri.split("/")[0]],
                       input=token, check=True, capture_output=True, env=env)
        subprocess.run(["docker", "build", "--platform", "linux/amd64", "--file", "Dockerfile.aws", "--tag", image, "."],
                       cwd=source, check=True, env=env)
        subprocess.run(["docker", "push", image], check=True, env=env)
    result = aws(config, "ecr", "describe-images", "--repository-name", repository.group(3),
                 "--image-ids", "imageTag=" + args.image_tag)["imageDetails"][0]
    print(json.dumps({"image_uri": args.repository_uri + "@" + result["imageDigest"],
                      "deployment": "NOT_EXECUTED", "secret_values_logged": False}))


def instance_id(config: dict) -> str:
    verify_account(config)
    stack = aws(config, "cloudformation", "describe-stacks", "--stack-name", config["stack_name"])["Stacks"][0]
    if stack["StackStatus"] not in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}:
        raise RuntimeError("COMPLETED_STACK_REQUIRED")
    values = {x["OutputKey"]: x["OutputValue"] for x in stack.get("Outputs", [])}
    value = values.get("InstanceId", "")
    if not re.fullmatch(r"i-[a-f0-9]{8,17}", value):
        raise RuntimeError("VERIFIED_STACK_INSTANCE_ID_REQUIRED")
    if values.get("ApplicationSecretArn") != config["application_secret_arn"] or values.get("ApplicationSecretVersionId") != config["application_secret_version_id"]:
        raise RuntimeError("STACK_PINNED_SECRET_MISMATCH")
    return value


def start_restored_host(config: dict, confirmed: bool) -> None:
    if not confirmed:
        raise ValueError("EXPLICIT_START_RESTORED_HOST_CONFIRMATION_REQUIRED")
    target = instance_id(config)
    verifier = base64.b64encode(ROOT.joinpath("verify_restore.py").read_bytes()).decode("ascii")
    code = "import base64;exec(compile(base64.b64decode(" + repr(verifier) + "),'<restore-verifier>','exec'))"
    # Remote command is fixed code and fixed paths: no original secret content.
    commands = ["set -eu", "sudo systemctl stop edurecover-aws.service",
                "mountpoint -q /var/lib/edurecover",
                "sudo python3 -c " + shlex.quote(code) + " " + HOST_STATE + " --fix-ownership",
                "sudo systemctl enable --now edurecover-aws.service"]
    reply = aws(config, "ssm", "send-command", "--instance-ids", target,
                "--document-name", "AWS-RunShellScript", "--comment", "Verify restored originals then start private single-worker candidate",
                "--parameters", json.dumps({"commands": commands, "executionTimeout": ["600"]}))
    print(json.dumps({"command_id": reply["Command"]["CommandId"], "instance_id": target,
                      "status": "START_VERIFICATION_SUBMITTED", "cutover": "NOT_PERFORMED"}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--build-and-push", action="store_true")
    modes.add_argument("--start-restored-host", action="store_true")
    modes.add_argument("--port-forward", action="store_true")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--source-root", type=Path, default=ROOT.parent.parent)
    parser.add_argument("--repository-uri")
    parser.add_argument("--image-tag")
    parser.add_argument("--confirm-start-restored-host", action="store_true")
    parser.add_argument("--local-port", type=int, default=8000)
    args = parser.parse_args()
    if args.build_and_push:
        build_and_push(args)
        return
    if args.config is None:
        raise ValueError("CONFIG_REQUIRED")
    config = load_config(args.config)
    if args.start_restored_host:
        start_restored_host(config, args.confirm_start_restored_host)
        return
    if not 1024 <= args.local_port <= 65535:
        raise ValueError("LOCAL_PORT_MUST_BE_1024_TO_65535")
    if not shutil.which("session-manager-plugin"):
        raise RuntimeError("SESSION_MANAGER_PLUGIN_REQUIRED")
    target = instance_id(config)
    subprocess.run(["aws", "--region", config["region"], "ssm", "start-session", "--target", target,
                    "--document-name", "AWS-StartPortForwardingSession",
                    "--parameters", json.dumps({"portNumber": ["8000"], "localPortNumber": [str(args.local_port)]})], check=True)


if __name__ == "__main__":
    safe_main(main)
