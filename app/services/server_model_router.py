"""Server-side cost/privacy model router with a persistent usage ledger.

Policy:
- non-personal, low-cost work -> Amazon Bedrock GPT-5.6 Luna
- privacy-sensitive, low-cost work -> Amazon Bedrock Claude Haiku 4.5
- privacy-sensitive standard/high work -> Claude Sonnet 5 / Opus 5
- reported GPT hallucination, overclaim, false report, lie, or instruction refusal
  -> never retry GPT; use Gemini or Claude
- Google/AWS credit budgets are planning budgets, not cloud-billing authority.

The router stores only model/usage metadata and hashes, never prompt/response text.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from app.core.config import settings
from app.services.secret_vault import state_root


Cloud = Literal["aws", "google"]
Provider = Literal["openai", "gemini", "claude"]
QualityTier = Literal["low", "standard", "high"]
GPTIssue = Literal[
    "hallucination",
    "overclaim",
    "false_report",
    "lie",
    "instruction_refusal",
    "invalid_output",
    "other_quality_failure",
]


@dataclass(frozen=True)
class ModelSpec:
    provider: Provider
    model: str
    cloud: Cloud
    input_usd_per_million: float
    output_usd_per_million: float
    privacy_class: str
    region_notice: str


@dataclass(frozen=True)
class RouteDecision:
    provider: Provider
    model: str
    cloud: Cloud
    reason: str
    privacy_notice: str
    fallback_from_gpt: bool


def _specs() -> dict[str, ModelSpec]:
    # Rates are planning defaults and can be updated by environment variables
    # without a client release. They are not a substitute for provider billing.
    return {
        "gpt_luna": ModelSpec(
            "openai",
            settings.bedrock_gpt_luna_model,
            "aws",
            float(os.getenv("EDURECOVER_PRICE_GPT_LUNA_INPUT_USD_M", "0.20")),
            float(os.getenv("EDURECOVER_PRICE_GPT_LUNA_OUTPUT_USD_M", "1.20")),
            "NON_PERSONAL_ONLY",
            "Bedrock Luna may use a model-specific endpoint/region path; do not route personal data to it.",
        ),
        "gemini_flash": ModelSpec(
            "gemini",
            settings.gemini_flash_model,
            "google",
            float(os.getenv("EDURECOVER_PRICE_GEMINI_FLASH_INPUT_USD_M", "0.75")),
            float(os.getenv("EDURECOVER_PRICE_GEMINI_FLASH_OUTPUT_USD_M", "3.75")),
            "NON_PERSONAL_OR_APPROVED_ZDR",
            "Vertex location and model residency must be reviewed separately for personal data.",
        ),
        "gemini_pro": ModelSpec(
            "gemini",
            settings.gemini_model,
            "google",
            float(os.getenv("EDURECOVER_PRICE_GEMINI_PRO_INPUT_USD_M", "2.00")),
            float(os.getenv("EDURECOVER_PRICE_GEMINI_PRO_OUTPUT_USD_M", "12.00")),
            "NON_PERSONAL_OR_APPROVED_ZDR",
            "Vertex location and model residency must be reviewed separately for personal data.",
        ),
        "claude_haiku": ModelSpec(
            "claude",
            settings.bedrock_claude_haiku_model,
            "aws",
            float(os.getenv("EDURECOVER_PRICE_CLAUDE_HAIKU_INPUT_USD_M", "1.00")),
            float(os.getenv("EDURECOVER_PRICE_CLAUDE_HAIKU_OUTPUT_USD_M", "5.00")),
            "PRIVACY_PREFERRED",
            "Haiku 4.5 may use cross/global inference on Bedrock Runtime; disclose this in privacy notices.",
        ),
        "claude_sonnet": ModelSpec(
            "claude",
            settings.bedrock_claude_sonnet_model,
            "aws",
            float(os.getenv("EDURECOVER_PRICE_CLAUDE_SONNET_INPUT_USD_M", "3.00")),
            float(os.getenv("EDURECOVER_PRICE_CLAUDE_SONNET_OUTPUT_USD_M", "15.00")),
            "PRIVACY_PREFERRED",
            "Use the configured Bedrock region/model path; disclose any cross-region inference path.",
        ),
        "claude_opus": ModelSpec(
            "claude",
            settings.bedrock_claude_opus_model,
            "aws",
            float(os.getenv("EDURECOVER_PRICE_CLAUDE_OPUS_INPUT_USD_M", "5.00")),
            float(os.getenv("EDURECOVER_PRICE_CLAUDE_OPUS_OUTPUT_USD_M", "25.00")),
            "PRIVACY_PREFERRED",
            "Use the configured Bedrock region/model path; disclose any cross-region inference path.",
        ),
    }


_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS model_usage(
 id TEXT PRIMARY KEY,
 created_at TEXT NOT NULL,
 provider TEXT NOT NULL,
 model TEXT NOT NULL,
 cloud TEXT NOT NULL,
 route_reason TEXT NOT NULL,
 input_tokens INTEGER NOT NULL,
 output_tokens INTEGER NOT NULL,
 estimated_cost_usd REAL NOT NULL
);
"""


