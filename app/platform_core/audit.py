from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class AuditEvent:
    actor_id: str
    domain: str
    action: str
    resource_id: str
    occurred_at: str

    @classmethod
    def record(cls, *, actor_id: str, domain: str, action: str, resource_id: str) -> "AuditEvent":
        return cls(
            actor_id=actor_id,
            domain=domain,
            action=action,
            resource_id=resource_id,
            occurred_at=datetime.now(timezone.utc).isoformat(),
        )
