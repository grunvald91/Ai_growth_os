import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.schemas import ExperimentCreate
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Approval, Artifact, Decision, Experiment, Task
from app.policies.experiment_preflight import ExperimentNotReadyError, evaluate_fields, is_placeholder_baseline
from app.services.experiments import ExperimentService

SPEC_PATH = Path(__file__).resolve().parents[1] / "products/servgo/experiments/EXP-SERVGO-001.json"
MEASURED_BASELINE = "2026-09-24..2026-09-30: 1 attributable lead, 0 demo requests (manual CRM count)."
NIL = "00000000-0000-0000-0000-000000000000"


def spec_body(**overrides):
    body = json.loads(SPEC_PATH.read_text(encoding="utf-8"))["experiment"]
    body.update(overrides)
    return body


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        app.dependency_overrides[get_db] = lambda: db
        try:
            with TestClient(app) as test_client:
                test_client.db = db
                yield test_client
        finally:
            app.dependency_overrides.clear()
    engine.dispose()


def create(client, **overrides):
    pid = client.post("/products", json={"name": "ServGo", "slug": "servgo"}).json()["id"]
    response = client.post(f"/products/{pid}/experiments", json=spec_body(**overrides))
    assert response.status_code == 201, response.text
    return pid, response.json()["id"]


def test_complete_measurable_experiment_is_ready(client):
    pid, eid = create(client, baseline_summary=MEASURED_BASELINE)
    response = client.get(f"/products/{pid}/experiments/{eid}/preflight")
    assert response.status_code == 200
    body = response.json()
    assert body == {"experiment_id": eid, "experiment_key": "EXP-SERVGO-001", "status": "draft",
                    "ready": True, "missing_fields": [], "blocking_reasons": []}


def test_unmeasured_baseline_is_not_ready(client):
    pid, eid = create(client)  # spec as committed: baseline UNMEASURED
    body = client.get(f"/products/{pid}/experiments/{eid}/preflight").json()
    assert body["ready"] is False
    assert body["missing_fields"] == []
    assert any("baseline_summary" in reason for reason in body["blocking_reasons"])


def test_missing_attribution_is_not_ready(client):
    pid, eid = create(client, baseline_summary=MEASURED_BASELINE)
    experiment = client.db.get(Experiment, UUID(eid))
    experiment.attribution_method = None  # simulate an incomplete draft (e.g. materialized from a plan)
    client.db.commit()
    body = client.get(f"/products/{pid}/experiments/{eid}/preflight").json()
    assert body["ready"] is False
    assert body["missing_fields"] == ["attribution_method"]
    assert "attribution_method is required" in body["blocking_reasons"]


def test_wrong_product_returns_404(client):
    _, eid = create(client, baseline_summary=MEASURED_BASELINE)
    other = client.post("/products", json={"name": "Other", "slug": "other"}).json()["id"]
    assert client.get(f"/products/{other}/experiments/{eid}/preflight").status_code == 404
    assert client.get(f"/products/{NIL}/experiments/{eid}/preflight").status_code == 404
    assert client.get(f"/products/{other}/experiments/{NIL}/preflight").status_code == 404


def test_preflight_has_no_side_effects(client):
    pid, eid = create(client)
    db = client.db

    def snapshot():
        counts = {m.__name__: db.scalar(select(func.count()).select_from(m))
                  for m in (Experiment, Task, Artifact, Decision, Approval)}
        db.expire_all()
        row = db.get(Experiment, UUID(eid))
        return counts, row.status, row.baseline_summary, row.updated_at

    before = snapshot()
    for _ in range(2):
        assert client.get(f"/products/{pid}/experiments/{eid}/preflight").status_code == 200
    assert snapshot() == before
    assert client.post(f"/products/{pid}/experiments/{eid}/preflight").status_code == 405


def test_incomplete_materialized_draft_reports_all_missing_fields():
    missing, reasons = evaluate_fields({"target_metric": "qualified_leads"})
    assert "target_metric" not in missing
    for name in ("experiment_key", "audience", "action", "attribution_method", "baseline_summary",
                 "observation_window_days", "time_budget_minutes", "success_condition",
                 "stop_condition", "inconclusive_condition", "budget", "currency"):
        assert name in missing
    assert len(reasons) >= len(missing)


@pytest.mark.parametrize("value", [None, "", "   ", "-", "UNKNOWN", "unmeasured", "Not measured yet", "TBD",
                                   "placeholder", "UNMEASURED — record before launch"])
def test_placeholder_baselines_are_rejected(value):
    assert is_placeholder_baseline(value)


def test_measured_baselines_are_accepted():
    assert not is_placeholder_baseline(MEASURED_BASELINE)
    assert not is_placeholder_baseline("Verified 0 attributable leads in the prior 7 days (manual CRM check).")


def test_invalid_values_block_preflight():
    fields = dict(spec_body(baseline_summary=MEASURED_BASELINE), experiment_key="exp-lower",
                  currency="rub", budget="-1", observation_window_days=0)
    missing, reasons = evaluate_fields(fields)
    assert missing == []
    assert len(reasons) == 4


def test_service_blocks_launch_until_preflight_passes(db):
    from app.models import Product
    owner = Product(name="ServGo", slug="servgo")
    db.add(owner)
    db.flush()
    service = ExperimentService(db)
    body = ExperimentCreate.model_validate(spec_body()).model_dump()

    with pytest.raises(ExperimentNotReadyError):
        service.create(product_id=owner.id, **dict(body, status="running"))

    experiment = service.create(product_id=owner.id, **body)
    with pytest.raises(ExperimentNotReadyError):
        service.update(experiment.id, status="running")
    assert experiment.status == "draft"

    service.update(experiment.id, baseline_summary=MEASURED_BASELINE)
    service.update(experiment.id, status="running")
    assert experiment.status == "running"


def test_servgo_spec_is_valid_and_explicitly_not_launch_ready():
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    body = ExperimentCreate.model_validate(spec["experiment"])
    assert body.experiment_key == "EXP-SERVGO-001"
    assert body.status == "draft"
    assert body.budget == 0 and body.currency == "RUB"
    assert spec["launch_ready"] is False
    missing, reasons = evaluate_fields(body.model_dump())
    assert missing == []
    assert reasons and all("baseline_summary" in reason for reason in reasons)
