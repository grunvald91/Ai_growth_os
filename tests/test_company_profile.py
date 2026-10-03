import pytest
import json
from copy import deepcopy
from pydantic import ValidationError

from app.company_profile import (
    Hypothesis,
    Unknown,
    VerifiedFact,
    parse_company_profile,
    load_company_profile,
    evaluate_readiness,
    MAX_PROFILE_BYTES,
)


def _valid_payload() -> dict:
    return {
        "schema_version": "1.0",
        "identity": {"legal_name": "Example Co"},
        "offer": {"name": "Widget", "description": "A widget"},
        "customers": {"ideal_customer": "Ops teams"},
        "commercial": {"pricing_model": "subscription"},
        "proof": {
            "facts": [
                {
                    "statement": "Used by three teams",
                    "evidence_ref": "case-notes",
                }
            ]
        },
        "brand": {"positioning": "Reliable"},
        "channels": {"items": [{"name": "email"}]},
        "outreach": {"motion": "founder-led"},
        "measurement": {"metrics": [{"name": "replies"}]},
        "constraints": {"items": ["no cold calls"]},
        "hypotheses": [{"statement": "CFOs are the buyer"}],
        "unknowns": [{"question": "What is the win rate?"}],
    }


def test_valid_parsing():
    profile = parse_company_profile(_valid_payload())

    assert profile.schema_version == "1.0"
    assert profile.identity.legal_name == "Example Co"
    assert profile.offer.name == "Widget"
    assert profile.proof.facts[0].fact_type == "verified"
    assert profile.hypotheses[0].record_type == "hypothesis"
    assert profile.unknowns[0].record_type == "unknown"
    assert profile.unknowns[0].question == "What is the win rate?"


def test_unsupported_schema_version():
    payload = _valid_payload()
    payload["schema_version"] = "2.0"

    with pytest.raises(ValidationError):
        parse_company_profile(payload)


def _ready_payload():
    return {
        "schema_version": "1.0",
        "identity": {"name": "Example", "one_liner": "Booking system", "languages": ["ru"]},
        "offer": {"products_services": [{"name": "Booking", "description": "Online booking"}],
                  "primary_conversion_action": "Request demo"},
        "customers": {"primary_segments": ["Service businesses"]},
        "brand": {"tone": ["Plain"], "prohibited_claims": []},
        "proof": {"no_proof_yet": True},
        "constraints": {"privacy": [], "legal_compliance": [], "operational": [], "approval_rules": []},
        "outreach": {"icp_criteria": ["Manual booking"], "allowed_channels": ["email"],
                     "sender_identity": "Founder", "approval_required": True, "do_not_contact": []},
        "measurement": {"funnel_stages": ["Lead", "Customer"],
                        "primary_conversion_event": "Customer", "attribution_rules": ["UTM"]},
    }


def _write_profile(tmp_path, payload):
    product = tmp_path / "example"
    product.mkdir(exist_ok=True)
    (product / "company-profile.json").write_text(json.dumps(payload), encoding="utf-8")
    return product


def test_canonical_profile_load_and_all_gates_ready(tmp_path):
    _write_profile(tmp_path, _ready_payload())
    result = load_company_profile(tmp_path, "example")
    assert result.source == "company-profile.json"
    assert result.validation_errors == ()
    assert result.legacy_text is None
    readiness = evaluate_readiness(result.profile)
    assert list(readiness) == ["strategy", "content", "outreach", "measurement"]
    assert all(g["ready"] and g["missing_fields"] == [] for g in readiness.values())
    assert readiness["content"]["warnings"]


@pytest.mark.parametrize("section,field,gate", [
    ("identity", "name", "strategy"), ("identity", "one_liner", "strategy"),
    ("customers", "primary_segments", "strategy"),
    ("offer", "products_services", "strategy"),
    ("offer", "primary_conversion_action", "strategy"),
    ("brand", "prohibited_claims", "strategy"),
    ("constraints", "privacy", "strategy"), ("constraints", "legal_compliance", "strategy"),
    ("constraints", "operational", "strategy"), ("constraints", "approval_rules", "strategy"),
    ("brand", "tone", "content"), ("outreach", "icp_criteria", "outreach"),
    ("outreach", "allowed_channels", "outreach"), ("outreach", "sender_identity", "outreach"),
    ("outreach", "approval_required", "outreach"), ("outreach", "do_not_contact", "outreach"),
    ("measurement", "funnel_stages", "measurement"),
    ("measurement", "primary_conversion_event", "measurement"),
    ("measurement", "attribution_rules", "measurement"),
])
def test_exact_missing_field(section, field, gate):
    data = _ready_payload()
    del data[section][field]
    gates = evaluate_readiness(parse_company_profile(data))
    assert gates[gate]["missing_fields"] == [f"{section}.{field}"]
    assert not gates[gate]["ready"]
    if gate == "strategy":
        assert gates["content"]["missing_fields"][0] == f"{section}.{field}"
        assert gates["outreach"]["missing_fields"][0] == f"{section}.{field}"


def test_language_or_geography_and_whitespace():
    data = _ready_payload()
    data["identity"]["languages"] = [" "]
    gates = evaluate_readiness(parse_company_profile(data))
    assert gates["strategy"]["missing_fields"] == ["identity.languages|identity.service_geography"]
    data["identity"]["service_geography"] = ["Sochi"]
    assert evaluate_readiness(parse_company_profile(data))["strategy"]["ready"]
    data["measurement"]["attribution_rules"] = [" "]
    assert not evaluate_readiness(parse_company_profile(data))["measurement"]["ready"]


