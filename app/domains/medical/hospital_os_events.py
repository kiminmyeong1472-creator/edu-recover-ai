from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import uuid
from typing import Literal

HospitalEventType = Literal[
    "source.revision.changed",
    "prescription.revision.changed",
    "medication.prepared",
    "medication.dispensed",
    "medication.received",
    "medication.administered",
    "identity.verified",
    "report.delivered",
    "handoff.created",
    "consult.requested",
    "consult.responded",
    "test.resulted",
    "imaging.reported",
    "preop.review.updated",
    "claim.review.updated",
    "operations.inventory.updated",
]


@dataclass(frozen=True)
class HospitalEvent:
    event_id: str
    event_type: HospitalEventType
    patient_id: str | None
    actor_id: str
    occurred_at: str
    payload: dict


def make_hospital_event(
    *,
    event_type: HospitalEventType,
    actor_id: str,
    patient_id: str | None = None,
    payload: dict | None = None,
) -> HospitalEvent:
    return HospitalEvent(
        event_id=f"evt_{uuid.uuid4().hex}",
        event_type=event_type,
        patient_id=patient_id,
        actor_id=actor_id,
        occurred_at=datetime.now(timezone.utc).isoformat(),
        payload=dict(payload or {}),
    )


def event_payload(event: HospitalEvent) -> dict:
    return asdict(event)
