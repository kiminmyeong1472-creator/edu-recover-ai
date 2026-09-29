from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class ActivityCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=1)
    domain: Literal["life","medical","legal"]
    action: str = Field(min_length=1)
    resource_ref: str = Field(min_length=1)
    source_ref: str | None = None


class PublicSupportProgramResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    program_id: str
    domain: Literal["MEDICAL","WELFARE","LEGAL","LIFE"]
    title: str
    provider: str
    official_source_ref: str
    state: Literal["OPEN","UPCOMING","CLOSED","UNKNOWN"]
    actionable: bool
