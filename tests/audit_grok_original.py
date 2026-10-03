import pytest
from pydantic import ValidationError

from audit_original.company_profile import (
    Hypothesis,
    Unknown,
    VerifiedFact,
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