class ModelUsageLedger:
    def __init__(self, path: Path | None = None):
        self.path = path or state_root() / "model_router.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise RuntimeError("MODEL_ROUTER_LEDGER_SYMLINK_FORBIDDEN")
        with sqlite3.connect(self.path) as db:
            db.executescript(_SCHEMA)
        self.path.chmod(0o600)

    def record(
        self,
        *,
        spec: ModelSpec,
        route_reason: str,
        input_tokens: int,
        output_tokens: int,
    ) -> dict[str, float | int | str]:
        input_tokens = max(0, int(input_tokens))
        output_tokens = max(0, int(output_tokens))
        cost = (
            input_tokens * spec.input_usd_per_million
            + output_tokens * spec.output_usd_per_million
        ) / 1_000_000
        receipt = {
            "id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "provider": spec.provider,
            "model": spec.model,
            "cloud": spec.cloud,
            "route_reason": route_reason,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": round(cost, 8),
        }
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO model_usage VALUES(?,?,?,?,?,?,?,?,?)",
                tuple(receipt.values()),
            )
        return receipt

    def spend_usd(self) -> dict[str, float]:
        result = {"aws": 0.0, "google": 0.0}
        with sqlite3.connect(self.path) as db:
            for cloud, amount in db.execute(
                "SELECT cloud,COALESCE(SUM(estimated_cost_usd),0) FROM model_usage GROUP BY cloud"
            ):
                if cloud in result:
                    result[cloud] = float(amount or 0)
        return result


def budget_snapshot(ledger: ModelUsageLedger | None = None) -> dict[str, object]:
    ledger = ledger or ModelUsageLedger()
    spent = ledger.spend_usd()
    fx = float(settings.budget_usd_krw)
    credits = {
        "google": float(settings.google_credit_krw),
        "aws": float(settings.aws_credit_krw),
    }
    clouds = {}
    for cloud in ("google", "aws"):
        spent_krw = spent[cloud] * fx
        initial = credits[cloud]
        remaining = max(0.0, initial - spent_krw)
        clouds[cloud] = {
            "initial_credit_krw": round(initial, 2),
            "estimated_spend_krw": round(spent_krw, 2),
            "estimated_remaining_krw": round(remaining, 2),
            "remaining_ratio": round(remaining / initial, 6) if initial > 0 else 0.0,
        }
    return {
        "planning_fx_krw_per_usd": fx,
        "clouds": clouds,
        "warning": "Local token-cost ledger is an estimate and does not replace Google Cloud or AWS billing.",
    }


def _remaining_ratio(cloud: Cloud, ledger: ModelUsageLedger | None = None) -> float:
    return float(budget_snapshot(ledger)["clouds"][cloud]["remaining_ratio"])


USER_MODEL_KEYS = {
    "gpt-5.6-luna": "gpt_luna",
    "gemini-3.8-flash": "gemini_flash",
    "gemini-3.1-pro-preview": "gemini_pro",
    "claude-haiku-4.5": "claude_haiku",
    "claude-sonnet-5": "claude_sonnet",
    "claude-opus-5": "claude_opus",
}


