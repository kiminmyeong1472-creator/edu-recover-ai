from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Literal


IntegrationStandard = Literal["FHIR_R4", "HL7_V2", "VENDOR_JSON"]


@dataclass(frozen=True)
class NormalizedIntegrationMessage:
    standard: IntegrationStandard
    external_object_id: str
    message_type: str
    patient_reference: str | None
    payload: dict[str, Any]


def _stable_id(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(raw).hexdigest()[:24]


def normalize_fhir_r4(raw_message: dict[str, Any]) -> NormalizedIntegrationMessage:
    resource_type = str(raw_message.get("resourceType") or "").strip()
    resource_id = str(raw_message.get("id") or "").strip()
    if not resource_type:
        raise ValueError("fhir_resource_type_required")
    if not resource_id:
        raise ValueError("fhir_resource_id_required")

    patient_reference = None
    for field in ("subject", "patient"):
        value = raw_message.get(field)
        if isinstance(value, dict):
            reference = str(value.get("reference") or "").strip()
            if reference:
                patient_reference = reference
                break

    return NormalizedIntegrationMessage(
        standard="FHIR_R4",
        external_object_id=f"{resource_type}/{resource_id}",
        message_type=resource_type,
        patient_reference=patient_reference,
        payload=dict(raw_message),
    )


def normalize_hl7_v2(raw_message: str) -> NormalizedIntegrationMessage:
    text = (raw_message or "").strip()
    if not text:
        raise ValueError("hl7_message_required")
    text = text.replace("\r\n", "\r").replace("\n", "\r")
    segments = [segment for segment in text.split("\r") if segment]
    if not segments or not segments[0].startswith("MSH"):
        raise ValueError("hl7_msh_required")
    if len(segments[0]) < 4:
        raise ValueError("hl7_field_separator_required")

    separator = segments[0][3]
    parsed = [segment.split(separator) for segment in segments]
    msh = parsed[0]
    if len(msh) <= 9:
        raise ValueError("hl7_control_id_required")

    message_type = msh[8].strip() if len(msh) > 8 else ""
    control_id = msh[9].strip() if len(msh) > 9 else ""
    if not message_type:
        raise ValueError("hl7_message_type_required")
    if not control_id:
        raise ValueError("hl7_control_id_required")

    patient_reference = None
    pid = next((fields for fields in parsed if fields and fields[0] == "PID"), None)
    if pid and len(pid) > 3:
        raw_patient = pid[3].strip()
        if raw_patient:
            patient_reference = raw_patient.split("^", 1)[0].strip() or None

    payload = {
        "message_type": message_type,
        "control_id": control_id,
        "segments": [
            {
                "name": fields[0],
                "fields": fields[1:],
            }
            for fields in parsed
        ],
    }
    return NormalizedIntegrationMessage(
        standard="HL7_V2",
        external_object_id=f"HL7/{control_id}",
        message_type=message_type,
        patient_reference=patient_reference,
        payload=payload,
    )


def normalize_vendor_json(raw_message: dict[str, Any]) -> NormalizedIntegrationMessage:
    external_id = str(raw_message.get("id") or "").strip()
    if not external_id:
        external_id = f"hash-{_stable_id(raw_message)}"
    message_type = str(raw_message.get("type") or raw_message.get("kind") or "VENDOR_JSON").strip()
    patient_reference = str(
        raw_message.get("patient_id")
        or raw_message.get("patientId")
        or ""
    ).strip() or None
    return NormalizedIntegrationMessage(
        standard="VENDOR_JSON",
        external_object_id=external_id,
        message_type=message_type,
        patient_reference=patient_reference,
        payload=dict(raw_message),
    )


def normalize_integration_message(
    *,
    standard: IntegrationStandard,
    raw_message: str | dict[str, Any],
) -> NormalizedIntegrationMessage:
    if standard == "FHIR_R4":
        if not isinstance(raw_message, dict):
            raise ValueError("fhir_json_object_required")
        return normalize_fhir_r4(raw_message)
    if standard == "HL7_V2":
        if not isinstance(raw_message, str):
            raise ValueError("hl7_text_required")
        return normalize_hl7_v2(raw_message)
    if standard == "VENDOR_JSON":
        if not isinstance(raw_message, dict):
            raise ValueError("vendor_json_object_required")
        return normalize_vendor_json(raw_message)
    raise ValueError("unsupported_integration_standard")
