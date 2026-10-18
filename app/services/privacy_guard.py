"""Opt-in provider-retention gate and factual approval ledger for EduRecover AI."""

from __future__ import annotations

import os
from typing import Any


class ProviderZDRHeld(RuntimeError):
    """Raised when strict provider-side retention requirements are not confirmed."""


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    # Unknown values neither opt in to enforcement nor confirm an approval.
    return default


_PROVIDER_SETTINGS = {
    "openai": ("EDURECOVER_OPENAI_ZDR_CONFIRMED", "EDURECOVER_OPENAI_ZDR_BASIS"),
    "gemini": ("EDURECOVER_GEMINI_ZDR_CONFIRMED", "EDURECOVER_GEMINI_ZDR_BASIS"),
    "claude": ("EDURECOVER_CLAUDE_ZDR_CONFIRMED", "EDURECOVER_CLAUDE_ZDR_BASIS"),
}


def privacy_status() -> dict[str, Any]:
    # Owner temporarily disabled ZDR blocking. Retain approval facts, but require
    # a subsequent explicit true flag before this gate can block provider calls.
    required = _bool_env("EDURECOVER_REQUIRE_ZDR", False)
    providers: dict[str, Any] = {}
    for provider, (flag_name, basis_name) in _PROVIDER_SETTINGS.items():
        confirmed = _bool_env(flag_name, False)
        basis = os.getenv(basis_name, "").strip()
        providers[provider] = {
            "zdr_confirmed": confirmed,
            "basis_recorded": bool(basis),
            "call_allowed_by_privacy_gate": (not required) or (confirmed and bool(basis)),
        }
    return {
        "strict_provider_zdr_required": required,
        "fail_closed": required,
        "enforcement_mode": "explicit_operator_opt_in" if required else "disabled_by_owner",
        "platform_chat_history_forwarded": False,
        "provider_calls_are_stateless": True,
        "providers": providers,
        "note": (
            "Disabling enforcement does not enable provider-side ZDR or change account retention. "
            "Application approval flags are only an operator attestation. Provider-side ZDR must be enabled, "
            "approved, contracted, or otherwise formally covered for the exact organization/project, "
            "endpoint and model before the corresponding flag is set true."
        ),
    }


def assert_provider_zdr(provider: str) -> None:
    normalized = provider.strip().lower()
    if normalized not in _PROVIDER_SETTINGS:
        raise ProviderZDRHeld(f"Unknown provider channel: {provider}")
    status = privacy_status()
    if not status["strict_provider_zdr_required"]:
        return
    provider_status = status["providers"][normalized]
    if not provider_status["zdr_confirmed"] or not provider_status["basis_recorded"]:
        raise ProviderZDRHeld(
            f"{normalized} channel is HOLD: provider-side ZDR/retention coverage is not confirmed."
        )
