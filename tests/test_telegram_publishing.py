"""Offline guarantees for Telegram connection and at-most-once scheduled posting."""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.content.telegram_publishing import (
    verify_channel, save_verified_channel, connected_channels, schedule_post,
    cancel_scheduled_post, run_due_telegram_post_once, safe_channel_locator,
)
from app.core.config import Settings
from app.models.domain import Artifact, Product, Publication


class FakeTelegram:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def call(self, method, payload=None):
        self.calls.append((method, payload or {}))
        if method == "getMe":
            return {"id": 31}
        if method == "getChat":
            return {"id": -1001234567890, "type": "channel",
                    "title": "Канал мастерской", "username": "craft_studio"}
        if method == "getChatMember":
            return {"status": "administrator", "can_post_messages": True}
        if method == "sendMessage":
            if self.fail:
                raise TimeoutError("network timeout after send")
            return {"message_id": 876, "chat": {"id": -1001234567890}}
        raise AssertionError("Unexpected Bot API call")


def sessions(db):
    @contextmanager
    def factory():
        yield db
    return factory


def company_and_post(db):
    product = Product(name="Творческая мастерская", slug="tg-publisher")
    db.add(product)
    db.flush()
    post = Artifact(
        product_id=product.id, kind="channel_text", title="Как выбрать бумагу",
        status="approved",
        content=json.dumps({
            "origin":"quick_post", "channel":"telegram",
            "title":"Как выбрать бумагу",
            "body":"Уточните плотность и поверхность бумаги. Проверьте упаковку и хранение.",
        }, ensure_ascii=False)
    )
    db.add(post)
    db.commit()
    return product, post


@pytest.mark.asyncio
async def test_verified_channel_connection_uses_official_bot_admin_permissions(db):
    product, _ = company_and_post(db)
    fake = FakeTelegram()
    data = await verify_channel(fake, "@craft_studio")
    assert data["chat_id"] == "-1001234567890"
    assert [name for name, _ in fake.calls] == ["getMe", "getChat", "getChatMember"]
    connection = save_verified_channel(db, product.id, data)
    assert len(connected_channels(db, product.id)) == 1
    same = save_verified_channel(db, product.id, data)
    assert same.id == connection.id
    assert len(connected_channels(db, product.id)) == 1
    with pytest.raises(ValueError):
        safe_channel_locator("https://evil.example")
    with pytest.raises(ValueError):
        safe_channel_locator("123")


@pytest.mark.asyncio
async def test_owner_scheduled_post_sent_once_and_acknowledged(db):
    product, post = company_and_post(db)
    verified = await verify_channel(FakeTelegram(), "@craft_studio")
    conn = save_verified_channel(db, product.id, verified)
    now = datetime.now(timezone.utc)
    pub = schedule_post(db, post.id, conn.id, now + timedelta(minutes=4), now=now)
    assert pub.status == "scheduled"
    with pytest.raises(ValueError, match="уже запланирован"):
        schedule_post(db, post.id, conn.id, now + timedelta(minutes=5), now=now)
    fake = FakeTelegram()
    enabled = Settings(
        telegram_bot_token="unit-test-only",
        content_telegram_autopost_enabled=True,
        _env_file=None,
    )
    assert await run_due_telegram_post_once(
        sessions(db), enabled, api=fake, now=now+timedelta(minutes=5),
    ) == 1
    db.refresh(pub)
    assert pub.status == "published"
    assert pub.external_reference == "telegram:-1001234567890:876"
    assert pub.published_at is not None
    assert len([m for m, _ in fake.calls if m == "sendMessage"]) == 1
    assert await run_due_telegram_post_once(
        sessions(db), enabled, api=fake, now=now+timedelta(minutes=7),
    ) == 0
    assert len([m for m, _ in fake.calls if m == "sendMessage"]) == 1


@pytest.mark.asyncio
async def test_ambiguous_send_failure_never_autoretries(db):
    product, post = company_and_post(db)
    conn = save_verified_channel(db, await verify_channel(FakeTelegram(), "@craft_studio"))
    now = datetime.now(timezone.utc)
    pub = schedule_post(db, post.id, conn.id, now + timedelta(minutes=2), now=now)
    fake = FakeTelegram(fail=True)
    enabled = Settings(telegram_bot_token="unit-test-only",
                       content_telegram_autopost_enabled=True, _env_file=None)
    assert await run_due_telegram_post_once(
        sessions(db), enabled, api=fake, now=now+timedelta(minutes=3),
    ) == 1
    db.refresh(pub)
    assert pub.status == "delivery_unknown"
    assert await run_due_telegram_post_once(
        sessions(db), enabled, api=fake, now=now+timedelta(minutes=4),
    ) == 0
    assert len([name for name, _ in fake.calls if name == "sendMessage"]) == 1


@pytest.mark.asyncio
async def test_disabled_flag_and_cancelled_publication_do_not_send(db):
    product, post = company_and_post(db)
    conn = save_verified_channel(db, await verify_channel(FakeTelegram(), "@craft_studio"))
    now = datetime.now(timezone.utc)
    pub = schedule_post(db, post.id, conn.id, now + timedelta(minutes=2), now=now)
    fake = FakeTelegram()
    disabled = Settings(telegram_bot_token="unit-test-only",
                        content_telegram_autopost_enabled=False, _env_file=None)
    assert await run_due_telegram_post_once(
        sessions(db), disabled, api=fake, now=now+timedelta(minutes=3),
    ) == 0
    cancel_scheduled_post(db, pub.id)
    enabled = Settings(telegram_bot_token="unit-test-only",
                       content_telegram_autopost_enabled=True, _env_file=None)
    assert await run_due_telegram_post_once(
        sessions(db), enabled, api=fake, now=now+timedelta(minutes=3),
    ) == 0
    assert len([name for name, _ in fake.calls if name == "sendMessage"]) == 0


@pytest.mark.asyncio
async def test_changed_source_is_rejected_before_send(db):
    product, post = company_and_post(db)
    conn = save_verified_channel(db, await verify_channel(FakeTelegram(), "@craft_studio"))
    now = datetime.now(timezone.utc)
    pub = schedule_post(db, post.id, conn.id, now+timedelta(minutes=1), now=now)
    post.version += 1
    db.commit()
    fake = FakeTelegram()
    enabled = Settings(telegram_bot_token="unit-test-only",
                       content_telegram_autopost_enabled=True, _env_file=None)
    assert await run_due_telegram_post_once(
        sessions(db), enabled, api=fake, now=now+timedelta(minutes=2),
    ) == 1
    db.refresh(pub)
    assert pub.status == "failed_before_send"
    assert not any(name == "sendMessage" for name, _ in fake.calls)
