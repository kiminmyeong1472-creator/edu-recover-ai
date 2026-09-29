from __future__ import annotations

from collections.abc import Callable

from fastapi import Header, HTTPException

from app.platform_core.domain_scope import DomainScope, require_domain


def require_subject_header(
    expected_subject_id: str,
    x_subject_id: str | None,
) -> None:
    if not x_subject_id or x_subject_id != expected_subject_id:
        raise HTTPException(status_code=403, detail="subject_scope_mismatch")


def require_domain_value(
    expected: DomainScope,
    x_domain_scope: str | None,
) -> DomainScope:
    if not x_domain_scope:
        raise HTTPException(status_code=403, detail="domain_scope_required")
    try:
        actual = DomainScope(x_domain_scope)
        require_domain(expected, actual)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403, detail="domain_scope_mismatch") from exc
    return actual


def domain_guard(expected: DomainScope) -> Callable[..., DomainScope]:
    """Build a FastAPI dependency that validates X-Domain-Scope at request time."""

    def _guard(
        x_domain_scope: str | None = Header(default=None, alias="X-Domain-Scope"),
    ) -> DomainScope:
        return require_domain_value(expected, x_domain_scope)

    return _guard
