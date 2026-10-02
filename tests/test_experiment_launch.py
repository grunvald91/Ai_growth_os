import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.domain import Artifact, Decision, DecisionType, Experiment, Product, Task, TaskStatus
from app.services.experiment_launch import ExperimentLaunchError, ExperimentLaunchService


def _experiment(db, product_id, **overrides):
    values = {
        "product_id": product_id,
        "experiment_key": "EXP-SERVGO-001",
        "name": "Direct outreach",
        "hypothesis": "Personalized outreach may produce qualified interest.",
        "audience": "Small SPA and massage businesses in Sochi/Adler.",
        "channel": "direct_outreach",
        "action": "Validate 30 candidates and prepare personalized first contact.",
        "target_metric": "qualified_leads",
        "secondary_signals": ["replies", "demo_requests"],
        "attribution_method": "UTM + manual source confirmation",
        "baseline_summary": "Attributed marketing leads = 0 before launch.",
        "observation_window_days": 14,
        "time_budget_minutes": 240,
        "success_condition": "KEEP if >=3 qualified leads from 30 valid reached businesses.",
        "stop_condition": "STOP after full cohort + follow-up + 14 days with 0 qualified leads.",
        "inconclusive_condition": "INCONCLUSIVE if fewer than 25 valid reached businesses.",
        "budget": Decimal("0"),
        "currency": "RUB",
        "status": "draft",
    }
    values.update(overrides)
    row = Experiment(**values)
    db.add(row)
    db.commit()
    return row


def test_launch_sets_window_records_decision_and_queues_preparation(db, tmp_path):
    product = Product(name="ServGo", slug="servgo")
    db.add(product)
    db.commit()
    experiment = _experiment(db, product.id)

    cohort_dir = tmp_path / "servgo" / "experiments" / "EXP-SERVGO-001"
    cohort_dir.mkdir(parents=True)
    (cohort_dir / "cohort.json").write_text(
        json.dumps(
            {
                "status": "technical_preflight_passed_human_approval_pending",
                "pending_gates": ["Human approval", "Manual validation"],
                "leads": [
                    {
                        "lead_id": "SG001-001",
                        "business_name": "Example SPA",
                        "area": "Sochi",
                        "category": "SPA",
                        "public_phone": "+70000000000",
                        "address": "Sochi",
                        "source_place_id": "place-1",
                        "source_checked_at": "2026-10-01",
                        "fit_status": "needs_manual_validation",
                        "outreach_status": "not_contacted",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = ExperimentLaunchService(db, tmp_path).launch(
        experiment.id,
        actor="telegram:123",
    )

    db.refresh(experiment)
    assert experiment.status == "running"
    assert experiment.starts_at == result.starts_at
    assert experiment.ends_at == result.ends_at
    assert (experiment.ends_at - experiment.starts_at).days == 14

    decision = db.get(Decision, result.decision_id)
    assert decision.decision_type == DecisionType.TEST
    assert decision.experiment_id == experiment.id
    assert "telegram:123" in decision.rationale
    assert "external outreach/publication still requires separate approval" in decision.rationale

    task = db.get(Task, result.task_id)
    assert task.status == TaskStatus.QUEUED
    assert task.assigned_agent == "research_pmm"
    assert task.priority == 100
    assert task.title.startswith("EXP-SERVGO-001:")

    brief = db.scalar(
        select(Artifact).where(Artifact.task_id == task.id, Artifact.kind == "task_brief")
    )
    payload = json.loads(brief.content)
    assert payload["experiment_id"] == str(experiment.id)
    assert payload["cohort_snapshot"]["lead_count"] == 1
    assert payload["cohort_snapshot"]["leads"][0]["lead_id"] == "SG001-001"
    assert payload["requires_approval"] is False
    assert "No outreach or publication." in payload["hard_constraints"]


def test_launch_rejects_incomplete_contract_without_state_change(db, tmp_path):
    product = Product(name="ServGo", slug="servgo")
    db.add(product)
    db.commit()
    experiment = _experiment(db, product.id, baseline_summary=None)

    with pytest.raises(ExperimentLaunchError, match="baseline_summary"):
        ExperimentLaunchService(db, tmp_path).launch(
            experiment.id,
            actor="telegram:123",
        )

    db.refresh(experiment)
    assert experiment.status == "draft"
    assert experiment.starts_at is None
    assert list(db.scalars(select(Task))) == []
    assert list(db.scalars(select(Decision))) == []


def test_launch_is_not_repeatable(db, tmp_path):
    product = Product(name="ServGo", slug="servgo")
    db.add(product)
    db.commit()
    experiment = _experiment(db, product.id, status="running")

    with pytest.raises(ExperimentLaunchError, match="must be draft"):
        ExperimentLaunchService(db, tmp_path).launch(
            experiment.id,
            actor="telegram:123",
        )
