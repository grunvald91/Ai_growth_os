"""Regression checks for the independently reproduced Growth V4 audit faults."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.growth_campaigns import campaign_metrics
from app.api.growth_live import campaigns_page
from app.models.domain import (
    AgentRun, Artifact, Decision, Experiment, Metric, Product, Task, TaskStatus,
)
from app.services.campaign_lifecycle import (
    CampaignLifecycleError, closing_campaign_for_material, command,
    recover_orphaned_research, task_halted,
)
from app.services.campaign_performance import comparison_for


def _company(db, name):
    product = Product(name=name, slug=name.lower().replace(" ", "-"))
    db.add(product)
    db.flush()
    return product


def _campaign(db, product, key, status="running", metric="leads"):
    campaign = Experiment(product_id=product.id, experiment_key=key,
                          name=key, hypothesis="Check", status=status,
                          target_metric=metric)
    db.add(campaign)
    db.flush()
    return campaign


def test_legacy_cmo_decision_followup_is_cancelled_and_tenant_isolated(db):
    owner = _company(db, "Audit CMO")
    other = _company(db, "Audit Other")
    campaign = _campaign(db, owner, "EXP-AUDIT-CMO")
    foreign = _campaign(db, other, "EXP-AUDIT-FOREIGN")
    decision = Decision(product_id=owner.id, experiment_id=campaign.id,
                        decision_type="TEST", rationale="Continue")
    unrelated_decision = Decision(product_id=other.id, experiment_id=foreign.id,
                                  decision_type="TEST", rationale="Independent")
    db.add_all([decision, unrelated_decision])
    db.flush()
    root = Task(product_id=owner.id, title="Daily organic growth review", status=TaskStatus.COMPLETED)
    db.add(root)
    db.flush()
    followup = Task(product_id=owner.id, title="Investigate conversion after review",
                    parent_id=root.id, status=TaskStatus.QUEUED)
    independent = Task(product_id=other.id, title="Other company CMO work",
                       status=TaskStatus.QUEUED)
    db.add_all([followup, independent])
    db.flush()
    data = {"decision_id": str(decision.id), "task_type": "research"}
    db.add(Artifact(product_id=owner.id, task_id=followup.id,
                    kind="task_brief", title=followup.title, content=json.dumps(data)))
    result_material = Artifact(product_id=owner.id, task_id=followup.id,
                               kind="channel_text", title="CMO campaign material",
                               content="{}")
    db.add(result_material)
    db.commit()
    assert not closing_campaign_for_material(db, owner.id, result_material.id)

    previous_autoflush = db.autoflush
    db.autoflush = False  # Match the production SessionLocal configuration.
    try:
        status, remaining = command(db, product_id=owner.id, experiment_id=campaign.id,
                                    action="stop", actor="audit")
    finally:
        db.autoflush = previous_autoflush
    assert status == "stopped" and remaining.clear
    db.refresh(followup)
    assert followup.status == TaskStatus.CANCELLED
    assert task_halted(db, followup, data)
    assert closing_campaign_for_material(db, owner.id, result_material.id)
    db.refresh(independent)
    assert independent.status == TaskStatus.QUEUED
    db.refresh(foreign)
    assert foreign.status == "running"


def test_orphan_recovery_is_manual_only_and_never_requeues(db):
    owner = _company(db, "Audit Orphans")
    row = _campaign(db, owner, "EXP-AUDIT-ORPHAN")
    parent = Task(product_id=owner.id, title="EXP-AUDIT-ORPHAN: prepare experiment",
                  assigned_agent="research_pmm", status=TaskStatus.RUNNING)
    db.add(parent)
    db.flush()
    child = Task(product_id=owner.id, parent_id=parent.id, title="Internal research",
                 assigned_agent="segment_researcher", status=TaskStatus.RUNNING)
    db.add(child)
    db.flush()
    db.add(Artifact(product_id=owner.id, task_id=parent.id, kind="task_brief",
                    title=parent.title, content=json.dumps({"experiment_id": str(row.id)})))
    run = AgentRun(task_id=child.id, agent_name="segment_researcher", status="running")
    db.add(run)
    db.commit()
    assert command(db, product_id=owner.id, experiment_id=row.id,
                   action="stop", actor="operator")[0] == "stopping"
    assert recover_orphaned_research(
        db, product_id=owner.id, experiment_id=row.id, actor="operator",
    ) == (2, 1)
    db.refresh(parent)
    assert parent.status == TaskStatus.RUNNING
    with pytest.raises(CampaignLifecycleError):
        recover_orphaned_research(db, product_id=owner.id, experiment_id=row.id,
                                  actor="operator", apply=True)
    assert recover_orphaned_research(
        db, product_id=owner.id, experiment_id=row.id, actor="operator",
        apply=True, confirmed_workers_stopped=True,
    ) == (2, 1)
    db.refresh(parent)
    db.refresh(child)
    db.refresh(run)
    db.refresh(row)
    assert parent.status == child.status == TaskStatus.CANCELLED
    assert run.status == "failed"
    assert row.status == "stopped"


def test_baseline_survives_more_than_200_new_measurements(db):
    owner = _company(db, "Audit Metrics")
    row = _campaign(db, owner, "EXP-AUDIT-METRIC")
    base = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    entries = [Metric(product_id=owner.id, experiment_id=row.id,
                      name="leads", value=Decimal("3"), unit="шт.",
                      source="manual", observed_at=base,
                      dimensions={"stage": "baseline", "recorded_at": base.isoformat()})]
    for i in range(201):
        entered = base + timedelta(minutes=i + 1)
        entries.append(Metric(product_id=owner.id, experiment_id=row.id,
                              name="leads", value=Decimal(i + 5), unit="шт.",
                              source="manual", observed_at=base + timedelta(days=1),
                              dimensions={"stage": "result", "recorded_at": entered.isoformat()}))
    db.add_all(entries)
    db.commit()
    history = campaign_metrics(db, owner.id, row.id)
    assert len(history) <= 202
    comparison = comparison_for(row, history)
    assert comparison.comparable
    assert comparison.baseline.value == 3
    assert comparison.result.value == 205
    assert comparison.absolute_change == 202


def test_101_campaigns_have_accessible_third_page(db, monkeypatch):
    owner = _company(db, "Audit Pagination")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(101):
        row = Experiment(product_id=owner.id, experiment_key=f"EXP-AUDIT-PAGE-{i:03d}",
                         name=f"Campaign {i}", hypothesis="Pagination", status="draft",
                         created_at=base + timedelta(minutes=i))
        db.add(row)
    db.commit()
    monkeypatch.setattr("app.api.growth_live._owner_product", lambda db, id: owner)
    monkeypatch.setattr("app.api.growth_live.studio_page_shell",
                        lambda product, **kwargs: kwargs["body"])
    first = campaigns_page(owner.id, page=1, db=db)
    third = campaigns_page(owner.id, page=3, db=db)
    assert "Campaign 100" in first
    assert "page=2" in first
    assert "Campaign 0" in third
    assert "Campaign 100" not in third
    assert "page=2" in third


def test_operator_recovery_cli_is_shipped_in_app_package():
    """Production deploy archives app/, but not the repository scripts/ tree."""
    from app.ops.recover_campaign_orphans import main
    assert callable(main)
