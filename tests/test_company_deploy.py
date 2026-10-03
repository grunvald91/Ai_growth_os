import json
from types import SimpleNamespace

from sqlalchemy import select

from app import apply_prospect_enrichment as importer
from app.models.domain import Approval, Artifact, Experiment, Product, Prospect


def test_deploy_keeps_enrichment_and_running_experiment_when_profile_blocks_outreach(db, tmp_path, monkeypatch):
    product = Product(name="Example", slug="example")
    db.add(product)
    db.flush()
    experiment = Experiment(product_id=product.id, experiment_key="EXP-EXAMPLE",
        name="Example", hypothesis="Test", status="running")
    db.add(experiment)
    db.flush()
    prospect = Prospect(product_id=product.id, experiment_id=experiment.id,
        lead_key="lead1", business_name="Example Spa", outreach_status="not_contacted",
        tracking_url="https://example.com/demo", evidence=[])
    db.add(prospect)
    db.commit()
    evidence = lambda field, value: {"field": field, "value": value,
        "source_url": "https://example.com", "source_type": "official_website", "observed_at": "2026-10-03"}
    path = tmp_path / "enrichment.json"
    path.write_text(json.dumps({"experiment_key": experiment.experiment_key, "prospects": [{
        "lead_id": "lead1", "email": "spa@example.com", "validation_status": "validated",
        "preferred_channel": "email", "enrichment_status": "direct",
        "evidence": [evidence("identity", "Example Spa"), evidence("email", "spa@example.com")]}]}))
    class Borrow:
        def __enter__(self): return db
        def __exit__(self, *args): pass
    monkeypatch.setattr(importer, "SessionLocal", lambda: Borrow())
    monkeypatch.setattr("app.services.outreach.get_settings", lambda: SimpleNamespace(products_root=tmp_path))
    result = importer.apply_file(experiment_key=experiment.experiment_key, path=path, prepare_outreach=True)
    assert result["outreach_package"] is None
    assert result["outreach_blocked_reason"].startswith("company_profile_outreach_not_ready")
    assert result["stats"]["ready_for_outreach"] == 1
    assert result["stats"]["enrichment_pending"] == 0
    assert prospect.email == "spa@example.com" and prospect.outreach_status == "not_contacted"
    assert experiment.status == "running"
    assert list(db.scalars(select(Approval))) == []
    assert list(db.scalars(select(Artifact))) == []
