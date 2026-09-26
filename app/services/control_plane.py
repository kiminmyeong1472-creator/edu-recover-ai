"""Owner-sovereignty control bridge for EduRecover AI.

The control plane governs AI-provider execution only. It must never be used to
lock out the human owner/operator or to take custody of API secret values.

Secrets stay in the deployment environment. This module sees only whether a
secret is configured through the existing Settings object; it never returns
secret values to a model prompt or an API response.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


class ProviderHeld(RuntimeError):
    """Raised when the owner control manifest holds an AI provider action."""


@dataclass(frozen=True)
class ControlDecision:
    allowed: bool
    provider: str
    action: str
    state: str
    reason: str
    control_epoch: str


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv_env(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _manifest_from_env() -> dict[str, Any]:
    raw = os.getenv("EDURECOVER_CONTROL_MANIFEST_JSON", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("EDURECOVER_CONTROL_MANIFEST_JSON is invalid JSON.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("EDURECOVER_CONTROL_MANIFEST_JSON must be a JSON object.")
    return value


def _normalized_manifest() -> dict[str, Any]:
    manifest = _manifest_from_env()
    blocked = set(str(v).strip().lower() for v in manifest.get("blocked_providers", []) if str(v).strip())
    restricted = set(str(v).strip().lower() for v in manifest.get("restricted_providers", []) if str(v).strip())
    blocked |= _csv_env("EDURECOVER_BLOCKED_PROVIDERS")
    restricted |= _csv_env("EDURECOVER_RESTRICTED_PROVIDERS")
    allowed = set(str(v).strip().lower() for v in manifest.get("allowed_providers", []) if str(v).strip())
    allowed |= _csv_env("EDURECOVER_ALLOWED_PROVIDERS")
    require_explicit_allow = bool(
        manifest.get(
            "require_explicit_provider_allow",
            _bool_env("EDURECOVER_REQUIRE_EXPLICIT_PROVIDER_ALLOW", True),
        )
    )

    hold_all = bool(manifest.get("hold_all_ai", False)) or _bool_env("EDURECOVER_AI_HOLD", False)
    epoch = str(
        manifest.get("control_epoch")
        or os.getenv("EDURECOVER_CONTROL_EPOCH")
        or "UNSYNCED"
    )

    return {
        "control_epoch": epoch,
        "hold_all_ai": hold_all,
        "blocked_providers": sorted(blocked),
        "restricted_providers": sorted(restricted),
        "allowed_providers": sorted(allowed),
        "require_explicit_provider_allow": require_explicit_allow,
        "require_cross_verification": bool(
            manifest.get(
                "require_cross_verification",
                _bool_env("EDURECOVER_REQUIRE_CROSS_VERIFICATION", True),
            )
        ),
        "owner_sovereignty": {
            "human_owner_can_be_auto_blocked": False,
            "ai_control_targets": "AI provider routing, automated execution, validation requirements, and provider privileges only",
            "api_secret_custody": "deployment_environment_only",
        },
        "source": str(manifest.get("source") or "owner-controlled Railway environment / pending Drive sync"),
        "sync_state": "SYNCED_MANIFEST" if manifest else "WAITING_OWNER_MANIFEST_OR_DRIVE_BRIDGE",
    }


def control_status() -> dict[str, Any]:
    """Return the non-secret owner control status."""
    return _normalized_manifest()


def evaluate_provider(provider: str, action: str) -> ControlDecision:
    provider = provider.strip().lower()
    action = action.strip().lower()
    manifest = _normalized_manifest()
    epoch = manifest["control_epoch"]

    if manifest["hold_all_ai"]:
        return ControlDecision(False, provider, action, "HOLD", "all AI automation is held by owner control", epoch)
    if manifest["require_explicit_provider_allow"] and provider not in manifest["allowed_providers"]:
        return ControlDecision(
            False,
            provider,
            action,
            "OWNER_PERMISSION_REQUIRED",
            "provider channel is not on the owner allow-list",
            epoch,
        )
    if provider in manifest["blocked_providers"]:
        return ControlDecision(False, provider, action, "BLOCKED", "provider is blocked by owner control", epoch)
    if provider in manifest["restricted_providers"] and action == "generate":
        return ControlDecision(
            False,
            provider,
            action,
            "RESTRICTED_VERIFY_ONLY",
            "provider is restricted to verification-only use by owner control",
            epoch,
        )
    return ControlDecision(True, provider, action, "ALLOWED", "owner control permits this AI action", epoch)


def assert_provider_allowed(provider: str, action: str) -> ControlDecision:
    decision = evaluate_provider(provider, action)
    if not decision.allowed:
        raise ProviderHeld(
            f"{provider} {action} is held by owner control ({decision.state}; epoch={decision.control_epoch})."
        )
    return decision


def notebook_tasks() -> list[dict[str, str]]:
    """Return owner-only setup tasks without ever requesting secret values in chat."""
    return [
        {
            "step": "1",
            "title": "Railway 서버 환경변수에 AI API 키 직접 입력",
            "detail": (
                "EduRecover-v3.24.9 > edurecover-prod > Variables에서 "
                "각 AI 회사의 API 키는 필요한 제공자만 직접 입력합니다. "
                "키 값은 AI 대화창이나 문서에 붙여넣지 않습니다."
            ),
        },
        {
            "step": "2",
            "title": "모델명 확인",
            "detail": (
                "각 허용 AI 채널의 모델 설정을 실제 사용 가능한 모델명으로 확인합니다."
            ),
        },
        {
            "step": "3",
            "title": "Google Drive 통제원장과 플랫폼 통제매니페스트 결속",
            "detail": (
                "현재 Drive 상태원장의 최신 control_epoch/block_state를 사람이 확인한 뒤 "
                "EDURECOVER_CONTROL_MANIFEST_JSON에 비밀값 없이 반영합니다. "
                "자동 동기화는 별도 인증 브리지를 설치하기 전까지 완료로 표시하지 않습니다."
            ),
        },
        {
            "step": "4",
            "title": "백업 암호화 마스터키 직접 입력",
            "detail": (
                "Railway edurecover-prod의 Sealed 환경변수 EDURECOVER_BACKUP_MASTER_KEY에 "
                "32바이트 무작위 암호화키를 사용자가 직접 입력합니다. "
                "이 키는 OpenAI/Gemini/Claude API 키와 완전히 분리하며 채팅·Drive·GitHub에 기록하지 않습니다."
            ),
        },
        {
            "step": "5",
            "title": "배포 후 상태 확인",
            "detail": (
                "/owner/ai-status, /control-status, /privacy-status, /owner/backup-encryption-status를 확인하고 "
                "실제 비밀값이 어떤 응답에도 노출되지 않는지 확인합니다."
            ),
        },
    ]