@pytest.mark.parametrize("value", [None, False])
def test_no_proof_requires_explicit_true(value):
    data = _ready_payload()
    data["proof"]["no_proof_yet"] = value
    gates = evaluate_readiness(parse_company_profile(data))
    assert gates["content"]["missing_fields"] == [
        "proof.approved_facts|proof.approved_numbers|proof.no_proof_yet"]
    data["proof"]["approved_facts"] = [{"statement": "Verified feature", "evidence_ref": "manual"}]
    assert evaluate_readiness(parse_company_profile(data))["content"]["ready"]


def test_missing_and_explicit_empty_are_distinct():
    data = _ready_payload()
    assert evaluate_readiness(parse_company_profile(data))["outreach"]["ready"]
    data["outreach"]["approval_required"] = False
    assert evaluate_readiness(parse_company_profile(data))["outreach"]["ready"]
    data["outreach"]["do_not_contact"] = None
    assert not evaluate_readiness(parse_company_profile(data))["outreach"]["ready"]
    del data["proof"]["no_proof_yet"]
    assert not evaluate_readiness(parse_company_profile(data))["content"]["ready"]


def test_hypotheses_and_legacy_facts_cannot_supply_proof():
    data = _ready_payload()
    data["proof"] = {"facts": [{"statement": "Legacy assertion"}]}
    data["hypotheses"] = [{"statement": "Customers love us"}]
    data["unknowns"] = [{"question": "Who buys?"}]
    assert not evaluate_readiness(parse_company_profile(data))["content"]["ready"]


@pytest.mark.parametrize("bad", ["{bad-json", '{"schema_version":"2.0"}', '[]',
                               '{"identity":{"name":42}}'])
def test_invalid_json_never_falls_back_or_leaks_contents(tmp_path, bad):
    product = _write_profile(tmp_path, _ready_payload())
    legacy = product / ".claude"
    legacy.mkdir()
    (legacy / "product-marketing-context.md").write_text("private legacy text")
    (product / "company-profile.json").write_text(bad)
    result = load_company_profile(tmp_path, "example")
    assert result.source == "company-profile.json"
    assert result.profile is None and result.legacy_text is None
    assert result.validation_errors
    assert bad not in str(result.validation_errors)


def test_legacy_fallback_and_json_priority(tmp_path):
    product = tmp_path / "example"
    legacy = product / ".claude"
    legacy.mkdir(parents=True)
    (legacy / "product-marketing-context.md").write_text("Legacy")
    result = load_company_profile(tmp_path, "example")
    assert result.source == "legacy_markdown" and result.legacy_text == "Legacy"
    _write_profile(tmp_path, _ready_payload())
    assert load_company_profile(tmp_path, "example").source == "company-profile.json"
    assert load_company_profile(tmp_path, "absent").source == "missing"


@pytest.mark.parametrize("slug", ["../example", "a/b", "a\\b", "", ".", "..", "тест", "/tmp", "-x"])
def test_invalid_slug(tmp_path, slug):
    result = load_company_profile(tmp_path, slug)
    assert result.validation_errors and result.profile is None


@pytest.mark.parametrize("link_kind", ["product", "json", "legacy_dir", "legacy_file", "dangling"])
def test_symlink_rejection(tmp_path, link_kind):
    root = tmp_path / "products"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "company-profile.json").write_text(json.dumps(_ready_payload()))
    product = root / "example"
    if link_kind == "product":
        product.symlink_to(outside, target_is_directory=True)
    else:
        product.mkdir()
        if link_kind in ("json", "dangling"):
            (product / "company-profile.json").symlink_to(
                outside / ("company-profile.json" if link_kind == "json" else "missing"))
        elif link_kind == "legacy_dir":
            (product / ".claude").symlink_to(outside, target_is_directory=True)
        else:
            (product / ".claude").mkdir()
            (product / ".claude/product-marketing-context.md").symlink_to(outside / "company-profile.json")
    result = load_company_profile(root, "example")
    assert result.validation_errors and result.profile is None and result.legacy_text is None


@pytest.mark.parametrize("contents", [b"\xff", b" " * (MAX_PROFILE_BYTES + 1)])
def test_invalid_encoding_and_bounded_read(tmp_path, contents):
    product = _write_profile(tmp_path, _ready_payload())
    (product / "company-profile.json").write_bytes(contents)
    assert load_company_profile(tmp_path, "example").validation_errors


def test_boolean_fields_do_not_accept_strings():
    data = deepcopy(_ready_payload())
    data["outreach"]["approval_required"] = "false"
    with pytest.raises(ValidationError):
        parse_company_profile(data)


def test_verified_facts_are_separate_from_hypotheses_and_unknowns():
    with pytest.raises(ValidationError):
        VerifiedFact.model_validate(
            {"statement": "maybe true", "fact_type": "hypothesis"}
        )
    with pytest.raises(ValidationError):
        Hypothesis.model_validate(
            {"statement": "checked", "record_type": "verified"}
        )
    with pytest.raises(ValidationError):
        Unknown.model_validate({"statement": "not a question"})

    payload = _valid_payload()
    payload["proof"] = {
        "facts": [{"statement": "untested", "record_type": "hypothesis"}]
    }
    with pytest.raises(ValidationError):
        parse_company_profile(payload)

    payload = _valid_payload()
    payload["hypotheses"] = [{"question": "Who buys?"}]
    with pytest.raises(ValidationError):
        parse_company_profile(payload)

    payload = _valid_payload()
    payload["unknowns"] = [{"statement": "CFOs are the buyer"}]
    with pytest.raises(ValidationError):
        parse_company_profile(payload)
