"""Signed, content-bound authoring provenance; never contains source text or keys.

Proofs use the existing owner session signing key with a separate domain. They
expire after seven days and become invalid on restart or owner credential change,
just like owner sessions. Revisions preserve the original generation provider.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from typing import Any

from app.services.owner_basic_auth import _credentials, _sign

_DOMAIN = "edurecover-authoring-provenance-v1:"
_TTL = 7 * 24 * 3600
_PROVIDERS = {"openai", "gemini", "claude"}


class ProvenanceError(ValueError):
    """A missing, forged, stale or mismatched authoring proof."""


def problem_digest(problems: list[dict[str, Any]]) -> str:
    raw = json.dumps(problems, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _issue(claims: dict[str, Any]) -> str:
    users, password = _credentials()
    if not users or not password:
        raise ProvenanceError("출제 출처를 확인할 관리자 인증 설정이 없습니다.")
    now = int(time.time())
    claims = {"v": 1, "issued_at": now, "expires_at": now + _TTL, **claims}
    raw = json.dumps(claims, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return encoded + "." + _sign(_DOMAIN + encoded, users, password)


def _verify(token: str, purpose: str) -> dict[str, Any]:
    invalid = "출제·검증 출처 증명이 없거나 유효하지 않습니다. 현재 문항의 출처를 다시 확인해 주세요."
    try:
        if not isinstance(token, str) or not 64 <= len(token) <= 4096:
            raise ValueError
        encoded, signature = token.split(".")
        users, password = _credentials()
        if not users or not password or len(signature) != 64:
            raise ValueError
        if not secrets.compare_digest(signature, _sign(_DOMAIN + encoded, users, password)):
            raise ValueError
        claims = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
        now = int(time.time())
        if (not isinstance(claims, dict) or claims.get("v") != 1 or claims.get("purpose") != purpose
                or claims.get("provider") not in _PROVIDERS
                or type(claims.get("expires_at")) is not int
                or type(claims.get("issued_at")) is not int
                or not claims["issued_at"] <= now < claims["expires_at"]
                or claims["expires_at"] - claims["issued_at"] != _TTL
                or not isinstance(claims.get("invocation_id"), str)
                or not isinstance(claims.get("generation_invocation_id"), str)
                or not isinstance(claims.get("problem_sha256"), str)):
            raise ValueError
        return claims
    except (ValueError, TypeError, UnicodeError, KeyError) as exc:
        raise ProvenanceError(invalid) from exc


def issue_origin_proof(provider: str, invocation_id: str, problems: list[dict[str, Any]],
                       generation_invocation_id: str | None = None) -> str:
    if provider not in _PROVIDERS:
        raise ProvenanceError("지원되지 않는 출제 공급자입니다.")
    return _issue({"purpose": "generation_origin", "provider": provider,
                   "invocation_id": invocation_id,
                   "generation_invocation_id": generation_invocation_id or invocation_id,
                   "problem_sha256": problem_digest(problems)})


def verify_origin_proof(token: str, *, provider: str | None = None,
                        invocation_id: str | None = None,
                        problems: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    claims = _verify(token, "generation_origin")
    if ((provider is not None and claims["provider"] != provider)
            or (invocation_id is not None and claims["invocation_id"] != invocation_id)
            or (problems is not None and claims["problem_sha256"] != problem_digest(problems))):
        raise ProvenanceError("출제 공급자·실행 기록 또는 문항이 출처 증명과 일치하지 않습니다. 편집한 문항은 수정 기록을 먼저 저장해 주세요.")
    return claims


def issue_verification_proof(provider: str, invocation_id: str, problems: list[dict[str, Any]],
                             origin_claims: dict[str, Any], verdict: str) -> str:
    if (verdict != "PASS" or provider not in _PROVIDERS or provider == origin_claims["provider"]
            or problem_digest(problems) != origin_claims["problem_sha256"]):
        raise ProvenanceError("독립 검증을 통과한 동일 문항에만 검증 증명을 발급할 수 있습니다.")
    return _issue({"purpose": "verification", "verdict": "PASS", "provider": provider,
                   "invocation_id": invocation_id,
                   "generation_invocation_id": origin_claims["generation_invocation_id"],
                   "origin_invocation_id": origin_claims["invocation_id"],
                   "origin_provider": origin_claims["provider"],
                   "problem_sha256": problem_digest(problems)})


def verify_verification_proof(token: str, *, problems: list[dict[str, Any]],
                              origin_claims: dict[str, Any]) -> dict[str, Any]:
    claims = _verify(token, "verification")
    if (claims.get("verdict") != "PASS" or claims["provider"] == origin_claims["provider"]
            or claims.get("origin_provider") != origin_claims["provider"]
            or claims.get("origin_invocation_id") != origin_claims["invocation_id"]
            or claims["generation_invocation_id"] != origin_claims["generation_invocation_id"]
            or claims["problem_sha256"] != origin_claims["problem_sha256"]
            or claims["problem_sha256"] != problem_digest(problems)):
        raise ProvenanceError("현재 문항과 원출제 기록에 결속된 독립 검증 통과 증명이 아닙니다.")
    return claims
