from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class ConsentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=1)
    domain: Literal["life", "medical", "legal"]
    purpose: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)


class SharingGrantCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=1)
    source_domain: Literal["life", "medical", "legal"]
    target_party: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    resource_refs: list[str] = Field(min_length=1)
    expires_at: str = Field(min_length=1)


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str
    domain: Literal["life", "medical", "legal"]
    action: str
    resource_id: str
    occurred_at: str
