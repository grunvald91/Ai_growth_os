"""Owner Telegram UI tests for verified multi-channel posting and plan linking."""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.content.telegram import TelegramContent
from app.core.config import Settings
from app.models.domain import Artifact, Product, Publication, Task, TaskStatus


class StubApi:
    def __init__(self):
        self.calls = []

    async def call(self, name, payload=None):
        self.calls.append(name)
        if name == "getMe":
            return {"id": 12}
        if name == "getChat":
            return {"id": -1001234567890, "type": "channel",
                    "title": "Наш канал", "username": "servgo_business"}
        if name == "getChatMember":
            return {"status": "administrator", "can_post_messages": True}
        raise AssertionError(name)


class FakeBot:
    def __init__(self):
        self.api = StubApi()
        self.messages = []

    async def _send(self, chat, text, reply_markup=None):
        self.messages.append((text, reply_markup))


def sessions(db):
    @contextmanager
    def factory():
        yield db
    return factory


@pytest.mark.asyncio
async def test_owner_connects_channel_without_token_and_schedules_approved_post(db, tmp_path):
    product = Product(name="Гончарная школа", slug="owner-post-tg-ui")
    db.add(product)
    db.flush()
    post = Artifact(product_id=product.id, kind="channel_text",
                    title="Наши мастер-классы", status="approved",
                    content=json.dumps({
                        "channel":"telegram", "origin":"quick_post",
                        "title":"Наши мастер-классы",
                        "body":"Подберите занятие по интересам и уточните уровень подготовки.",
                    },ensure_ascii=False))
    db.add(post)
    db.commit()
    bot = FakeBot()
    ui = TelegramContent(bot, sessions(db),
                         lambda: Settings(products_root=tmp_path,
                                          content_telegram_autopost_enabled=False))
    await ui.callback(11, f"content:channels:{product.id}")
    assert "Подключить Telegram" in str(bot.messages[-1][1])
    await ui.callback(11, f"content:connecttg:{product.id}")
    assert ui.channel_connect_product == product.id
    await ui.message({"chat":{"id":11}, "text":"@servgo_business"})
    assert ui.channel_connect_product is None
    assert bot.api.calls == ["getMe", "getChat", "getChatMember"]

    await ui.callback(11, f"content:asset:{post.id}")
    assert "Запланировать в Telegram" in str(bot.messages[-1][1])
    await ui.callback(11, f"content:schedulechannels:{post.id}")
    channels = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product.id, Artifact.kind == "telegram_channel_connection",
    )))
    await ui.callback(11, f"content:schedtg:{channels[0].id}")
    assert ui.schedule_target == (post.id, channels[0].id)
    scheduled_at = (datetime.now(ZoneInfo("Europe/Moscow")) +
                    timedelta(days=2)).strftime("%d.%m.%Y %H:%M")
    await ui.message({"chat":{"id":11}, "text":scheduled_at})
    assert ui.schedule_target is None
    pub = db.scalar(select(Publication).where(Publication.product_id == product.id))
    assert pub is not None and pub.status == "scheduled"
    assert "Автоотправка пока выключена" in bot.messages[-1][0]


@pytest.mark.asyncio
async def test_owner_picks_existing_editorial_slot_instead_of_reentering_date(db, tmp_path):
    product = Product(name="Арт-школа", slug="owner-calendar-tg")
    db.add(product)
    db.flush()
    task = Task(product_id=product.id, title="campaign",
                assigned_agent="content_factory", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    post = Artifact(product_id=product.id, task_id=task.id,
                    kind="channel_text", title="Анонс занятия", status="approved",
                    content=json.dumps({
                        "channel":"telegram", "origin":"quick_post", "title":"Анонс занятия",
                        "body":"Занятия по лепке помогают попробовать новую технику.",
                    },ensure_ascii=False))
    db.add(post)
    db.flush()
    when = datetime.now(timezone.utc) + timedelta(days=4)
    plan = Artifact(product_id=product.id, kind="editorial_plan",
                    title="Редакторский план", status="approved",
                    content=json.dumps({"approval_state":"active","slots":[{
                        "slot_id":"4", "task_id":str(task.id), "platform":"telegram",
                        "topic":"Анонс занятия", "publish_at":when.isoformat(),
                        "state":"ready_for_review",
                    }]}))
    conn = Artifact(product_id=product.id,kind="telegram_channel_connection",
                    title="Канал", status="approved",
                    content=json.dumps({"chat_id":"-1001234567890","title":"Канал"}))
    db.add_all([plan,conn])
    db.commit()
    bot = FakeBot()
    ui = TelegramContent(bot, sessions(db), lambda: Settings(products_root=tmp_path))
    await ui.callback(11, f"content:planpost:{post.id}")
    assert "🕒" in str(bot.messages[-1][1])
    await ui.callback(11, f"content:planslot:{plan.id}:4")
    assert ui.calendar_publish_slot == (plan.id, "4")
    await ui.callback(11, f"content:schedtg:{conn.id}")
    pub = db.scalar(select(Publication).where(Publication.product_id == product.id))
    assert pub is not None and pub.status == "scheduled"
    db.refresh(plan)
    assert json.loads(plan.content)["slots"][0]["telegram_publication_id"] == str(pub.id)
    assert ui.calendar_publish_slot is None
