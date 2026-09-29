from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ProgramDomain = Literal["MEDICAL", "WELFARE", "LEGAL", "LIFE"]
ProgramState = Literal["OPEN", "UPCOMING", "CLOSED", "UNKNOWN"]


@dataclass(frozen=True)
class PublicSupportProgram:
    program_id: str
    domain: ProgramDomain
    title: str
    provider: str
    official_source_ref: str
    state: ProgramState
    application_start: str | None = None
    application_end: str | None = None
    eligibility_text: str | None = None
    location_text: str | None = None
    reservation_required: bool | None = None


def is_actionable(program: PublicSupportProgram) -> bool:
    """Only official-source programs in an actionable state may surface as apply-now."""
    return bool(
        program.official_source_ref
        and program.state in {"OPEN", "UPCOMING"}
    )
