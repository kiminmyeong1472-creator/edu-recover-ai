from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


EvidenceKind = Literal["FACT", "CLAIM", "INFERENCE", "DOCUMENT", "AUDIO", "IMAGE", "VIDEO"]


@dataclass
class CaseEvidence:
    evidence_id: str
    case_id: str
    subject_id: str
    kind: EvidenceKind
    source_ref: str
    revision_id: str
    sha256: str | None = None
    reviewed_state: Literal["PRELIMINARY", "ATTORNEY_REVIEWED"] = "PRELIMINARY"
