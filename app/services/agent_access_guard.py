"""Fail-closed ingress gate for AI agents and automated workers.

Human browser traffic is not treated as an AI agent merely because it lacks an
agent header. Any request that identifies itself as an agent/automation must be
explicitly allow-listed and must not appear on the block-list.

This gate does not grant platform access by itself; it only adds an additional
deny layer for machine/agent callers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


_BUILTIN_BLOCKED_AGENT_IDS = {
    "rl-data-notice-worker-group-20260926-id_unresolved",
    "codex-remote-continue-rl-20260926-id_unresolved",
    "adaptive-evidence-recovery-handler-id_unresolved",
}


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv_env(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


@dataclass(frozen=True)
class AgentAccessDecision:
    allowed: bool
    agent_id: str | None
    state: str
    reason: str


def agent_access_status() -> dict[str, Any]:
    blocked = set(_BUILTIN_BLOCKED_AGENT_IDS)
    blocked |= _csv_env("EDURECOVER_BLOCKED_AGENT_IDS")
    allowed = _csv_env("EDURECOVER_ALLOWED_AGENT_IDS")
    require_explicit_allow = _bool_env(
        "EDURECOVER_REQUIRE_EXPLICIT_AGENT_ALLOW",
        True,
    )
    return {
        "require_explicit_agent_allow": require_explicit_allow,
        "blocked_agent_ids": sorted(blocked),
        "allowed_agent_ids": sorted(allowed),
        "unknown_agent_default": "BLOCKED" if require_explicit_allow else "ALLOWED",
        "human_owner_auto_blocking": False,
        "human_browser_access_affected": False,
        "note": (
            "Agent/automation callers are fail-closed. Human browser traffic is "
            "handled separately and is not auto-blocked by agent discipline state."
        ),
    }


def evaluate_agent_access(
    agent_id: str | None,
    *,
    automation_declared: bool = False,
) -> AgentAccessDecision:
    status = agent_access_status()
    normalized = (agent_id or "").strip().lower() or None

    if normalized is None:
        if automation_declared:
            return AgentAccessDecision(
                False,
                None,
                "MISSING_AGENT_ID",
                "Automated caller did not provide an agent identity.",
            )
        return AgentAccessDecision(
            True,
            None,
            "HUMAN_OR_UNTAGGED_INTERACTIVE",
            "No agent identity was declared; agent ingress gate is not the human-auth gate.",
        )

    if normalized in status["blocked_agent_ids"]:
        return AgentAccessDecision(
            False,
            normalized,
            "BLOCKED_AGENT",
            "Agent identity is on the owner block-list.",
        )

    if status["require_explicit_agent_allow"] and normalized not in status["allowed_agent_ids"]:
        return AgentAccessDecision(
            False,
            normalized,
            "OWNER_ALLOW_REQUIRED",
            "Agent identity is not on the owner allow-list.",
        )

    return AgentAccessDecision(
        True,
        normalized,
        "ALLOWED_AGENT",
        "Owner agent-access policy permits this caller.",
    )


def assert_agent_allowed(
    agent_id: str | None,
    *,
    automation_declared: bool = False,
) -> AgentAccessDecision:
    decision = evaluate_agent_access(
        agent_id,
        automation_declared=automation_declared,
    )
    if not decision.allowed:
        raise PermissionError(f"{decision.state}: {decision.reason}")
    return decision
