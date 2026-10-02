import json
from pathlib import Path

from app.models.domain import Experiment, Product, Prospect
from app.services.prospects import ProspectService


def test_servgo_enrichment_covers_all_30_and_passes_service_validation(db):
    path = Path("products/servgo/experiments/EXP-SERVGO-001/enrichment.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["prospects"]

    expected = {f"SG001-{index:03d}" for index in range(1, 31)}
    lead_ids = {row["lead_id"] for row in rows}
    assert len(rows) == 30
    assert lead_ids == expected

    product = Product(name="ServGo", slug="servgo-all30-test")
    db.add(product)
    db.flush()
    experiment = Experiment(
        product_id=product.id,
        experiment_key="EXP-SERVGO-001-TEST",
        name="All 30 enrichment",
        hypothesis="Test data validity",
        status="running",
    )
    db.add(experiment)
    db.flush()

    for index in range(1, 31):
        lead_id = f"SG001-{index:03d}"
        db.add(
            Prospect(
                product_id=product.id,
                experiment_id=experiment.id,
                lead_key=lead_id,
                business_name=f"Business {lead_id}",
                public_phone=f"+7000000{index:04d}",
                validation_status="pending",
                outreach_status="not_contacted",
                tracking_url=f"https://promo.servgo.ru/?utm_content={lead_id}",
                evidence=[],
            )
        )
    db.commit()

    service = ProspectService(db)
    updated = service.apply_enrichment(
        experiment_id=experiment.id,
        rows=rows,
    )
    db.commit()
    stats = service.stats_for_experiment(experiment.id)

    assert len(updated) == 30
    assert stats["total"] == 30
    assert stats["contact_found"] == 30
    assert stats["phone"] == 30
    assert stats["validated"] == 27
    assert stats["needs_review"] == 3
    assert stats["email"] == 7
    assert stats["whatsapp"] == 4
    assert stats["telegram"] == 3
    assert stats["website"] == 19
    assert stats["ready_for_outreach"] == 11
    assert stats["contacted"] == 0
