from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class ConsentReceipt:
    subject_id: str
    domain: str
    purpose: str
    policy_version: str
    granted_at: str

    @classmethod
    def issue(cls, *, subject_id: str, domain: str, purpose: str, policy_version: str) -> "ConsentReceipt":
        return cls(
            subject_id=subject_id,
            domain=domain,
            purpose=purpose,
            policy_version=policy_version,
            granted_at=datetime.now(timezone.utc).isoformat(),
        )
