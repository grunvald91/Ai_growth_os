"""Manual campaign status is an auditable DB-only operation, not ad delivery."""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.api.growth_campaigns import router as campaign_router
from app.api.growth_live import router as live_router
from app.db.models import Base
from app.db.session import get_db
from app.models.domain import Artifact, Experiment, Product, Task
from app.services.campaign_lifecycle import (
    EVENT_KIND, allowed_actions, campaign_event_history, change_campaign_status,
    CampaignTransitionError,
)


@pytest.fixture()
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        first = Product(name="Studio first", slug="campaign-first")
        second = Product(name="Studio second", slug="campaign-second")
        db.add_all([first, second])
        db.commit()
        db.refresh(first)
        db.refresh(second)
        item = Experiment(product_id=first.id, name="Промо", hypothesis="Проверка гипотезы")
        db.add(item)
        db.commit()
        db.refresh(item)
        app = FastAPI()
        app.include_router(campaign_router)
        app.include_router(live_router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[require_studio_auth] = lambda: None
        app.dependency_overrides[require_studio_csrf] = lambda: None
        with TestClient(app) as client:
            yield client, db, first.id, second.id, item.id
    engine.dispose()


def path(pid, eid):
    return f"/studio/products/{pid}/campaigns/{eid}/status"


def test_full_manual_workflow_and_logged_actions_do_not_create_tasks(ctx):
    client, db, product_id, _, campaign_id = ctx
    stages = [
        ("review", "manual_review"),
        ("approve", "manual_approved"),
        ("start_tracking", "manual_running"),
        ("pause", "manual_paused"),
        ("resume", "manual_running"),
        ("complete", "manual_completed"),
        ("reopen", "manual_paused"),
    ]
    for action, expected in stages:
        response = client.post(path(product_id, campaign_id), data={"action": action},
                               follow_redirects=False)
        assert response.status_code == 303, (action, response.text)
        db.refresh(db.get(Experiment, campaign_id))
        assert db.get(Experiment, campaign_id).status == expected
    events = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.kind == EVENT_KIND,
    )))
    assert len(events) == len(stages)
    assert not list(db.scalars(select(Task))), "manual tracking must not queue any work"
    assert all('"external_action": false' in event.content for event in events)
    history = campaign_event_history(db, product_id=product_id,
                                     campaign_ids=[campaign_id], max_events=10)
    assert len(history[campaign_id]) == len(stages)
    assert history[campaign_id][0]["action"] == "reopen"


def test_owner_scope_and_invalid_transition_are_rejected_without_journal(ctx):
    client, db, product_id, other_id, campaign_id = ctx
    response = client.post(path(other_id, campaign_id), data={"action": "review"},
                           follow_redirects=False)
    assert response.status_code == 404
    assert db.get(Experiment, campaign_id).status == "draft"
    for action in ("approve", "start_tracking", "delete", "", "review; DROP TABLE"):
        result = client.post(path(product_id, campaign_id), data={"action": action},
                             follow_redirects=False)
        assert result.status_code == 409, action
    assert db.get(Experiment, campaign_id).status == "draft"
    assert not list(db.scalars(select(Artifact).where(Artifact.kind == EVENT_KIND)))


def test_independent_legacy_experiment_status_has_no_manual_controls(ctx):
    client, db, product_id, _, campaign_id = ctx
    old = db.get(Experiment, campaign_id)
    old.status = "running"
    db.commit()
    assert not allowed_actions("running")
    page = client.get(f"/studio/products/{product_id}/campaigns")
    assert page.status_code == 200
    assert "Измеряемый эксперимент выполняется" in page.text
    result = client.post(path(product_id, campaign_id), data={"action": "complete"},
                         follow_redirects=False)
    assert result.status_code == 409
    db.refresh(old)
    assert old.status == "running"


def test_manual_cards_display_history_and_are_html_escaped(ctx):
    client, db, product_id, _, campaign_id = ctx
    record = db.get(Experiment, campaign_id)
    record.name = "<script>alert(1)</script>"
    db.commit()
    client.post(path(product_id, campaign_id), data={"action": "review"},
                follow_redirects=False)
    page = client.get(f"/studio/products/{product_id}/campaigns")
    assert page.status_code == 200
    assert "История изменений" in page.text
    assert "На согласовании" in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    assert "<script>alert(1)</script>" not in page.text
    assert "Отметить согласованной" in page.text
    assert 'name="action" value="approve"' in page.text


def test_event_history_keeps_businesses_separate(ctx):
    client, db, product_id, other_id, campaign_id = ctx
    assert client.post(path(product_id, campaign_id), data={"action": "review"},
                       follow_redirects=False).status_code == 303
    assert campaign_event_history(db, product_id=other_id,
                                  campaign_ids=[campaign_id]).get(campaign_id) == []


def test_transition_uses_status_compare_and_swap(ctx):
    _, db, product_id, _, campaign_id = ctx
    db.execute(__import__("sqlalchemy").update(Experiment).where(
        Experiment.id == campaign_id,
    ).values(status="manual_review"))
    db.commit()
    with pytest.raises(CampaignTransitionError):
        change_campaign_status(db, product_id=product_id,
                               campaign_id=campaign_id, action="pause")
    assert len(list(db.scalars(select(Artifact).where(Artifact.kind == EVENT_KIND)))) == 0
