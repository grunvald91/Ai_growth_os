from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SourceType = Literal["owner_message", "owner_file", "owned_website", "official_registry",
                     "public_third_party", "existing_profile"]


class IntakeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntakeSource(IntakeModel):
    id: str
    source_type: SourceType
    reference: str
    observed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    text: str = ""
    status: Literal["read", "unsupported", "unavailable", "truncated"] = "read"
    note: str = ""
    filename: str | None = None
    sha256: str | None = None


class ExtractedClaim(IntakeModel):
    field_path: str
    proposed_value: Any
    source_id: str
    evidence_excerpt: str = Field(min_length=1, max_length=1000)
    kind: Literal["candidate", "hypothesis", "unknown"] = "candidate"


class IntakeExtraction(IntakeModel):
    claims: list[ExtractedClaim] = Field(default_factory=list, max_length=100)


class IntakeError(ValueError):
    pass
