"""Company Profile Contract v1.

Verified business inputs live in typed section models. Hypotheses and unknowns
are separate record types and cannot be substituted for verified facts.
"""

from __future__ import annotations

from typing import Literal
from dataclasses import dataclass
from pathlib import Path
import json
import re

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class VerifiedFact(_Strict):
    """A fact treated as verified input. Not a hypothesis or an unknown."""

    statement: str
    evidence_ref: str | None = None
    fact_type: Literal["verified"] = "verified"


class Identity(_Strict):
    legal_name: str | None = None
    name: str | None = None
    one_liner: str | None = None
    category: str | None = None
    languages: list[str] = Field(default_factory=list)
    service_geography: list[str] = Field(default_factory=list)
    display_name: str | None = None
    website: str | None = None
    industry: str | None = None
    geography: str | None = None


class ServiceOffer(_Strict):
    name: str
    description: str
    pricing: VerifiedFact | None = None


class Offer(_Strict):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    products_services: list[ServiceOffer] = Field(default_factory=list)
    primary_conversion_action: str | None = None


class CustomerSegment(_Strict):
    name: str
    description: str | None = None


class Customers(_Strict):
    primary_segments: list[str] = Field(default_factory=list)
    pains: list[str] = Field(default_factory=list)
    jobs_to_be_done: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    segments: list[CustomerSegment] = Field(default_factory=list)
    ideal_customer: str | None = None


class Commercial(_Strict):
    pricing_model: str | None = None
    currency: str | None = None
    average_deal_value: str | None = None


class Proof(_Strict):
    approved_facts: list[VerifiedFact] = Field(default_factory=list)
    approved_numbers: list[VerifiedFact] = Field(default_factory=list)
    no_proof_yet: bool | None = None
    facts: list[VerifiedFact] = Field(default_factory=list)


class Brand(_Strict):
    tone: list[str] = Field(default_factory=list)
    required_phrases: list[str] = Field(default_factory=list)
    prohibited_phrases: list[str] = Field(default_factory=list)
    voice: str | None = None
    positioning: str | None = None
    prohibited_claims: list[str] = Field(default_factory=list)


class Channel(_Strict):
    name: str
    role: str | None = None


class Channels(_Strict):
    items: list[Channel] = Field(default_factory=list)


class Outreach(_Strict):
    allowed_channels: list[str] = Field(default_factory=list)
    sender_identity: str | None = None
    icp_criteria: list[str] = Field(default_factory=list)
    do_not_contact: list[str] | None = None
    approval_required: bool | None = None
    motion: str | None = None
    notes: list[str] = Field(default_factory=list)


class Metric(_Strict):
    name: str
    definition: str | None = None


class Measurement(_Strict):
    funnel_stages: list[str] = Field(default_factory=list)
    primary_conversion_event: str | None = None
    attribution_rules: list[str] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)


class Constraints(_Strict):
    privacy: list[str] = Field(default_factory=list)
    legal_compliance: list[str] = Field(default_factory=list)
    operational: list[str] = Field(default_factory=list)
    approval_rules: list[str] = Field(default_factory=list)
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
    identity: Identity = Field(default_factory=Identity)
    offer: Offer = Field(default_factory=Offer)
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


