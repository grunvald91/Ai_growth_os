"""Team map is built from the real role catalog and pipeline stages, never from a separate invented list."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.agents import AgentRole
from app.api.content_studio_team import LANES, all_placed_agents, catalog, lane_agents
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import AgentRun, Product, Task, TaskStatus
from app.workflows.article_production import STAGES


@pytest.fixture
def studio():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        product = Product(name="Мастерская кофе", slug="coffee-team")
        other = Product(name="Чужая компания", slug="other-team")
        db.add_all([product, other])
        db.commit()
        app.dependency_overrides[get_db] = lambda: (yield db)

        def critic(flag: bool):
            app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, agent_critic_enabled=flag)

        with TestClient(app) as client:
            yield client, db, product, other, critic
        app.dependency_overrides.clear()
    engine.dispose()


def test_every_registered_role_is_on_the_map_or_in_reserve():
    data = catalog()
    assert {role.value for role in AgentRole} <= set(data)
    placed = all_placed_agents()
    assert all(agent in data for agent in placed)       # на схеме нет выдуманных агентов
    assert [a for a in data if a not in placed] == ["video_producer"]


def test_article_lane_follows_real_pipeline_order():
    article = next(lane for lane in LANES if lane["key"] == "article")
    agents = lane_agents(article)
    stage_names = [name for name, _schema, _instruction in STAGES]
    assert agents[:len(stage_names)] == stage_names
    assert {"platform_writer", "youtube_shorts_director", "instagram_reels_director"} <= set(agents)


def test_team_map_page_counts_agents_and_shows_critic_state(studio):
    client, db, product, other, critic = studio
    total = len(catalog())
    critic(False)
    page = client.get(f"/studio/products/{product.id}/team-map").text
    assert f"<b>{total}</b><span>агента" in page.replace("агентов", "агента") or f"<b>{total}</b><span>агентов" in page
    assert "Сейчас выключен" in page and "Включён" not in page.split("Критик: второй ИИ")[0]
    for needle in ("Контент-стратег", "SEO-редактор", "Автор статей", "Маркетинговый директор", "Короткий пост",
                   "Исследование рынка", "Рисованное видео", "Где решаете вы"):
        assert needle in page
    assert page.count("sg-shield") > 20
    critic(True)
    page = client.get(f"/studio/products/{product.id}/team-map").text
    assert "Сейчас включён" in page and "Сейчас выключен" not in page


def test_team_map_highlights_agents_of_the_task_and_ignores_foreign_tasks(studio):
    client, db, product, other, critic = studio
    critic(False)
    task = Task(product_id=product.id, title="CONTENT: тема", assigned_agent="content_factory", status=TaskStatus.RUNNING)
    foreign = Task(product_id=other.id, title="CONTENT: чужая", assigned_agent="content_factory", status=TaskStatus.RUNNING)
    db.add_all([task, foreign])
    db.flush()
    now = datetime.now(timezone.utc)
    db.add_all([AgentRun(task_id=task.id, agent_name="content_strategist", status="completed", started_at=now, finished_at=now,
                         input_references=[], output_references=[]),
                AgentRun(task_id=task.id, agent_name="seo_editor", status="running", started_at=now,
                         input_references=[], output_references=[]),
                AgentRun(task_id=foreign.id, agent_name="article_writer", status="running", started_at=now,
                         input_references=[], output_references=[])])
    db.commit()
    page = client.get(f"/studio/products/{product.id}/team-map?task={task.id}").text
    assert "Ваше задание на схеме" in page
    assert 'class="sg-node done"' in page and 'class="sg-node run"' in page
    foreign_page = client.get(f"/studio/products/{product.id}/team-map?task={foreign.id}").text
    assert "Ваше задание на схеме" not in foreign_page
    assert client.get(f"/studio/products/{product.id}/team-map?task={uuid4()}").status_code == 200


def test_entry_points_lead_to_the_map(studio):
    client, db, product, other, critic = studio
    critic(False)
    map_url = f"/studio/products/{product.id}/team-map"
    home = client.get(f"/studio/products/{product.id}").text
    assert "Показать схему всей команды" in home and map_url in home and "Как устроена команда" in home
    task = Task(product_id=product.id, title="CONTENT: тема", assigned_agent="content_factory", status=TaskStatus.RUNNING)
    db.add(task)
    db.flush()
    db.add(AgentRun(task_id=task.id, agent_name="content_strategist", status="running", started_at=datetime.now(timezone.utc),
                    input_references=[], output_references=[]))
    db.commit()
    page = client.get(f"/studio/products/{product.id}/tasks/{task.id}").text
    assert f"{map_url}?task={task.id}" in page