def select_user_model(
    requested_model: str,
    *,
    contains_personal_data: bool,
) -> RouteDecision:
    """Honor an explicit user model choice without cost-based substitution.

    Privacy and provider-availability gates are enforced by the execution layer.
    This function only prevents the automatic cost router from silently changing
    a model selected by the user.
    """
    key = USER_MODEL_KEYS.get(requested_model.strip().lower())
    if key is None:
        raise ValueError("UNSUPPORTED_USER_MODEL")
    spec = _specs()[key]
    privacy_notice = spec.region_notice
    if contains_personal_data and spec.privacy_class == "NON_PERSONAL_ONLY":
        privacy_notice = (
            "The user explicitly selected a model that is not an EduRecover "
            "privacy-preferred route. Do not silently substitute another model; "
            "surface the privacy warning and enforce the configured retention gate."
        )
    return RouteDecision(
        spec.provider,
        spec.model,
        spec.cloud,
        "User explicitly selected this model; automatic cost routing bypassed.",
        privacy_notice,
        False,
    )


def select_route(
    *,
    contains_personal_data: bool,
    quality_tier: QualityTier = "low",
    reported_gpt_issue: GPTIssue | None = None,
    ledger: ModelUsageLedger | None = None,
) -> RouteDecision:
    specs = _specs()

    if reported_gpt_issue is not None:
        # Owner rule: once GPT hallucination/refusal/false-report behavior is
        # observed, the next run must not use GPT.
        if contains_personal_data:
            key = "claude_haiku" if quality_tier == "low" else (
                "claude_sonnet" if quality_tier == "standard" else "claude_opus"
            )
        else:
            # Use the larger remaining promotional-credit pool for cheap recovery.
            key = "gemini_flash" if _remaining_ratio("google", ledger) >= _remaining_ratio("aws", ledger) else "claude_haiku"
        spec = specs[key]
        return RouteDecision(
            spec.provider,
            spec.model,
            spec.cloud,
            f"GPT issue reported ({reported_gpt_issue}); GPT excluded for retry.",
            spec.region_notice,
            True,
        )

    if contains_personal_data:
        key = {
            "low": "claude_haiku",
            "standard": "claude_sonnet",
            "high": "claude_opus",
        }[quality_tier]
        spec = specs[key]
        return RouteDecision(
            spec.provider,
            spec.model,
            spec.cloud,
            f"Privacy-sensitive {quality_tier} task routed to Claude tier.",
            spec.region_notice,
            False,
        )

    if quality_tier == "low":
        spec = specs["gpt_luna"]
        return RouteDecision(
            spec.provider,
            spec.model,
            spec.cloud,
            "Non-personal low-cost task routed to GPT-5.6 Luna.",
            spec.region_notice,
            False,
        )

    if quality_tier == "standard":
        # Google has the larger initial credit allocation; prefer it while the
        # estimated remaining ratio is not below AWS.
        key = "gemini_pro" if _remaining_ratio("google", ledger) >= _remaining_ratio("aws", ledger) else "claude_sonnet"
        spec = specs[key]
        return RouteDecision(
            spec.provider,
            spec.model,
            spec.cloud,
            "Standard non-personal task routed by remaining cloud-credit ratio.",
            spec.region_notice,
            False,
        )

    spec = specs["claude_opus"]
    return RouteDecision(
        spec.provider,
        spec.model,
        spec.cloud,
        "High-quality task routed to Claude Opus 5.",
        spec.region_notice,
        False,
    )


def record_route_usage(
    decision: RouteDecision,
    *,
    input_tokens: int,
    output_tokens: int,
    ledger: ModelUsageLedger | None = None,
) -> dict[str, object]:
    ledger = ledger or ModelUsageLedger()
    specs = _specs()
    spec = next(
        (item for item in specs.values() if item.provider == decision.provider and item.model == decision.model),
        None,
    )
    if spec is None:
        raise RuntimeError("UNKNOWN_ROUTED_MODEL")
    return ledger.record(
        spec=spec,
        route_reason=decision.reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def router_status() -> dict[str, object]:
    return {
        "server_only": True,
        "client_side_router_installed": False,
        "aws_region": settings.aws_region,
        "default_credits_krw": {
            "google": settings.google_credit_krw,
            "aws": settings.aws_credit_krw,
        },
        "models": {name: asdict(spec) for name, spec in _specs().items()},
        "user_selectable_models": sorted(USER_MODEL_KEYS),
        "explicit_user_selection_bypasses_auto_cost_routing": True,
        "budget": budget_snapshot(),
        "gpt_quality_failover": [
            "hallucination",
            "overclaim",
            "false_report",
            "lie",
            "instruction_refusal",
            "invalid_output",
            "other_quality_failure",
        ],
    }
