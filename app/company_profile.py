"""Company Profile Contract v1.

Verified business inputs live in typed section models. Hypotheses and unknowns
are separate record types and cannot be substituted for verified facts.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VerifiedFact(_Strict):
    """A fact treated as verified input. Not a hypothesis or an unknown."""

    statement: str
    evidence_ref: str | None = None
    fact_type: Literal["verified"] = "verified"


class Identity(_Strict):
    legal_name: str
    display_name: str | None = None
    website: str | None = None
    industry: str | None = None
    geography: str | None = None


class Offer(_Strict):
    name: str
    description: str
    category: str | None = None


class CustomerSegment(_Strict):
    name: str
    description: str | None = None


class Customers(_Strict):
    segments: list[CustomerSegment] = Field(default_factory=list)
    ideal_customer: str | None = None


class Commercial(_Strict):
    pricing_model: str | None = None
    currency: str | None = None
    average_deal_value: str | None = None


class Proof(_Strict):
    facts: list[VerifiedFact] = Field(default_factory=list)


class Brand(_Strict):
    voice: str | None = None
    positioning: str | None = None
    prohibited_claims: list[str] = Field(default_factory=list)


class Channel(_Strict):
    name: str
    role: str | None = None


class Channels(_Strict):
    items: list[Channel] = Field(default_factory=list)


class Outreach(_Strict):
    motion: str | None = None
    notes: list[str] = Field(default_factory=list)


class Metric(_Strict):
    name: str
    definition: str | None = None


class Measurement(_Strict):
    metrics: list[Metric] = Field(default_factory=list)


class Constraints(_Strict):
    items: list[str] = Field(default_factory=list)


class Hypothesis(_Strict):
    """Unverified assumption. Structurally distinct from VerifiedFact."""

    statement: str
    rationale: str | None = None
    record_type: Literal["hypothesis"] = "hypothesis"


class Unknown(_Strict):
    """Open question. Structurally distinct from VerifiedFact and Hypothesis."""

    question: str
    record_type: Literal["unknown"] = "unknown"


class CompanyProfile(_Strict):
    schema_version: Literal["1.0"] = "1.0"
    identity: Identity
    offer: Offer
    customers: Customers = Field(default_factory=Customers)
    commercial: Commercial = Field(default_factory=Commercial)
    proof: Proof = Field(default_factory=Proof)
    brand: Brand = Field(default_factory=Brand)
    channels: Channels = Field(default_factory=Channels)
    outreach: Outreach = Field(default_factory=Outreach)
    measurement: Measurement = Field(default_factory=Measurement)
    constraints: Constraints = Field(default_factory=Constraints)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    unknowns: list[Unknown] = Field(default_factory=list)


def parse_company_profile(data: dict) -> CompanyProfile:
    """Parse and validate a Company Profile v1 payload."""
    return CompanyProfile.model_validate(data)
