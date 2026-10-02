import pytest

from app.control.telegram import render_experiment_detail
from app.models.domain import Experiment, Product, Prospect
from app.services.prospects import ProspectService


def _setup(db):
    product = Product(name="ServGo", slug="servgo-enrichment")
    db.add(product)
    db.flush()
    experiment = Experiment(
        product_id=product.id,
        experiment_key="EXP-SERVGO-001",
        name="Outreach",
        hypothesis="Test demand",
        status="running",
    )
    db.add(experiment)
    db.flush()
    prospect = Prospect(
        product_id=product.id,
        experiment_id=experiment.id,
        lead_key="SG001-001",
        business_name="Example SPA",
        public_phone="+70000000000",
        validation_status="pending",
        outreach_status="not_contacted",
        evidence=[],
    )
    db.add(prospect)
    db.commit()
    return experiment, prospect


def _evidence(field, value):
    return {
        "field": field,
        "value": value,
        "source_url": "https://example.com/contact",
        "observed_at": "2026-10-02",
        "source_type": "official_website",
    }


def test_enrichment_requires_evidence_and_is_idempotent(db):
    experiment, prospect = _setup(db)
    row = {
        "lead_id": "SG001-001",
        "validation_status": "validated",
        "website": "https://example.com/",
        "email": "hello@example.com",
        "preferred_channel": "email",
        "source_checked_at": "2026-10-02",
        "evidence": [
            _evidence("identity", "Example SPA | +70000000000"),
            _evidence("website", "https://example.com/"),
            _evidence("email", "hello@example.com"),
        ],
    }

    service = ProspectService(db)
    service.apply_enrichment(experiment_id=experiment.id, rows=[row])
    service.apply_enrichment(experiment_id=experiment.id, rows=[row])
    db.commit()
    db.refresh(prospect)

    assert prospect.validation_status == "validated"
    assert prospect.website == "https://example.com/"
    assert prospect.email == "hello@example.com"
    assert prospect.preferred_channel == "email"
    assert len(prospect.evidence) == 3

    stats = service.stats_for_experiment(experiment.id)
    assert stats == {
        "total": 1,
        "validated": 1,
        "phone": 1,
        "contact_found": 1,
        "needs_review": 0,
        "email": 1,
        "whatsapp": 0,
        "telegram": 0,
        "website": 1,
        "ready_for_outreach": 1,
        "contacted": 0,
    }


def test_enrichment_refuses_unproven_or_conflicting_contact(db):
    experiment, prospect = _setup(db)
    service = ProspectService(db)

    with pytest.raises(ValueError, match="email requires matching evidence"):
        service.apply_enrichment(
            experiment_id=experiment.id,
            rows=[
                {
                    "lead_id": "SG001-001",
                    "email": "hello@example.com",
                    "evidence": [_evidence("identity", "Example SPA")],
                }
            ],
        )

    prospect.email = "manual@example.com"
    db.commit()
    with pytest.raises(ValueError, match="refusing to overwrite existing email"):
        service.apply_enrichment(
            experiment_id=experiment.id,
            rows=[
                {
                    "lead_id": "SG001-001",
                    "email": "different@example.com",
                    "evidence": [_evidence("email", "different@example.com")],
                }
            ],
        )


def test_validated_requires_identity_evidence_and_render_shows_stats(db):
    experiment, _ = _setup(db)
    service = ProspectService(db)
    with pytest.raises(ValueError, match="identity evidence"):
        service.apply_enrichment(
            experiment_id=experiment.id,
            rows=[
                {
                    "lead_id": "SG001-001",
                    "validation_status": "validated",
                    "website": "https://example.com/",
                    "evidence": [_evidence("website", "https://example.com/")],
                }
            ],
        )

    text = render_experiment_detail(
        experiment,
        {
            "total": 30,
            "validated": 30,
            "phone": 30,
            "contact_found": 30,
            "ready_for_outreach": 10,
            "email": 4,
            "whatsapp": 3,
            "telegram": 1,
            "website": 10,
            "contacted": 0,
        },
    )
    assert "Prospects: 30" in text
    assert "найдено 30/30" in text
    assert "письменный outreach 10" in text
    assert "Проверено компаний: 30" in text
    assert "телефон 30" in text
    assert "email 4" in text
    assert "Уже contacted: 0" in text



def test_servgo_enrichment_batch_covers_all_30_contacts():
    import json
    from pathlib import Path

    payload = json.loads(
        Path("products/servgo/experiments/EXP-SERVGO-001/enrichment.json").read_text(
            encoding="utf-8"
        )
    )
    rows = payload["prospects"]
    assert len(rows) == 30
    assert {row["lead_id"] for row in rows} == {
        f"SG001-{index:03d}" for index in range(1, 31)
    }
    assert all(row["validation_status"] == "validated" for row in rows)
    assert all(
        any(item["field"] == "identity" for item in row["evidence"])
        for row in rows
    )
