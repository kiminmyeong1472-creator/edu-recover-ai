"""Report model readiness; paid synthetic calls require --invoke and model keys.

Configuration-only is the default. Neither credentials nor model output is
printed. This utility does not create resources or change configuration.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.control_plane import ProviderHeld, assert_provider_allowed, evaluate_provider
from app.services.open_model_transport import OpenModelError, call_model, catalog_status


async def check_models(model_keys: list[str], invoke: bool = False) -> dict:
    if invoke and not model_keys:
        raise ValueError("실제 호출할 모델 이름을 지정해야 합니다.")
    rows = catalog_status()
    known = {row["model_key"] for row in rows}
    if len(set(model_keys)) != len(model_keys) or not set(model_keys) <= known:
        raise ValueError("등록된 모델 이름을 중복 없이 지정해야 합니다.")
    selected = [row for row in rows if not model_keys or row["model_key"] in model_keys]
    results = []
    for row in selected:
        record = {key: row[key] for key in ("model_key", "family", "configured", "enabled")}
        try:
            record["permitted"] = evaluate_provider(row["family"], "generate").allowed
        except RuntimeError:
            record["permitted"] = False
        record["live_call_attempted"] = False
        if invoke:
            try:
                assert_provider_allowed(row["family"], "generate")
                record["live_call_attempted"] = bool(row["configured"] and row["enabled"])
                answer = await call_model(
                    row["model_key"], "답은 숫자 하나만 출력하십시오.",
                    "1 더하기 1은 얼마입니까?", max_tokens=32,
                )
                record.update(response_received=True, answer_matches_expected=answer.strip() == "2")
            except ProviderHeld:
                record.update(response_received=False, error_code="OPEN_MODEL_POLICY_HELD")
            except OpenModelError as exc:
                record.update(response_received=False, error_code=exc.code)
        results.append(record)
    return {
        "mode": "synthetic_live_call" if invoke else "configuration_only",
        "configuration_is_not_live_success": True,
        "models": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-key", action="append", default=[])
    parser.add_argument("--invoke", action="store_true", help="선택한 모델마다 유료 시험 요청 1회")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(check_models(args.model_key, args.invoke))
    except (ValueError, OpenModelError) as exc:
        print(json.dumps({"error_code": getattr(exc, "code", "INVALID_CHECK_REQUEST")}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    if args.invoke and not all(row.get("response_received") and row.get("answer_matches_expected")
                               for row in result["models"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
