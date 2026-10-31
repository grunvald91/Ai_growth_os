"""Campaign creation remains a draft, with no fabricated planning figures.

End-to-end tests use local SQLite and TestClient; no external services, paid
models, email delivery, ad campaigns or production infrastructure.
"""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.api.growth_campaigns import CampaignDraftForm, parse_campaign_draft, router
from app.api.schemas import ExperimentCreate
from app.db.models import Base
from app.db.session import get_db
from app.models.domain import Experiment, Product


@pytest.fixture()
def workspace():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = Product(name="Test business one", slug="test-business-one")
        other = Product(name="Test business two", slug="test-business-two")
        db.add_all((owner, other))
        db.commit()
        db.refresh(owner)
        db.refresh(other)
        app = FastAPI()
        app.include_router(router)

        def session():
            yield db

        app.dependency_overrides[get_db] = session
        app.dependency_overrides[require_studio_auth] = lambda: None
        app.dependency_overrides[require_studio_csrf] = lambda: None
        with TestClient(app) as client:
            yield client, db, owner.id, other.id
    engine.dispose()


def test_short_form_creates_only_draft_with_no_fake_financial_values(workspace):
    client, db, product_id, _ = workspace
    base = f"/studio/products/{product_id}/campaigns"
    html = client.get(base + "/new")
    assert html.status_code == 200
    assert "Дополнительные настройки (необязательно)" in html.text
    assert html.text.count(" required") == 2
    result = client.post(base + "/new", data={
        "name": "Увеличить заявки",
        "hypothesis": "Проверим новую подачу предложения",
        "audience": "Локальные предприниматели",
        "channel": "Telegram",
    }, follow_redirects=False)
    assert result.status_code == 303
    rows = list(db.scalars(select(Experiment).where(Experiment.product_id == product_id)))
    assert len(rows) == 1
    row = rows[0]
    assert row.name == "Увеличить заявки"
    assert row.status == "draft"
    assert row.audience == "Локальные предприниматели"
    assert row.channel == "Telegram"
    for key in ("budget", "currency", "experiment_key", "target_metric",
                "observation_window_days", "action", "success_condition",
                "attribution_method"):
        assert getattr(row, key) is None, key


def test_draft_can_be_edited_and_optional_values_cleared(workspace):
    client, db, product_id, other_product_id = workspace
    item = Experiment(product_id=product_id, name="Проверка", hypothesis="Цель",
                      audience="Старая аудитория", channel="VK",
                      experiment_key="EXP-OLD", budget=100)
    db.add(item)
    db.commit()
    db.refresh(item)
    path = f"/studio/products/{product_id}/campaigns/{item.id}/edit"
    old = client.get(path)
    assert old.status_code == 200
    assert "Старая аудитория" in old.text
    assert client.get(
        f"/studio/products/{other_product_id}/campaigns/{item.id}/edit"
    ).status_code == 404
    res = client.post(path, data={"name": "Новая цель", "hypothesis": "План",
                                   "audience": "", "channel": "", "budget": "",
                                   "experiment_key": ""}, follow_redirects=False)
    assert res.status_code == 303
    db.refresh(item)
    assert item.name == "Новая цель"
    assert item.audience is None and item.channel is None
    assert item.budget is None and item.experiment_key is None
    assert item.status == "draft"
    assert client.post(
        f"/studio/products/{other_product_id}/campaigns/{item.id}/edit",
        data={"name": "Взлом", "hypothesis": "Другая компания"},
        follow_redirects=False,
    ).status_code == 404


@pytest.mark.parametrize("bad", [
    {"name": "", "hypothesis": "Цель"},
    {"name": "Кампания", "hypothesis": ""},
    {"name": "Кампания", "hypothesis": "Цель", "budget": "-1", "currency": "RUB"},
    {"name": "Кампания", "hypothesis": "Цель", "budget": "200"},
    {"name": "Кампания", "hypothesis": "Цель", "experiment_key": "NOT-CODE"},
    {"name": "Кампания", "hypothesis": "Цель", "observation_window_days": "0"},
])
def test_invalid_drafts_are_rejected_without_creation(workspace, bad):
    client, db, product_id, _ = workspace
    response = client.post(f"/studio/products/{product_id}/campaigns/new",
                           data=bad, follow_redirects=False)
    assert response.status_code == 422
    assert not list(db.scalars(select(Experiment)))


def test_non_drafts_cannot_be_edited(workspace):
    client, db, product_id, _ = workspace
    item = Experiment(product_id=product_id, name="Проводится",
                      hypothesis="Цель", status="active")
    db.add(item)
    db.commit()
    db.refresh(item)
    path = f"/studio/products/{product_id}/campaigns/{item.id}/edit"
    assert client.get(path).status_code == 409
    assert client.post(path, data={"name": "Изменено", "hypothesis": "x"}).status_code == 409
    db.refresh(item)
    assert item.name == "Проводится"


def test_internal_short_form_does_not_relax_strict_experiment_api():
    draft = parse_campaign_draft({"name": "Новый", "hypothesis": "Проверим"})
    assert draft == {"name": "Новый", "hypothesis": "Проверим"}
    with pytest.raises(ValidationError):
        ExperimentCreate.model_validate(draft)


def test_campaign_form_escapes_user_supplied_markup(workspace):
    client, db, product_id, _ = workspace
    item = Experiment(product_id=product_id, name="<img src=x onerror=alert(1)>",
                      hypothesis="Проверка & объяснение")
    db.add(item)
    db.commit()
    db.refresh(item)
    result = client.get(f"/studio/products/{product_id}/campaigns/{item.id}/edit")
    assert result.status_code == 200
    assert "&lt;img src=x onerror=alert(1)&gt;" in result.text
    assert '<img src=x onerror=alert(1)>' not in result.text
