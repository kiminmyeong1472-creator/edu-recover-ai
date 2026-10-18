#!/usr/bin/env python3
"""Read-only, informational Bedrock account and retention observations.

This standalone command does not import the application, invoke a model,
change retention, select a provider, or block model use. Retention mode is
reported regardless of whether it is ``none``. Model-specific ZDR basis is
unchecked metadata. Exit 0 means requested reads completed; exit 2 indicates
an identity, Region, SDK, or read error, and never issues a model-block action.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import re
import sys
from typing import Any


EXIT_READ_COMPLETE = 0
EXIT_READ_ERROR = 2
READ_COMPLETE_STATUS = "INFORMATIONAL_READ_COMPLETE"
READ_INCOMPLETE_STATUS = "INFORMATIONAL_READ_INCOMPLETE"
REPORT_NOTE = "Informational observations only; this command makes no model-block or approval decision and changes no configuration."
EXIT_CODE_MEANING = "0 means requested reads completed; 2 means requested facts could not be verified. Neither exit code issues a model-block action."
POLICY_SOURCE = "https://docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html"
SDK_SOURCE = "https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock/client/get_account_data_retention.html"
SDK_UPGRADE = "Install or upgrade dependencies from requirements-aws.txt. This read-only command does not change retention settings."


def _safe_token(value: Any, fallback: str | None = None) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
        return value
    return fallback


def _error_summary(exc: Exception) -> dict[str, Any]:
    """Keep service error codes/request IDs, never exception messages or bodies."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error") or {}
        metadata = response.get("ResponseMetadata") or {}
        return {
            "errorCode": _safe_token(error.get("Code"), "UNKNOWN_AWS_ERROR"),
            "requestId": _safe_token(metadata.get("RequestId")),
        }
    return {"errorCode": _safe_token(type(exc).__name__, "UNKNOWN_CLIENT_ERROR")}


def _metadata(response: dict[str, Any]) -> dict[str, Any]:
    meta = response.get("ResponseMetadata") or {}
    result: dict[str, Any] = {}
    request_id = _safe_token(meta.get("RequestId"))
    if request_id:
        result["requestId"] = request_id
    if isinstance(meta.get("HTTPStatusCode"), int):
        result["httpStatusCode"] = meta["HTTPStatusCode"]
    return result


def _profile_scope(arn: Any) -> tuple[str, str] | None:
    if not isinstance(arn, str):
        return None
    parts = arn.split(":", 5)
    if len(parts) != 6 or parts[0] != "arn" or parts[2] != "bedrock":
        return None
    if not parts[5].startswith(("inference-profile/", "application-inference-profile/")):
        return None
    if not re.fullmatch(r"\d{12}", parts[4]):
        return None
    return parts[4], parts[3]


def _valid_configuration(region: str, expected_account_id: str, inference_profiles: list[str]) -> bool:
    return bool(
        re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d", region or "")
        and re.fullmatch(r"\d{12}", expected_account_id or "")
        and inference_profiles
        and all(isinstance(p, str) and p and len(p) <= 2048 and not any(ord(c) < 32 for c in p) for p in inference_profiles)
    )


