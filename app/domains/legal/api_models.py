from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class CaseEventCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    stage: Literal["INTAKE","POLICE","PROSECUTION","COURT","ADMINISTRATIVE","CLOSED"]
    kind: Literal["FACT","FILING","NOTICE","DEADLINE","EVIDENCE","DECISION","CONSULTATION"]
    title: str = Field(min_length=1)
    occurred_at: str | None = None
    source_ref: str | None = None


class LegalDeadlineCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    due_at: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    legal_basis_ref: str | None = None


class LegalDeadlineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deadline_id: str
    state: Literal["UNVERIFIED","VERIFIED","COMPLETED","MISSED","HOLD"]
    due_at: str
    source_ref: str
