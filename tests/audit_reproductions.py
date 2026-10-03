"""Read-only audit reproductions: assertions demonstrate current bad behavior."""
import json
import runpy
from pathlib import Path

import pytest
from sqlalchemy import select

from app.company_profile import CompanyProfileNotReady, parse_company_profile
from app.control.decisions import decide_approval
from app.models.domain import Approval, Experiment, Product, Prospect
from app.services.outreach import OutreachPackageService
from audit_original.company_profile import parse_company_profile as original_parse

helpers = runpy.run_path("tests/test_outreach_package.py")
supplied_profile = helpers["supplied_profile"]
setup = helpers["_setup"]
prospect = helpers["_prospect"]


def test_reproduce_old_approval_button_approves_replaced_package(db):
    product, experiment = setup(db)
    person = prospect(product, experiment, "lead1", "email", "old@example.com")
    db.add(person)
    db.commit()
    first = OutreachPackageService(db).prepare(experiment_id=experiment.id)
    old_button_id, old_artifact_id = first.approval.id, first.artifact.id
    person.email = "changed@example.com"
    db.commit()
    second = OutreachPackageService(db).prepare(experiment_id=experiment.id)
    assert second.artifact.id != old_artifact_id
    decided = decide_approval(db, old_button_id, decision="approved", actor="old-button")
    assert decided.status == "approved"
    assert decided.artifact_id == second.artifact.id


def test_reproduce_new_experiment_retargets_other_experiment_approval(db):
    product, first_experiment = setup(db)
    db.add(prospect(product, first_experiment, "lead1", "email", "first@example.com"))
    second_experiment = Experiment(product_id=product.id, experiment_key="EXP-SECOND",
        name="Second", hypothesis="Second test", status="running")
    db.add(second_experiment)
    db.flush()
    db.add(prospect(product, second_experiment, "lead2", "email", "second@example.com"))
    db.commit()
    first = OutreachPackageService(db).prepare(experiment_id=first_experiment.id)
    original_artifact = first.artifact.id
    second = OutreachPackageService(db).prepare(experiment_id=second_experiment.id)
    assert second.approval.id == first.approval.id
    assert second.approval.artifact_id != original_artifact
    assert len(list(db.scalars(select(Approval).where(Approval.status == "pending")))) == 1


def test_reproduce_grok_schema_accepts_unversioned_and_empty_fact():
    payload = {"identity": {"legal_name": "Example"},
               "offer": {"name": "Example", "description": "Example"},
               "proof": {"facts": [{"statement": "  "}]}}
    old = original_parse(payload)
    assert old.schema_version == "1.0"
    assert old.proof.facts[0].statement == "  "
    current = parse_company_profile(payload)
    assert current.schema_version == "1.0"
    assert current.proof.facts[0].statement == "  "


def test_reproduce_deploy_enrichment_aborts_for_unready_profile(db, supplied_profile, tmp_path, monkeypatch):
    from app import apply_prospect_enrichment as importer
    product, experiment = setup(db)
    person = prospect(product, experiment, "lead1", "email", "lead@example.com")
    person.enrichment_status = "direct"
    db.add(person)
    db.commit()
    profile = json.loads(supplied_profile.read_text())
    profile["outreach"].pop("sender_identity")
    supplied_profile.write_text(json.dumps(profile))
    path = tmp_path / "enrichment.json"
    path.write_text(json.dumps({"experiment_key": experiment.experiment_key,
                              "prospects": [{"lead_id": "lead1", "evidence": [{
                                  "field": "identity", "value": "Business lead1",
                                  "source_url": "https://example.com", "observed_at": "2026-10-03",
                                  "source_type": "official_website"}]}]}))
    class Borrow:
        def __enter__(self): return db
        def __exit__(self, *args): pass
    monkeypatch.setattr(importer, "SessionLocal", lambda: Borrow())
    # Calls the unchanged production importer's real prepare-outreach branch.
    with pytest.raises(CompanyProfileNotReady):
        importer.apply_file(experiment_key=experiment.experiment_key, path=path, prepare_outreach=True)
