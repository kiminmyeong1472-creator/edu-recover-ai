from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DomainScope(StrEnum):
    EDUCATION = "education"
    LIFE = "life"
    MEDICAL = "medical"
    LEGAL = "legal"


@dataclass(frozen=True)
class DomainContext:
    scope: DomainScope
    subject_id: str
    consent_version: str | None = None


def require_domain(expected: DomainScope, actual: DomainScope) -> None:
    """Fail closed when a request attempts to cross domain boundaries."""
    if actual != expected:
        raise PermissionError(
            f"domain_scope_mismatch: expected={expected.value} actual={actual.value}"
        )
