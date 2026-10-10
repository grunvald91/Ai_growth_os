"""Regression checks for Telegram channel ownership, multichannel scheduling and safe delivery."""
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.models import Base
from app.models.domain import Artifact, ArtifactStatus, Product, Task, TaskStatus
from app.social.models import TelegramChannel, TelegramPost
from app.social import telegram


@pytest.fixture
def state(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    product = Product(name="Beauty", slug="beauty")
    other = Product(name="Other", slug="other")
    db.add_all([product, other])
    db.commit()
    settings = Settings(_env_file=None, products_root=tmp_path,
        comment_platform_proxy_url="http://foreign-proxy:8891",
        social_telegram_bot_token="123456:TEST_token")
    yield db, engine, product, other, settings
    db.close()
    engine.dispose()


def signed_login(settings, telegram_id=42):
    payload = {"id": str(telegram_id), "auth_date": str(int(datetime.now(timezone.utc).timestamp())),
               "first_name": "Owner"}
    check = "\n".join(f"{k}={v}" for k, v in sorted(payload.items()))
    key = hashlib.sha256(settings.social_telegram_bot_token.encode()).digest()
    payload["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return payload


@pytest.mark.asyncio
async def test_connect_requires_signed_telegram_admin(state, monkeypatch):
    db, _, product, other, settings = state
    async def fake_api(settings, method, payload=None, **kwargs):
        if method == "getMe":
            return {"id": 123, "username": "ServGoPublishBot"}
        if method == "getChat":
            return {"id": -100111222333, "type": "channel", "title": "Beauty", "username": "beautynews"}
        if method == "getChatMember":
            return ({"status": "administrator", "can_post_messages": True}
                    if payload["user_id"] == 123 else {"status": "creator"})
        raise AssertionError(method)
    monkeypatch.setattr(telegram, "api", fake_api)
    channel = await telegram.start_connect(db, settings, product.id, "@beautynews")
    assert channel.status == "pending"
    with pytest.raises(ValueError, match="Подпись"):
        await telegram.confirm_connect(db, settings, product.id, channel.id,
                                      {**signed_login(settings), "first_name": "Fake"})
    with pytest.raises(LookupError):
        await telegram.confirm_connect(db, settings, other.id, channel.id, signed_login(settings))
    confirmed = await telegram.confirm_connect(db, settings, product.id, channel.id, signed_login(settings))
    assert confirmed.status == "active"
    with pytest.raises(ValueError, match="уже"):
        await telegram.start_connect(db, settings, other.id, "@beautynews")


def test_telegram_widget_signature_rejects_stale_or_forged(state):
    _, _, _, _, settings = state
    assert telegram.verify_telegram_login(settings.social_telegram_bot_token, signed_login(settings)) == 42
    forged = signed_login(settings)
    forged["id"] = "1"
    with pytest.raises(ValueError):
        telegram.verify_telegram_login(settings.social_telegram_bot_token, forged)
    stale = signed_login(settings)
    stale["auth_date"] = str(int(stale["auth_date"]) - 1000)
    check = "\n".join(f"{k}={v}" for k, v in sorted(stale.items()) if k != "hash")
    secret = hashlib.sha256(settings.social_telegram_bot_token.encode()).digest()
    stale["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(ValueError, match="устарел"):
        telegram.verify_telegram_login(settings.social_telegram_bot_token, stale)


def channels(state):
    db, _, product, _, _ = state
    rows = [TelegramChannel(product_id=product.id, chat_id=str(-1000000000 - i),
        title="Channel " + str(i), status="active", auto_plan=True) for i in range(2)]
    db.add_all(rows)
    db.commit()
    return rows


def test_schedule_creates_independent_delivery_for_each_destination(state):
    db, _, product, other, _ = state
    a, b = channels(state)
    due = datetime.now(timezone.utc) + timedelta(minutes=5)
    posts = telegram.create_posts(db, product.id, [a.id, b.id], "Hello", [], due)
    assert len(posts) == 2
    assert {p.channel_id for p in db.scalars(select(TelegramPost))} == {a.id, b.id}
    with pytest.raises(ValueError, match="каналов"):
        telegram.create_posts(db, product.id, [a.id, a.id], "Hello", [], due)
    with pytest.raises(ValueError, match="каналов"):
        telegram.create_posts(db, other.id, [a.id], "Hello", [], due)


def test_approved_plan_autofills_two_channels_once(state):
    db, _, product, _, settings = state
    a, b = channels(state)
    task = Task(product_id=product.id, title="CONTENT", status=TaskStatus.COMPLETED,
                assigned_agent="content_factory")
    db.add(task)
    db.flush()
    db.add(Artifact(product_id=product.id, task_id=task.id, kind="channel_text", title="Telegram post",
        content=json.dumps({"channel": "telegram", "title": "Useful", "body": "Post"})))
    plan = {"approval_state": "active", "slots": [
        {"platform": "telegram", "task_id": str(task.id), "slot_id": "1",
         "publish_at": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()}]}
    db.add(Artifact(product_id=product.id, kind="editorial_plan", title="Plan",
                    content=json.dumps(plan), status=ArtifactStatus.APPROVED))
    db.commit()
    assert telegram.auto_plan_queue(db, settings) == 2
    assert telegram.auto_plan_queue(db, settings) == 0
    assert len(list(db.scalars(select(TelegramPost)))) == 2
    b.auto_plan = False
    db.commit()


@pytest.mark.asyncio
async def test_publishes_once_without_retry_after_timeout(state, monkeypatch):
    db, engine, product, _, settings = state
    (channel, _) = channels(state)
    ids = telegram.create_posts(db, product.id, [channel.id], "Public test", [],
        datetime.now(timezone.utc) + timedelta(minutes=5))
    record = db.get(TelegramPost, ids[0])
    record.scheduled_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    db.commit()
    def factory():
        return Session(engine, expire_on_commit=False)
    calls = []
    async def fail(settings, method, payload=None, **kwargs):
        calls.append(method)
        raise TimeoutError("Network timeout. Delivery may have succeeded.")
    monkeypatch.setattr(telegram, "api", fail)
    assert await telegram.deliver_once(factory, settings) == 1
    db.expire_all()
    assert db.get(TelegramPost, ids[0]).status == "delivery_unknown"
    assert await telegram.deliver_once(factory, settings) == 0
    assert calls == ["sendMessage"]


@pytest.mark.asyncio
async def test_success_records_message_id(state, monkeypatch):
    db, engine, product, _, settings = state
    (channel, _) = channels(state)
    ids = telegram.create_posts(db, product.id, [channel.id], "Hello", [],
        datetime.now(timezone.utc) + timedelta(minutes=1))
    record = db.get(TelegramPost, ids[0])
    record.scheduled_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    async def ok(settings, method, payload=None, **kwargs):
        return {"message_id": 7001}
    monkeypatch.setattr(telegram, "api", ok)
    assert await telegram.deliver_once(lambda: Session(engine, expire_on_commit=False), settings) == 1
    db.expire_all()
    assert db.get(TelegramPost, ids[0]).external_message_ids == [7001]
    assert db.get(TelegramPost, ids[0]).status == "sent"