def run_preflight(
    sts: Any,
    bedrock: Any,
    *,
    region: str,
    expected_account_id: str,
    inference_profiles: list[str],
) -> dict[str, Any]:
    """Use supplied clients so validation is testable without AWS access."""
    report: dict[str, Any] = {
        "status": READ_INCOMPLETE_STATUS,
        "reportNote": REPORT_NOTE,
        "exitCodeMeaning": EXIT_CODE_MEANING,
        "model_specific_zdr_basis": "UNCHECKED",
        "region": region,
        "reasonCodes": [],
        "profileChecks": [],
        "sourcePolicy": POLICY_SOURCE,
    }
    reasons = report["reasonCodes"]
    if not _valid_configuration(region, expected_account_id, inference_profiles):
        reasons.append("INVALID_CONFIGURATION")
        return report

    required_methods = ((sts, "get_caller_identity"), (bedrock, "get_account_data_retention"), (bedrock, "get_inference_profile"))
    if any(not callable(getattr(client, method, None)) for client, method in required_methods):
        reasons.append("SDK_OPERATION_UNAVAILABLE")
        report["sdkAction"] = SDK_UPGRADE
        report["sdkSource"] = SDK_SOURCE
        return report
    if any(getattr(getattr(client, "meta", None), "region_name", None) != region for client in (sts, bedrock)):
        reasons.append("CLIENT_REGION_MISMATCH")
        return report
    report["client_region_match"] = True

    try:
        identity = sts.get_caller_identity()
    except Exception as exc:
        reasons.append("IDENTITY_CHECK_FAILED")
        report["identityCheck"] = _error_summary(exc)
        return report
    report["identityCheck"] = _metadata(identity)
    report["account_match"] = identity.get("Account") == expected_account_id
    if not report["account_match"]:
        reasons.append("ACCOUNT_MISMATCH")
        return report

    try:
        retention = bedrock.get_account_data_retention()
    except Exception as exc:
        reasons.append("RETENTION_CHECK_FAILED")
        report["retentionCheck"] = _error_summary(exc)
        return report
    report["retentionCheck"] = _metadata(retention)
    mode = retention.get("mode")
    report["account_data_retention_mode"] = mode if mode in {"none", "inherit", "default", "aws_review", "provider_data_share"} else "UNKNOWN"
    updated_at = retention.get("updatedAt")
    if isinstance(updated_at, datetime):
        report["retention_updated_at"] = updated_at.isoformat()
    if report["account_data_retention_mode"] == "UNKNOWN":
        reasons.append("RETENTION_RESPONSE_UNRECOGNIZED")
        return report

    profiles_read_complete = True
    for index, profile in enumerate(inference_profiles, 1):
        check: dict[str, Any] = {"index": index, "scope_verified": False}
        report["profileChecks"].append(check)
        try:
            response = bedrock.get_inference_profile(inferenceProfileIdentifier=profile)
        except Exception as exc:
            check.update(_error_summary(exc))
            reasons.append("PROFILE_CHECK_FAILED")
            profiles_read_complete = False
            continue
        check.update(_metadata(response))
        scope = _profile_scope(response.get("inferenceProfileArn"))
        check["account_match"] = bool(scope and scope[0] == expected_account_id)
        check["region_match"] = bool(scope and scope[1] == region)
        check["active"] = response.get("status") == "ACTIVE"
        models = response.get("models")
        check["models_present"] = isinstance(models, list) and bool(models)
        check["scope_verified"] = check["account_match"] and check["region_match"]
        if not check["scope_verified"]:
            profiles_read_complete = False
            reasons.append("PROFILE_ACCOUNT_OR_REGION_MISMATCH")

    if profiles_read_complete:
        report["status"] = READ_COMPLETE_STATUS
    report["reasonCodes"] = list(dict.fromkeys(reasons))
    report["modelSignalPath"] = "Mantle GET /v1/models/{base-model-id}: data_retention.allowed_modes; not exposed by Bedrock control plane."
    report["scopeNote"] = "Account retention mode and profile activity are observations only. Model-specific ZDR basis and successful inference are not verified."
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Informational AWS Bedrock account/retention reads. All known retention modes are reported without a ZDR gate. No inference, configuration changes, or model-block actions.")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION"), help="Source AWS Region; defaults to AWS_REGION.")
    parser.add_argument("--expected-account-id", required=True, help="Expected 12-digit account ID; not echoed in the report.")
    parser.add_argument("--inference-profile", action="append", required=True, help="Selected inference profile ID or ARN; repeat for each selected profile.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.region:
        report = {"status": READ_INCOMPLETE_STATUS, "reasonCodes": ["AWS_REGION_REQUIRED"]}
    elif os.environ.get("AWS_REGION") and os.environ["AWS_REGION"] != args.region:
        report = {"status": READ_INCOMPLETE_STATUS, "reasonCodes": ["ENVIRONMENT_REGION_MISMATCH"]}
    elif not _valid_configuration(args.region, args.expected_account_id, args.inference_profile):
        report = {"status": READ_INCOMPLETE_STATUS, "reasonCodes": ["INVALID_CONFIGURATION"]}
    else:
        try:
            import boto3
            from botocore.config import Config

            config = Config(retries={"mode": "standard", "total_max_attempts": 1}, connect_timeout=5, read_timeout=20)
            sts = boto3.client("sts", region_name=args.region, config=config)
            bedrock = boto3.client("bedrock", region_name=args.region, config=config)
            report = run_preflight(sts, bedrock, region=args.region, expected_account_id=args.expected_account_id, inference_profiles=args.inference_profile)
        except ImportError:
            report = {"status": READ_INCOMPLETE_STATUS, "reasonCodes": ["SDK_UNAVAILABLE"], "sdkAction": SDK_UPGRADE}
        except Exception as exc:
            report = {"status": READ_INCOMPLETE_STATUS, "reasonCodes": ["CLIENT_SETUP_FAILED"], "error": _error_summary(exc)}
    report["reportNote"] = REPORT_NOTE
    report["exitCodeMeaning"] = EXIT_CODE_MEANING
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return EXIT_READ_COMPLETE if report["status"] == READ_COMPLETE_STATUS else EXIT_READ_ERROR


if __name__ == "__main__":
    sys.exit(main())
