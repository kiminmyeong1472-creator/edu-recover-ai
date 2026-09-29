from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
import uuid


ShareDomain = Literal["life", "medical", "legal"]


@dataclass(frozen=True)
class SharingGrant:
    grant_id: str
    subject_id: str
    source_domain: ShareDomain
    target_party: str
    purpose: str
    resource_refs: tuple[str, ...]
    issued_at: str
    expires_at: str
    revoked: bool = False

    @classmethod
    def issue(
        cls,
        *,
        subject_id: str,
        source_domain: ShareDomain,
        target_party: str,
        purpose: str,
        resource_refs: list[str],
        expires_at: str,
    ) -> "SharingGrant":
        if not resource_refs:
            raise ValueError("resource_refs_required")
        return cls(
            grant_id=f"share_{uuid.uuid4().hex}",
            subject_id=subject_id,
            source_domain=source_domain,
            target_party=target_party,
            purpose=purpose,
            resource_refs=tuple(resource_refs),
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=expires_at,
        )


def revoke(grant: SharingGrant) -> SharingGrant:
    return SharingGrant(
        grant_id=grant.grant_id,
        subject_id=grant.subject_id,
        source_domain=grant.source_domain,
        target_party=grant.target_party,
        purpose=grant.purpose,
        resource_refs=grant.resource_refs,
        issued_at=grant.issued_at,
        expires_at=grant.expires_at,
        revoked=True,
    )
