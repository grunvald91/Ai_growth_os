import json
from pathlib import Path


def test_servgo_enrichment_covers_full_experiment_cohort():
    path = Path("products/servgo/experiments/EXP-SERVGO-001/enrichment.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["prospects"]

    assert len(rows) == 30
    assert {row["lead_id"] for row in rows} == {
        f"SG001-{index:03d}" for index in range(1, 31)
    }
    assert all(row.get("evidence") for row in rows)
    assert all(
        any(item.get("field") == "identity" for item in row["evidence"])
        for row in rows
    )
    assert sum(row["validation_status"] == "validated" for row in rows) == 28
    assert sum(row["validation_status"] == "needs_review" for row in rows) == 2
    assert payload["status"] == "verified_30_of_30"
