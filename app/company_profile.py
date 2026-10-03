"""Company Profile Contract v1.

Verified business inputs live in typed section models. Hypotheses and unknowns
are separate record types and cannot be substituted for verified facts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


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


_JSON_BYTE_LIMIT = 150_000
_LEGACY_BYTE_LIMIT = 12_000
_JSON_NAME = "company-profile.json"
_LEGACY_PARTS = (".claude", "product-marketing-context.md")


@dataclass(frozen=True)
class CompanyProfileLoad:
    """Allowlisted load result. profile is set only for validated JSON."""

    source: Literal["json", "legacy_markdown", "missing", "rejected", "invalid"]
    profile: CompanyProfile | None = None
    error: str | None = None
    legacy_markdown: str | None = None


@dataclass(frozen=True)
class GateResult:
    ready: bool
    missing_fields: tuple[str, ...]
    warnings: tuple[str, ...]


def _slug_allowed(slug: str) -> bool:
    return bool(slug) and all(c.isascii() and (c.isalnum() or c == "-") for c in slug)


def _resolve_within(root: Path, candidate: Path) -> Path | None:
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(root):
        return None
    return resolved


def _read_bounded(path: Path, limit: int) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError("could not be read") from exc
    if len(data) > limit:
        raise ValueError("exceeds size bound")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("is not valid UTF-8") from exc


def _read_json_profile(path: Path) -> CompanyProfileLoad:
    try:
        text = _read_bounded(path, _JSON_BYTE_LIMIT)
    except ValueError as exc:
        return CompanyProfileLoad(source="invalid", error=f"company profile {exc}")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return CompanyProfileLoad(source="invalid", error="malformed JSON")
    if not isinstance(payload, dict):
        return CompanyProfileLoad(source="invalid", error="company profile must be a JSON object")
    try:
        profile = parse_company_profile(payload)
    except ValidationError:
        return CompanyProfileLoad(source="invalid", error="validation failed")
    return CompanyProfileLoad(source="json", profile=profile)


def _read_legacy_markdown(path: Path) -> CompanyProfileLoad:
    try:
        text = _read_bounded(path, _LEGACY_BYTE_LIMIT)
    except ValueError as exc:
        return CompanyProfileLoad(source="invalid", error=f"legacy context {exc}")
    return CompanyProfileLoad(source="legacy_markdown", legacy_markdown=text)


def load_company_profile(products_root: Path, slug: str) -> CompanyProfileLoad:
    """Load products/<slug>/company-profile.json, else the legacy markdown file.

    Only those two paths under products_root are eligible. A present JSON file
    is never replaced by the legacy file, and invalid input is not coerced.
    """
    if not _slug_allowed(slug):
        return CompanyProfileLoad(source="rejected", error="slug is not allowlisted")
    root = products_root.resolve()
    json_path = _resolve_within(root, root / slug / _JSON_NAME)
    if json_path is None:
        return CompanyProfileLoad(source="rejected", error="path is outside products root")
    if json_path.is_file():
        return _read_json_profile(json_path)
    legacy_path = _resolve_within(root, root.joinpath(slug, *_LEGACY_PARTS))
    if legacy_path is None:
        return CompanyProfileLoad(source="rejected", error="path is outside products root")
    if legacy_path.is_file():
        return _read_legacy_markdown(legacy_path)
    return CompanyProfileLoad(source="missing")


def _is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


def _gate(missing: list[str], warnings: list[str]) -> GateResult:
    return GateResult(ready=not missing, missing_fields=tuple(missing), warnings=tuple(warnings))


def _strategy_gate(profile: CompanyProfile) -> GateResult:
    missing: list[str] = []
    if _is_blank(profile.identity.legal_name):
        missing.append("identity.legal_name")
    if _is_blank(profile.offer.name):
        missing.append("offer.name")
    if _is_blank(profile.offer.description):
        missing.append("offer.description")
    if _is_blank(profile.customers.ideal_customer):
        missing.append("customers.ideal_customer")
    if _is_blank(profile.brand.positioning):
        missing.append("brand.positioning")
    warnings: list[str] = []
    if not profile.proof.facts:
        warnings.append("proof.facts is empty")
    if profile.hypotheses:
        warnings.append("hypotheses are not verified facts")
    if profile.unknowns:
        warnings.append("unknowns remain open")
    return _gate(missing, warnings)


def _content_gate(profile: CompanyProfile) -> GateResult:
    missing: list[str] = []
    if _is_blank(profile.offer.name):
        missing.append("offer.name")
    if _is_blank(profile.offer.description):
        missing.append("offer.description")
    if _is_blank(profile.brand.voice):
        missing.append("brand.voice")
    if _is_blank(profile.brand.positioning):
        missing.append("brand.positioning")
    warnings: list[str] = []
    if not profile.proof.facts:
        warnings.append("proof.facts is empty")
    return _gate(missing, warnings)


def _outreach_gate(profile: CompanyProfile) -> GateResult:
    missing: list[str] = []
    if _is_blank(profile.offer.name):
        missing.append("offer.name")
    if _is_blank(profile.offer.description):
        missing.append("offer.description")
    if _is_blank(profile.customers.ideal_customer):
        missing.append("customers.ideal_customer")
    if _is_blank(profile.outreach.motion):
        missing.append("outreach.motion")
    if not profile.channels.items or any(_is_blank(item.name) for item in profile.channels.items):
        missing.append("channels.items")
    warnings: list[str] = []
    if profile.unknowns:
        warnings.append("unknowns remain open")
    return _gate(missing, warnings)


def _measurement_gate(profile: CompanyProfile) -> GateResult:
    missing: list[str] = []
    warnings: list[str] = []
    if not profile.measurement.metrics:
        missing.append("measurement.metrics")
    else:
        for index, metric in enumerate(profile.measurement.metrics):
            if _is_blank(metric.name):
                missing.append(f"measurement.metrics[{index}].name")
            elif _is_blank(metric.definition):
                warnings.append(f"measurement.metrics[{index}].definition")
    return _gate(missing, warnings)


def evaluate_readiness(profile: CompanyProfile) -> dict[str, GateResult]:
    """Readiness for strategy, content, outreach, and measurement.

    ready is true only when every required field is present. Warnings never
    fill missing business facts.
    """
    return {
        "strategy": _strategy_gate(profile),
        "content": _content_gate(profile),
        "outreach": _outreach_gate(profile),
        "measurement": _measurement_gate(profile),
    }
