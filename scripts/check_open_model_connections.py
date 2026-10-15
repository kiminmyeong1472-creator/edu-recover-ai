"""Report the owner's 25 selected models; live checks require explicit names.

The default is a configuration-only report. Each explicitly selected, permitted,
configured and enabled model receives at most one synthetic request with
--invoke. This utility does not configure deployments or infer runtime IDs.
Only readiness flags and stable error codes are printed, never responses,
prompts, credentials, deployment URLs or additional AWS catalogue candidates.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.control_plane import ProviderHeld, assert_provider_allowed, evaluate_provider
from app.services.open_model_catalog import SOURCE_MODEL_CATALOG
from app.services.open_model_transport import OpenModelError, call_model, catalog_status


# Error codes are public vocabulary, not arbitrary exception data. An unknown
# code must not become a route for an upstream message or secret into a report.
_PUBLIC_ERROR_CODES = frozenset({
    "OPEN_MODEL_ACCESS_DENIED", "OPEN_MODEL_AUTHENTICATION_FAILED",
    "OPEN_MODEL_AWS_SUSPENDED", "OPEN_MODEL_CONFIG_INVALID",
    "OPEN_MODEL_CREDENTIALS_UNAVAILABLE", "OPEN_MODEL_DISABLED",
    "OPEN_MODEL_INCOMPLETE_RESPONSE", "OPEN_MODEL_INPUT_INVALID",
    "OPEN_MODEL_INPUT_TOO_LARGE", "OPEN_MODEL_INVOCATION_FAILED",
    "OPEN_MODEL_NOT_CONFIGURED", "OPEN_MODEL_NOT_READY",
    "OPEN_MODEL_POLICY_HELD", "OPEN_MODEL_QUOTA_EXCEEDED",
    "OPEN_MODEL_REQUEST_REJECTED", "OPEN_MODEL_RESOURCE_NOT_FOUND",
    "OPEN_MODEL_RESPONSE_INVALID", "OPEN_MODEL_RESPONSE_TOO_LARGE",
    "OPEN_MODEL_RUNTIME_UNAVAILABLE", "OPEN_MODEL_SDK_UNAVAILABLE",
    "OPEN_MODEL_SERVICE_UNAVAILABLE", "OPEN_MODEL_TIMEOUT", "OPEN_MODEL_UNKNOWN",
})


def _public_error_code(exc: OpenModelError) -> str:
    return exc.code if exc.code in _PUBLIC_ERROR_CODES else "OPEN_MODEL_CHECK_FAILED"


async def check_models(model_keys: list[str], invoke: bool = False) -> dict:
    """Inspect selected exact names, with no invocation unless explicitly asked."""
    # Validate the explicit invocation gate before resolving the default list.
    # Consequently --invoke alone can never expand to all 25 paid requests.
    if invoke and not model_keys:
        raise ValueError("실제 호출할 모델 이름을 지정해야 합니다.")
    known = {entry["model_key"] for entry in SOURCE_MODEL_CATALOG}
    if (not isinstance(model_keys, list)
            or any(not isinstance(key, str) for key in model_keys)
            or len(set(model_keys)) != len(model_keys)
            or not set(model_keys) <= known):
        raise ValueError("선택한 25개 모델의 정확한 이름을 중복 없이 지정해야 합니다.")
    selected_keys = set(model_keys) if model_keys else known
    # Restrict using SOURCE_MODEL_CATALOG, even if runtime status also contains
    # historical AWS-only candidates. Preserve the owner's catalogue order.
    selected = [entry for entry in SOURCE_MODEL_CATALOG
                if entry["model_key"] in selected_keys]
    status_by_key = {row["model_key"]: row for row in catalog_status()}
    results = []
    for entry in selected:
        row = status_by_key.get(entry["model_key"], {})
        record = {
            "model_key": entry["model_key"],
            "family": entry["family"],
            "configured": bool(row.get("configured", False)),
            "enabled": bool(row.get("enabled", False)),
        }
        if "readiness_state" in row:
            record["readiness_state"] = row["readiness_state"]
        elif "readiness_state" in entry:
            record["readiness_state"] = entry["readiness_state"]
        try:
            record["permitted"] = evaluate_provider(entry["family"], "generate").allowed
        except RuntimeError:
            record["permitted"] = False
        record["live_call_attempted"] = False
        if invoke:
            try:
                assert_provider_allowed(entry["family"], "generate")
                if record.get("readiness_state") == "missing_credentials":
                    record.update(response_received=False, error_code="OPEN_MODEL_CREDENTIALS_UNAVAILABLE")
                elif not record["configured"]:
                    record.update(response_received=False, error_code="OPEN_MODEL_NOT_CONFIGURED")
                elif not record["enabled"]:
                    record.update(response_received=False, error_code="OPEN_MODEL_DISABLED")
                else:
                    record["live_call_attempted"] = True
                    answer = await call_model(
                        entry["model_key"], "답은 숫자 하나만 출력하십시오.",
                        "1 더하기 1은 얼마입니까?", max_tokens=32,
                    )
                    record.update(
                        response_received=True,
                        answer_matches_expected=isinstance(answer, str) and answer.strip() == "2",
                    )
            except ProviderHeld:
                record.update(permitted=False, response_received=False,
                              error_code="OPEN_MODEL_POLICY_HELD")
            except OpenModelError as exc:
                record.update(response_received=False, error_code=_public_error_code(exc))
            except Exception:
                # A diagnostic command must not print a traceback containing
                # deployment settings or an unfiltered upstream response.
                record.update(response_received=False, error_code="OPEN_MODEL_CHECK_FAILED")
        results.append(record)
    return {
        "mode": "synthetic_live_call" if invoke else "configuration_only",
        "configuration_is_not_live_success": True,
        "models": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-key", action="append", default=[])
    parser.add_argument("--invoke", action="store_true", help="명시한 모델마다 합성 시험 요청 1회")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(check_models(args.model_key, args.invoke))
    except ValueError:
        print(json.dumps({"error_code": "INVALID_CHECK_REQUEST"}, ensure_ascii=False))
        return 2
    except OpenModelError as exc:
        print(json.dumps({"error_code": _public_error_code(exc)}, ensure_ascii=False))
        return 2
    except Exception:
        print(json.dumps({"error_code": "OPEN_MODEL_CHECK_FAILED"}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    if args.invoke and not all(row.get("response_received") and row.get("answer_matches_expected")
                               for row in result["models"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