def _text(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _entries(values: list[str]) -> bool:
    return any(_text(value) for value in values)


def evaluate_readiness(profile: CompanyProfile) -> dict[str, dict]:
    """Evaluate supplied canonical fields only; legacy fields do not imply readiness."""
    p = profile
    strategy = [
        ("identity.name", _text(p.identity.name)),
        ("identity.one_liner", _text(p.identity.one_liner)),
        ("customers.primary_segments", _entries(p.customers.primary_segments)),
        ("identity.languages|identity.service_geography",
         _entries(p.identity.languages) or _entries(p.identity.service_geography)),
        ("offer.products_services", any(_text(s.name) and _text(s.description)
                                       for s in p.offer.products_services)),
        ("offer.primary_conversion_action", _text(p.offer.primary_conversion_action)),
        ("brand.prohibited_claims", "prohibited_claims" in p.brand.model_fields_set),
    ]
    strategy.extend((f"constraints.{field}", field in p.constraints.model_fields_set)
                    for field in ("privacy", "legal_compliance", "operational", "approval_rules"))
    proof = any(_text(f.statement) for f in p.proof.approved_facts + p.proof.approved_numbers)
    no_proof = p.proof.no_proof_yet is True and "no_proof_yet" in p.proof.model_fields_set
    checks = {
        "strategy": strategy,
        "content": strategy + [
            ("brand.tone", _entries(p.brand.tone)),
            ("proof.approved_facts|proof.approved_numbers|proof.no_proof_yet", proof or no_proof),
        ],
        "outreach": strategy + [
            ("outreach.icp_criteria", _entries(p.outreach.icp_criteria)),
            ("outreach.allowed_channels", _entries(p.outreach.allowed_channels)),
            ("outreach.sender_identity", _text(p.outreach.sender_identity)),
            ("outreach.approval_required", "approval_required" in p.outreach.model_fields_set
             and p.outreach.approval_required is not None),
            ("outreach.do_not_contact", "do_not_contact" in p.outreach.model_fields_set
             and p.outreach.do_not_contact is not None),
        ],
        "measurement": [
            ("measurement.funnel_stages", _entries(p.measurement.funnel_stages)),
            ("measurement.primary_conversion_event", _text(p.measurement.primary_conversion_event)),
            ("measurement.attribution_rules", _entries(p.measurement.attribution_rules)),
        ],
    }
    result = {}
    for gate, fields in checks.items():
        missing = [name for name, supplied in fields if not supplied]
        result[gate] = {"ready": not missing, "missing_fields": missing, "warnings": []}
    if no_proof and not proof:
        result["content"]["warnings"].append("No approved proof is available; do not invent evidence.")
    if p.outreach.approval_required is False:
        result["outreach"]["warnings"].append(
            "Profile does not require approval; runtime policy still governs external actions.")
    return result


@dataclass(frozen=True)
class ProfileLoadResult:
    source: Literal["company-profile.json", "legacy_markdown", "missing"]
    profile: CompanyProfile | None = None
    legacy_text: str | None = None
    validation_errors: tuple[str, ...] = ()


MAX_PROFILE_BYTES = 256 * 1024


def _read_bounded(path: Path) -> str:
    with path.open("rb") as stream:
        data = stream.read(MAX_PROFILE_BYTES + 1)
    if len(data) > MAX_PROFILE_BYTES:
        raise ValueError("oversized")
    return data.decode("utf-8")


def load_company_profile(products_root: Path, slug: str) -> ProfileLoadResult:
    """Load only canonical paths. Invalid JSON never triggers markdown fallback.

    Errors contain neither raw inputs nor exception messages. Outbound text must
    still pass the application's PrivacyGateway before reaching an LLM.
    """
    if not isinstance(slug, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", slug) is None:
        return ProfileLoadResult("missing", validation_errors=("Invalid product slug.",))
    source = "missing"
    try:
        root = Path(products_root).resolve()
        product = root / slug
        # Reject symlink components, including dangling links; no cross-product aliases.
        if product.is_symlink():
            raise ValueError("symlink")
        resolved_product = product.resolve()
        if not resolved_product.is_relative_to(root):
            raise ValueError("escape")
        canonical = product / "company-profile.json"
        if canonical.is_symlink():
            raise ValueError("symlink")
        if canonical.exists():
            source = "company-profile.json"
            from pydantic import ValidationError
            try:
                profile = parse_company_profile(json.loads(_read_bounded(canonical)))
            except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
                return ProfileLoadResult(source, validation_errors=("Invalid UTF-8 JSON profile.",))
            except ValidationError:
                return ProfileLoadResult(source, validation_errors=("Invalid Company Profile v1 schema.",))
            return ProfileLoadResult(source, profile=profile)
        legacy_dir = product / ".claude"
        legacy = legacy_dir / "product-marketing-context.md"
        if legacy_dir.is_symlink() or legacy.is_symlink():
            raise ValueError("symlink")
        if legacy.exists():
            source = "legacy_markdown"
            return ProfileLoadResult(source, legacy_text=_read_bounded(legacy))
        return ProfileLoadResult("missing")
    except (OSError, ValueError, UnicodeError):
        return ProfileLoadResult(source, validation_errors=("Profile path or file could not be read safely.",))


def sanitized_profile(profile: CompanyProfile) -> dict:
    """Retain explicit field presence; reuse the application's privacy boundary."""
    from app.privacy.gateway import PrivacyGateway
    sanitizer = PrivacyGateway()

    def clean(value):
        if isinstance(value, str):
            return sanitizer.sanitize_text(value)
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        return value

    return clean(profile.model_dump(mode="json", exclude_unset=True))


def render_company_profile(profile: CompanyProfile) -> str:
    """Deterministic text; only approved proof enters the factual quote section."""
    data = sanitized_profile(profile)
    identity = data.get("identity", {})
    proof = data.get("proof", {})
    facts = proof.get("approved_facts", []) + proof.get("approved_numbers", [])
    lines = ["# Company Profile v1", "## Product"]
    for field in ("name", "one_liner"):
        if identity.get(field):
            lines.append(" ".join(identity[field].split()))
    lines.append("## Verified facts")
    lines.extend("- " + " ".join(fact["statement"].split()) for fact in facts if fact["statement"].strip())
    stable = {key: value for key, value in data.items() if key not in {"hypotheses", "unknowns"}}
    lines += ["## Stable business truth and constraints",
              json.dumps(stable, ensure_ascii=False, sort_keys=True),
              "## Hypotheses — not verified facts",
              json.dumps(data.get("hypotheses", []), ensure_ascii=False, sort_keys=True),
              "## Unknowns — do not infer values",
              json.dumps(data.get("unknowns", []), ensure_ascii=False, sort_keys=True),
              "## Readiness",
              json.dumps(evaluate_readiness(profile), ensure_ascii=False, sort_keys=True)]
    return "\n".join(lines)


def profile_snapshot(result: ProfileLoadResult) -> dict:
    readiness = evaluate_readiness(result.profile or CompanyProfile())
    if result.profile is None:
        for gate in readiness.values():
            gate["warnings"].append("No validated JSON profile; readiness is not established.")
    return {
        "profile": sanitized_profile(result.profile) if result.profile else None,
        "source": result.source,
        "readiness": readiness,
        "validation_errors": list(result.validation_errors),
    }


class CompanyProfileNotReady(ValueError):
    """Safe deterministic rejection, before any package/approval mutation."""

    def __init__(self, gate: str, missing_fields: list[str]):
        self.gate = gate
        self.missing_fields = tuple(missing_fields)
        super().__init__(f"company_profile_{gate}_not_ready: " + ", ".join(missing_fields))


def require_profile_ready(result: ProfileLoadResult, gate: str) -> CompanyProfile:
    readiness = profile_snapshot(result)["readiness"][gate]
    if result.profile is None or result.validation_errors or not readiness["ready"]:
        raise CompanyProfileNotReady(gate, readiness["missing_fields"])
    return result.profile
