"""Owner read-only requests must be accurate, quick and company-scoped."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.dispatcher import service
from app.dispatcher.executors import Context
from app.dispatcher.fast_routes import quick_capability
from app.dispatcher.models import DispatcherMessage
from app.models.domain import Artifact, ArtifactStatus, Product, Task, TaskStatus


@pytest.fixture
def owner(db):
    product = Product(name="ServGo", slug="servgo-test")
    other = Product(name="Other Business", slug="other-business")
    db.add_all([product, other])
    db.commit()
    return product, other


def ctx(db, product):
    return Context(db=db, product=product, settings=Settings(_env_file=None), account="owner")


async def ask(db, product, text, *, key="owner", model=None):
    return await service.handle_message(db, ctx(db, product), model, text=text,
                                        client_key=key, mode="text")


def blocks(reply, kind):
    return [item for item in reply["blocks"] if item["type"] == kind]


@pytest.mark.parametrize(("prompt", "expected"), [
    ("Что порекомендуешь сегодня сделать для бизнеса?", "recommend_today"),
    ("Посоветуй, какие шаги сделать для продвижения компании", "recommend_today"),
    ("Покажи последние видео, которые я запускал", "show_recent_videos"),
    ("Где мои готовые ролики?", "show_recent_videos"),
    ("Покажи созданные видео", "show_recent_videos"),
    ("Сделай видео про полезные советы", None),
    ("Запусти видео про бизнес", None),
    ("Увеличь рекламный бюджет", None),
    ("Какие записи у меня сегодня?", None),
])
def test_safe_fast_routes(prompt, expected):
    assert quick_capability(prompt) == expected


@pytest.mark.asyncio
async def test_asking_for_business_advice_returns_actionable_steps_without_model(db, owner):
    product, _ = owner
    db.add(Task(product_id=product.id, title="CONTENT: Работа",
                assigned_agent="content_factory", status=TaskStatus.RUNNING))
    db.add(Artifact(product_id=product.id, kind="channel_text", title="Проверить пост",
                    content='{"body":"draft"}', status=ArtifactStatus.AWAITING_APPROVAL))
    db.commit()

    before = list(db.scalars(select(Task).where(Task.product_id == product.id)))
    answer = (await ask(db, product, "Что порекомендуешь сегодня сделать для бизнеса?"))["messages"][1]
    assert answer["intent"] == "execute"
    assert db.scalar(select(DispatcherMessage).where(DispatcherMessage.role == "assistant")).capability == "recommend_today"
    assert "На сегодня" not in answer["text"] or "сегодня" in answer["text"].lower()
    table = blocks(answer, "table")[0]
    assert any("провер" in cell.lower() for row in table["rows"] for cell in row)
    assert any("в работе" in cell.lower() or "текущ" in cell.lower() for row in table["rows"] for cell in row)
    assert blocks(answer, "links")
    assert len(list(db.scalars(select(Task).where(Task.product_id == product.id)))) == len(before)


@pytest.mark.asyncio
async def test_recent_videos_include_completed_tasks_and_owner_links(db, owner):
    product, other = owner
    recent = Task(product_id=product.id, title="Видео: последние новости",
                  assigned_agent="whiteboard_renderer", status=TaskStatus.COMPLETED,
                  created_at=datetime(2026, 10, 31, 21))
    waiting = Task(product_id=product.id, title="Видео про записи",
                   assigned_agent="whiteboard_renderer", status=TaskStatus.WAITING_APPROVAL,
                   created_at=datetime(2026, 10, 31, 20))
    hidden = Task(product_id=other.id, title="Private other company",
                  assigned_agent="whiteboard_renderer", status=TaskStatus.COMPLETED)
    db.add_all([recent, waiting, hidden])
    db.flush()
    art = Artifact(product_id=product.id, task_id=recent.id, kind="whiteboard_video",
                   title="Собранное видео", content='{}', status=ArtifactStatus.AWAITING_APPROVAL)
    db.add(art)
    db.commit()
    answer = (await ask(db, product, "Покажи мои последние видео, которые я запустил"))["messages"][1]
    assert answer["intent"] == "execute"
    assert db.scalar(select(DispatcherMessage).where(DispatcherMessage.role == "assistant")).capability == "show_recent_videos"
    cards = blocks(answer, "resource_list")[0]["items"]
    assert len(cards) == 2
    assert [item["title"] for item in cards] == ["Видео про записи", "Видео: последние новости"]
    assert cards[-1]["status"] == "Готово к просмотру"
    assert str(recent.id) in cards[-1]["href"]
    assert str(waiting.id) in cards[0]["href"]
    assert all(str(other.id) not in item["href"] for item in cards)
    assert not blocks(answer, "table") and not blocks(answer, "links")
    assert all("Private other company" not in item["title"] for item in cards)


@pytest.mark.asyncio
async def test_recent_videos_limit_to_eight_but_show_newest_nearest_composer(db, owner):
    product, _ = owner
    start = datetime(2026, 10, 30, 9)
    db.add_all([
        Task(product_id=product.id, title=f"Видео {i}",
             assigned_agent="whiteboard_renderer", status=TaskStatus.QUEUED,
             created_at=start + timedelta(minutes=i))
        for i in range(12)
    ])
    db.commit()
    answer = (await ask(db, product, "Покажи последние готовые ролики"))["messages"][1]
    items = blocks(answer, "resource_list")[0]["items"]
    assert [item["title"] for item in items] == [f"Видео {i}" for i in range(4, 12)]
    assert not blocks(answer, "table") and not blocks(answer, "links")


@pytest.mark.asyncio
async def test_recent_videos_are_honest_when_there_are_none(db, owner):
    product, _ = owner
    answer = (await ask(db, product, "Покажи последние готовые ролики"))["messages"][1]
    assert "Не нашёл" in answer["text"]
    assert blocks(answer, "links")
    assert blocks(answer, "table") == []


@pytest.mark.asyncio
async def test_slow_model_stops_after_short_dispatcher_budget(db, owner, monkeypatch):
    product, _ = owner

    class TooSlow:
        async def generate(self, _request, **_kwargs):
            await asyncio.sleep(2)
            raise AssertionError("Must be cancelled before returning")

    monkeypatch.setattr(service, "DECISION_TIMEOUT_SECONDS", 0.03)
    started = time.monotonic()
    answer = (await ask(db, product, "Придумай идею для осенней акции", model=TooSlow()))["messages"][1]
    assert time.monotonic() - started < 0.5
    assert answer["intent"] == "failed"
    assert blocks(answer, "chips")


def test_dispatcher_model_timeout_only_guards_provider_outages():
    assert service.DECISION_TIMEOUT_SECONDS == 45
