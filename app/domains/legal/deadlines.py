from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
import uuid


DeadlineState = Literal["UNVERIFIED", "VERIFIED", "COMPLETED", "MISSED", "HOLD"]


@dataclass(frozen=True)
class LegalDeadline:
    deadline_id: str
    case_id: str
    subject_id: str
    title: str
    due_at: str
    source_ref: str
    legal_basis_ref: str | None
    state: DeadlineState
    verification_note: str | None = None

    @classmethod
    def create(
        cls,
        *,
        case_id: str,
        subject_id: str,
        title: str,
        due_at: str,
        source_ref: str,
        legal_basis_ref: str | None = None,
    ) -> "LegalDeadline":
        return cls(
            deadline_id=f"ddl_{uuid.uuid4().hex}",
            case_id=case_id,
            subject_id=subject_id,
            title=title,
            due_at=due_at,
            source_ref=source_ref,
            legal_basis_ref=legal_basis_ref,
            state="UNVERIFIED",
        )


def verify_deadline(
    deadline: LegalDeadline,
    *,
    verification_note: str,
) -> LegalDeadline:
    if not verification_note.strip():
        raise ValueError("verification_note_required")
    return LegalDeadline(
        deadline_id=deadline.deadline_id,
        case_id=deadline.case_id,
        subject_id=deadline.subject_id,
        title=deadline.title,
        due_at=deadline.due_at,
        source_ref=deadline.source_ref,
        legal_basis_ref=deadline.legal_basis_ref,
        state="VERIFIED",
        verification_note=verification_note,
    )
