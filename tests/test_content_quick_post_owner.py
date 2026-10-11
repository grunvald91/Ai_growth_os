"""Owner control and worker-dispatch tests for individual posts."""
import json
from contextlib import contextmanager

import pytest
from sqlalchemy import select

from app.content.quick_post import QuickPostRequest
from app.content.service import queue_content
from app.content.telegram import TelegramContent
from app.content.worker import run_content_once
from app.core.config import Settings
from app.models.domain import Artifact, ArtifactStatus, Product, Task, TaskStatus, Publication


class FakeBot:
    def __init__(self):
        self.messages = []

    async def _send(self, chat, text, reply_markup=None):
        self.messages.append((chat, text, reply_markup))


def make_sessions(db):
    @contextmanager
    def sessions():
        yield db
    return sessions


@pytest.mark.asyncio
async def test_owner_can_choose_channel_and_queue_one_post(db, tmp_path):
    product = Product(name="Школа рисования", slug="drawing-school")
    db.add(product)
    db.commit()
    bot = FakeBot()
    content = TelegramContent(bot, make_sessions(db),
                              lambda: Settings(products_root=tmp_path))

    await content.product(7, product.id)
    assert "✍️ Один пост" in str(bot.messages[-1][2])
    assert "📦 Полный пакет" in str(bot.messages[-1][2])

    assert await content.callback(7, f"content:postchannels:{product.id}")
    assert "ВКонтакте" in str(bot.messages[-1][2])
    assert await content.callback(7, f"content:posttg:{product.id}")
    assert "тему поста" in bot.messages[-1][1]

    assert await content.message({
        "chat": {"id": 7}, "text": "Как начать рисовать акварелью",
    })
    task = db.scalar(select(Task).where(Task.product_id == product.id,
                                        Task.assigned_agent == "content_factory"))
    assert task is not None and task.status == TaskStatus.QUEUED
    artifact = db.scalar(select(Artifact).where(
        Artifact.product_id == product.id, Artifact.task_id == task.id,
        Artifact.kind == "content_request",
    ))
    request = QuickPostRequest.model_validate_json(artifact.content)
    assert request.mode == "post" and request.channel == "telegram"
    assert content.post_target is None

    await content.callback(7, f"content:postvk:{product.id}")
    assert content.post_target is not None
    await content.callback(7, f"content:product:{product.id}")
    assert content.post_target is None


@pytest.mark.asyncio
async def test_owner_post_approval_is_idempotent_and_does_not_publish(db, tmp_path):
    product = Product(name="Студия", slug="owner-approved-post")
    db.add(product)
    db.flush()
    task = Task(product_id=product.id, title="post",
                assigned_agent="content_factory", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    post = Artifact(
        product_id=product.id, task_id=task.id, kind="channel_text",
        title="Полезный текст",
        content=json.dumps({
            "origin": "quick_post", "channel": "telegram",
            "title": "Полезный текст", "body": "Текст для проверки",
        }, ensure_ascii=False),
        status=ArtifactStatus.AWAITING_APPROVAL,
    )
    db.add(post)
    db.commit()
    bot = FakeBot()
    content = TelegramContent(bot, make_sessions(db),
                              lambda: Settings(products_root=tmp_path))

    await content.callback(7, f"content:asset:{post.id}")
    assert "Утвердить текст" in str(bot.messages[-1][2])
    await content.callback(7, f"content:approvepost:{post.id}")
    db.refresh(post)
    assert post.status == ArtifactStatus.APPROVED
    assert post.version == 2
    await content.callback(7, f"content:approvepost:{post.id}")
    db.refresh(post)
    assert post.version == 2
    assert list(db.scalars(select(Publication))) == []


@pytest.mark.asyncio
async def test_worker_dispatches_quick_post_and_preserves_full_package(monkeypatch, db, tmp_path):
    product = Product(name="Ремонт", slug="quick-worker")
    db.add(product)
    db.commit()
    task = queue_content(db, product.id, "Обслуживание электроинструмента",
                         format="post", channel="vk")
    calls = []

    async def fake_quick_post(db_arg, settings, request, provider=None):
        calls.append(request)
        db_arg.get(Task, request.task_id).status = TaskStatus.COMPLETED
        db_arg.commit()
        return True

    monkeypatch.setattr("app.content.worker.run_quick_post", fake_quick_post)
    result = await run_content_once(make_sessions(db), Settings(products_root=tmp_path))
    assert result == 1 and len(calls) == 1
    assert calls[0].mode == "post"
    assert calls[0].product_id == product.id and calls[0].task_id == task.id
