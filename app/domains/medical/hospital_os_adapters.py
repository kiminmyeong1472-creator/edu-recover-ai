from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

AdapterKind = Literal["FHIR", "HL7", "EMR", "PACS", "LIS", "PHARMACY", "IDENTITY", "PUBLIC_DATA"]


@dataclass(frozen=True)
class AdapterEnvelope:
    adapter_kind: AdapterKind
    source_system: str
    external_id: str
    patient_external_id: str | None
    source_type: str
    occurred_at: str | None
    payload: dict
    verification_state: str


class HospitalSourceAdapter(Protocol):
    adapter_kind: AdapterKind

    def normalize(self, raw: dict) -> AdapterEnvelope:
        ...


class DictAdapter:
    """Vendor-neutral adapter skeleton.

    Production connectors must provide explicit field mappings and authentication.
    This class deliberately performs no network call and makes no clinical inference.
    """

    def __init__(self, *, adapter_kind: AdapterKind, source_system: str) -> None:
        self.adapter_kind = adapter_kind
        self.source_system = source_system

    def normalize(self, raw: dict) -> AdapterEnvelope:
        external_id = str(raw.get("external_id") or "").strip()
        source_type = str(raw.get("source_type") or "").strip()
        if not external_id or not source_type:
            raise ValueError("adapter_required_fields_missing")
        verification_state = str(raw.get("verification_state") or "UNVERIFIED").strip()
        if verification_state not in {"VERIFIED", "UNVERIFIED", "UNRESOLVED"}:
            raise ValueError("adapter_verification_state_invalid")
        return AdapterEnvelope(
            adapter_kind=self.adapter_kind,
            source_system=self.source_system,
            external_id=external_id,
            patient_external_id=(
                str(raw["patient_external_id"]).strip()
                if raw.get("patient_external_id") is not None
                else None
            ),
            source_type=source_type,
            occurred_at=(
                str(raw["occurred_at"]).strip()
                if raw.get("occurred_at") is not None
                else None
            ),
            payload=dict(raw.get("payload") or {}),
            verification_state=verification_state,
        )
