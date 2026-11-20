"""VK image draft coverage: no external paid image calls or VK writes."""
from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image
from sqlalchemy import select

from app.comments.models import CommentChannel
from app.content.quick_post import QuickPostRequest
from app.content.vk_post_image import prepare_vk_post_image, export_vk_png, vk_visual_prompt
from app.core.config import Settings
from app.models.domain import Artifact, Product, Task
from app.social_vk import schedule


def picture() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (960, 1200), (22, 80, 111)).save(buffer, "PNG")
    return buffer.getvalue()


def test_vk_post_only_opt_in_and_prompt_no_fake_claims():
    fields = {"mode": "post", "product_id": "fedc10d3-2568-475d-ab6a-6441cfca3b50",
              "topic": "Запись клиентов", "channel": "vk", "image_model": "auto"}
    assert QuickPostRequest.model_validate(fields).image_model == "auto"
    with pytest.raises(ValueError):
        QuickPostRequest.model_validate({**fields, "channel": "telegram"})
    assert QuickPostRequest.model_validate({**fields, "image_model": "none",
                                            "channel": "telegram"}).image_model == "none"
    prompt = vk_visual_prompt("Клиника", "Удобная запись", "Как записать клиентов без очереди")
    assert "How" not in prompt
    assert "NO letters" in prompt
    with Image.open(io.BytesIO(export_vk_png(picture()))) as generated:
        assert generated.format == "PNG"
        assert generated.size == (1080, 1350)


@pytest.mark.asyncio
async def test_vk_image_saved_and_reused_without_repeat_paid_api(db, tmp_path, monkeypatch):
    product = Product(name="Тестовая мастерская", slug="test-vk-visual")
    db.add(product)
    db.flush()
    task = Task(product_id=product.id, assigned_agent="content_factory",
                title="CONTENT: post:vk:photo", status="running")
    db.add(task)
    db.flush()
    post = Artifact(product_id=product.id, task_id=task.id, kind="channel_text",
                    title="Зачем онлайн-запись",
                    content=json.dumps({"channel": "vk", "title": "Зачем онлайн-запись",
                                        "body": "Как мастерской меньше отвлекаться на звонки",
                                        "image_model": "auto", "image_state": "pending"}),
                    status="awaiting_approval")
    db.add(post)
    db.commit()
    settings = Settings(_env_file=None, products_root=tmp_path, xai_api_key="dummy")
    requests = []

    async def fake_request(settings, model, prompt, ratio):
        requests.append((model, prompt, ratio))
        return picture()

    monkeypatch.setattr("app.content.vk_post_image.request_image", fake_request)
    asset = await prepare_vk_post_image(db, settings, product=product, task=task,
                                        post=post, model_choice="auto")
    with Image.open(tmp_path / "_creative_images" / str(product.id) /
                    str(task.id) / "frame-01.png") as actual:
        assert actual.size == (1080, 1350)
    assert json.loads(post.content)["image_artifact_id"] == str(asset.id)
    assert json.loads(post.content)["image_state"] == "ready"
    assert len(requests) == 1 and requests[0][2] == "4:5"
    again = await prepare_vk_post_image(db, settings, product=product, task=task,
                                         post=post, model_choice="auto")
    assert again.id == asset.id
    assert len(requests) == 1
    intents = db.scalars(select(Artifact).where(
        Artifact.product_id == product.id, Artifact.kind == "vk_post_image_intent")).all()
    assert len(intents) == 1


def test_vk_image_post_must_not_silently_send_text_only(db):
    product = Product(name="Мастерская", slug="vk-photo-not-text")
    db.add(product)
    db.flush()
    post = Artifact(product_id=product.id, kind="channel_text",
                    title="Пост с фото", status="approved",
                    content=json.dumps({"channel": "vk", "body": "Пост с изображением",
                                        "image_model": "gemini", "image_state": "ready"}))
    channel = CommentChannel(product_id=product.id, platform="vk", account_id="12345",
                             account_name="Группа", status="connected")
    db.add_all([post, channel])
    db.commit()
    with pytest.raises(ValueError, match="иллюстрацию"):
        schedule(db, product.id, post.id, datetime.now(timezone.utc) + timedelta(minutes=10))
