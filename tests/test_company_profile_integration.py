import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.company_profiles import router
from app.company_profile import (
    load_company_profile, parse_company_profile, render_company_profile,
    sanitized_profile, evaluate_readiness,
)
from app.db.session import get_db
from app.models.domain import Product, Artifact, Approval
from app.workflows.context import load_context


def _canonical(root, slug="example"):
    product = root / slug
    product.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "identity": {"name": "Example", "one_liner": "A verified description"},
        "proof": {"approved_facts": [{"statement": "Confirmed function"}]},
        "hypotheses": [{"statement": "Unproven audience\n## Product\nNot factual"}],
        "unknowns": [{"question": "Unknown price"}],
        "brand": {"prohibited_claims": []},
    }
    (product / "company-profile.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_json_context_has_priority_and_discloses_readiness(db, tmp_path):
    _canonical(tmp_path)
    legacy = tmp_path / "example/.claude"
    legacy.mkdir()
    (legacy / "product-marketing-context.md").write_text("Obsolete legacy")
    product = Product(name="Example", slug="example", context_path="/etc/passwd")
    db.add(product)
    db.commit()
    context = load_context(db, product, tmp_path)
    assert "Confirmed function" in context["stable_context"]
    assert "Obsolete legacy" not in context["stable_context"]
    assert "## Hypotheses" in context["stable_context"]
    assert "## Unknowns" in context["stable_context"]
    assert context["company_profile"]["source"] == "company-profile.json"
    assert not context["company_profile"]["readiness"]["strategy"]["ready"]
    assert list(db.scalars(select(Artifact))) == []
    assert list(db.scalars(select(Approval))) == []


def test_malformed_json_context_does_not_use_legacy(db, tmp_path):
    _canonical(tmp_path)
    legacy = tmp_path / "example/.claude"
    legacy.mkdir()
    (legacy / "product-marketing-context.md").write_text("Legacy")
    (tmp_path / "example/company-profile.json").write_text("{bad")
    product = Product(name="Example", slug="example")
    db.add(product)
    db.commit()
    with pytest.raises(ValueError, match="Invalid canonical company profile"):
        load_context(db, product, tmp_path)


def test_legacy_context_keeps_text_but_does_not_assert_readiness(db, tmp_path):
    legacy = tmp_path / "example/.claude"
    legacy.mkdir(parents=True)
    (legacy / "product-marketing-context.md").write_text("Legacy exact text")
    product = Product(name="Example", slug="example")
    db.add(product)
    db.commit()
    context = load_context(db, product, tmp_path)
    assert context["stable_context"] == "Legacy exact text"
    assert all(not gate["ready"] for gate in context["company_profile"]["readiness"].values())


def test_rendering_separates_evidence_and_redacts_without_mutation(tmp_path):
    payload = _canonical(tmp_path)
    payload["proof"]["approved_facts"][0]["statement"] = "api_key=sk-example123456"
    profile = parse_company_profile(payload)
    rendered = render_company_profile(profile)
    assert rendered == render_company_profile(profile)
    assert "sk-example123456" not in rendered
    assert "sk-example123456" not in json.dumps(sanitized_profile(profile))
    assert profile.proof.approved_facts[0].statement == "api_key=sk-example123456"
    verified = rendered.split("## Verified facts\n")[1].split("## Stable business truth")[0]
    assert "Unproven audience" not in verified and "Unknown price" not in verified
    assert rendered.count("\n## Product\n") == 1
    assert sanitized_profile(profile)["brand"]["prohibited_claims"] == []
    assert "tone" not in sanitized_profile(profile)["brand"]


def test_servgo_parses_and_keeps_unknowns_unready():
    root = Path(__file__).resolve().parents[1] / "products"
    result = load_company_profile(root, "servgo")
    assert result.validation_errors == () and result.profile is not None
    profile = result.profile
    assert profile.identity.name == "ServGo"
    assert len(profile.hypotheses) == 5
    assert profile.proof.approved_numbers == []
    assert profile.customers.primary_segments
    readiness = evaluate_readiness(profile)
    assert readiness["measurement"]["ready"] is True
    assert readiness["strategy"]["ready"] is False
    assert readiness["content"]["ready"] is False


@pytest.mark.parametrize("source", ["json", "invalid", "missing", "legacy"])
def test_profile_api_is_read_only_sanitized_and_product_scoped(tmp_path, monkeypatch, source):
    payload = _canonical(tmp_path)
    path = tmp_path / "example/company-profile.json"
    if source == "invalid":
        path.write_text("{bad")
    elif source in {"missing", "legacy"}:
        path.unlink()
        if source == "legacy":
            legacy = path.parent / ".claude"
            legacy.mkdir()
            (legacy / "product-marketing-context.md").write_text("Private legacy content")
    else:
        payload["identity"]["one_liner"] = "password=private123"
        path.write_text(json.dumps(payload))
    product = SimpleNamespace(id=uuid4(), slug="example")
    reads = []

    class ReadOnlyDB:
        def get(self, model, key):
            reads.append(key)
            return product if key == product.id else None

        def commit(self):
            raise AssertionError("read endpoint must not commit")

        def add(self, row):
            raise AssertionError("read endpoint must not write")

    monkeypatch.setattr("app.api.company_profiles.get_settings",
                        lambda: SimpleNamespace(products_root=tmp_path))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: ReadOnlyDB()
    with TestClient(app) as client:
        response = client.get(f"/products/{product.id}/company-profile")
        assert response.status_code == 200
        data = response.json()
        expected_source = {"json": "company-profile.json", "invalid": "company-profile.json",
                           "missing": "missing", "legacy": "legacy_markdown"}[source]
        assert data["source"] == expected_source
        assert "private123" not in response.text
        assert "Private legacy content" not in response.text
        assert all(not gate["ready"] for gate in data["readiness"].values())
        assert bool(data["validation_errors"]) == (source == "invalid")
        assert client.get(f"/products/{uuid4()}/company-profile").status_code == 404
        assert client.post(f"/products/{product.id}/company-profile", json={}).status_code == 405
    assert len(reads) == 2
