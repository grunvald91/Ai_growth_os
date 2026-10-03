import json

import pytest
from pydantic import ValidationError

from app.company_profile import (
    Hypothesis,
    Unknown,
    VerifiedFact,
    evaluate_readiness,
    load_company_profile,
    parse_company_profile,
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


def test_load_valid_company_profile_json(tmp_path):
    directory = tmp_path / "acme"
    directory.mkdir()
    (directory / "company-profile.json").write_text(json.dumps(_valid_payload()), encoding="utf-8")
    legacy = directory / ".claude"
    legacy.mkdir()
    (legacy / "product-marketing-context.md").write_text("legacy", encoding="utf-8")

    result = load_company_profile(tmp_path, "acme")

    assert result.source == "json"
    assert result.error is None
    assert result.legacy_markdown is None
    assert result.profile is not None
    assert result.profile.identity.legal_name == "Example Co"
    assert result.profile.offer.name == "Widget"


def test_malformed_json_does_not_invent_profile_or_use_legacy(tmp_path):
    directory = tmp_path / "acme"
    directory.mkdir()
    (directory / "company-profile.json").write_text("{not json", encoding="utf-8")
    legacy = directory / ".claude"
    legacy.mkdir()
    (legacy / "product-marketing-context.md").write_text("# legacy", encoding="utf-8")

    result = load_company_profile(tmp_path, "acme")

    assert result.source == "invalid"
    assert result.error == "malformed JSON"
    assert result.profile is None
    assert result.legacy_markdown is None


def test_invalid_schema_does_not_invent_profile_or_use_legacy(tmp_path):
    directory = tmp_path / "acme"
    directory.mkdir()
    (directory / "company-profile.json").write_text('{"schema_version": "9.9"}', encoding="utf-8")
    legacy = directory / ".claude"
    legacy.mkdir()
    (legacy / "product-marketing-context.md").write_text("# legacy", encoding="utf-8")

    result = load_company_profile(tmp_path, "acme")

    assert result.source == "invalid"
    assert result.error == "validation failed"
    assert result.profile is None
    assert result.legacy_markdown is None


@pytest.mark.parametrize(
    "slug",
    ["../secrets", "..", ".", "a/b", "foo bar", "acme/../../etc", "café", "", "/tmp/acme", "acme\\x"],
)
def test_path_allowlisting_rejects_slug(tmp_path, slug):
    result = load_company_profile(tmp_path, slug)

    assert result.source == "rejected"
    assert result.error == "slug is not allowlisted"
    assert result.profile is None
    assert result.legacy_markdown is None


def test_path_allowlisting_rejects_symlink_outside_root(tmp_path):
    root = tmp_path / "products"
    slug_dir = root / "acme"
    slug_dir.mkdir(parents=True)
    outside = tmp_path / "secret.json"
    outside.write_text(json.dumps(_valid_payload()), encoding="utf-8")
    (slug_dir / "company-profile.json").symlink_to(outside)

    result = load_company_profile(root, "acme")

    assert result.source == "rejected"
    assert result.error == "path is outside products root"
    assert result.profile is None
    assert result.legacy_markdown is None


def test_legacy_markdown_fallback_only_when_json_absent(tmp_path):
    legacy = tmp_path / "acme" / ".claude"
    legacy.mkdir(parents=True)
    text = "# Product marketing context\n\nNo structured facts.\n"
    (legacy / "product-marketing-context.md").write_text(text, encoding="utf-8")

    result = load_company_profile(tmp_path, "acme")

    assert result.source == "legacy_markdown"
    assert result.profile is None
    assert result.error is None
    assert result.legacy_markdown == text

    missing = load_company_profile(tmp_path, "other")
    assert missing.source == "missing"
    assert missing.profile is None
    assert missing.legacy_markdown is None


def test_readiness_gates():
    incomplete = parse_company_profile(
        {
            "identity": {"legal_name": "Example Co"},
            "offer": {"name": "Widget", "description": "A widget"},
        }
    )
    gates = evaluate_readiness(incomplete)

    assert gates["strategy"].ready is False
    assert gates["strategy"].missing_fields == ("customers.ideal_customer", "brand.positioning")
    assert gates["strategy"].warnings == ("proof.facts is empty",)
    assert gates["content"].ready is False
    assert gates["content"].missing_fields == ("brand.voice", "brand.positioning")
    assert gates["content"].warnings == ("proof.facts is empty",)
    assert gates["outreach"].ready is False
    assert gates["outreach"].missing_fields == (
        "customers.ideal_customer",
        "outreach.motion",
        "channels.items",
    )
    assert gates["outreach"].warnings == ()
    assert gates["measurement"].ready is False
    assert gates["measurement"].missing_fields == ("measurement.metrics",)
    assert gates["measurement"].warnings == ()

    partial = evaluate_readiness(parse_company_profile(_valid_payload()))
    assert partial["strategy"].ready is True
    assert partial["strategy"].missing_fields == ()
    assert partial["strategy"].warnings == (
        "hypotheses are not verified facts",
        "unknowns remain open",
    )
    assert partial["content"].ready is False
    assert partial["content"].missing_fields == ("brand.voice",)
    assert partial["content"].warnings == ()
    assert partial["outreach"].ready is True
    assert partial["outreach"].warnings == ("unknowns remain open",)
    assert partial["measurement"].ready is True
    assert partial["measurement"].warnings == ("measurement.metrics[0].definition",)

    ready_payload = _valid_payload()
    ready_payload["brand"] = {"positioning": "Reliable", "voice": "Direct"}
    ready_payload["measurement"] = {"metrics": [{"name": "replies", "definition": "Positive replies"}]}
    ready_payload["hypotheses"] = []
    ready_payload["unknowns"] = []
    ready = evaluate_readiness(parse_company_profile(ready_payload))
    assert set(ready) == {"strategy", "content", "outreach", "measurement"}
    assert all(gate.ready for gate in ready.values())
    assert all(gate.missing_fields == () and gate.warnings == () for gate in ready.values())
