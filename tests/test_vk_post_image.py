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
    with Image.open(io.BytesIO(export_vk_png(picture(), ratio="1:1"))) as square:
        assert square.size == (1080, 1080)
    assert "square 1:1" in vk_visual_prompt("Компания", "Тема", "Текст", ratio="1:1")


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

    async def fake_request(settings, model, prompt, ratio, *, preserve_source=False):
        requests.append((model, prompt, ratio, preserve_source))
        return picture()

    monkeypatch.setattr("app.content.vk_post_image.request_image", fake_request)
    asset = await prepare_vk_post_image(db, settings, product=product, task=task,
                                        post=post, model_choice="auto")
    with Image.open(tmp_path / "_creative_images" / str(product.id) /
                    str(task.id) / "frame-01.png") as actual:
        assert actual.size == (1080, 1350)
    assert json.loads(post.content)["image_artifact_id"] == str(asset.id)
    assert json.loads(post.content)["image_state"] == "ready"
    assert len(requests) == 1 and requests[0][2:] == ("4:5", True)
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

def test_vk_export_does_not_stretch_subject(tmp_path):
    """A square source must be center-cropped, not geometrically distorted."""
    raw = io.BytesIO()
    # Left red and right blue stripes show if the image was stretched.
    source = Image.new("RGB", (1000, 1000), (0, 0, 0))
    for x in range(400, 600):
        for y in range(1000):
            source.putpixel((x, y), (255, 0, 0))
    source.save(raw, format="PNG")
    with Image.open(io.BytesIO(export_vk_png(raw.getvalue(), ratio="4:5"))) as frame:
        # Original stripe is 200px wide, cropping to 800px produces 270px at 1080px target.
        red_at_middle = sum(1 for x in range(1080) if frame.getpixel((x, 675))[0] > 220)
        assert 250 <= red_at_middle <= 290


@pytest.mark.asyncio
async def test_vk_square_preset_is_persisted(db, tmp_path, monkeypatch):
    product = Product(name="Квадрат", slug="vk-square")
    db.add(product)
    db.flush()
    task = Task(product_id=product.id, assigned_agent="content_factory",
                title="CONTENT: post:vk:square", status="running")
    db.add(task)
    db.flush()
    post = Artifact(product_id=product.id, task_id=task.id, kind="channel_text",
                    title="Квадрат", status="awaiting_approval",
                    content=json.dumps({"channel": "vk", "title": "Квадрат",
                                        "body": "Описание для квадратной иллюстрации"}))
    db.add(post)
    db.commit()
    settings = Settings(_env_file=None, products_root=tmp_path, xai_api_key="dummy")
    requests = []
    async def fake_request(settings, model, prompt, ratio, *, preserve_source=False):
        requests.append((ratio, preserve_source))
        return picture()
    monkeypatch.setattr("app.content.vk_post_image.request_image", fake_request)
    asset = await prepare_vk_post_image(db, settings, product=product, task=task,
                                        post=post, model_choice="auto", ratio="1:1")
    with Image.open(tmp_path / "_creative_images" / str(product.id) /
                    str(task.id) / "frame-01.png") as result:
        assert result.size == (1080, 1080)
    assert json.loads(asset.content)["ratio"] == "1:1"
    assert json.loads(post.content)["image_ratio"] == "1:1"
    assert requests == [("1:1", True)]


@pytest.mark.asyncio
async def test_dispatcher_create_vk_post_with_image_preserves_confirmation_and_ratio(db, tmp_path):
    from app.dispatcher import service
    from app.dispatcher.editable import CreateHints
    from app.dispatcher.outcome import Context
    from app.models.domain import TaskStatus

    product = Product(name="Компания VK", slug="vk-dispatcher-image")
    db.add(product)
    db.commit()
    ctx = Context(db=db, product=product,
                  settings=Settings(_env_file=None, products_root=tmp_path,
                                    xai_api_key="test-not-used"),
                  account="owner")
    topic = "Зачем бизнесу онлайн-запись?"
    creation = CreateHints(format="post", topic=topic, platform="vk",
                           image_model="gemini", aspect_ratio="1:1")
    answer = await service.handle_message(
        db, ctx, None, text=topic, client_key="vk-image-confirm-1",
        creation=creation)
    card = next(x for x in answer["messages"][-1]["blocks"] if x["type"] == "confirm")
    assert card["paid"] is True
    assert card["editable"]["aspect_ratio"] == "1:1"
    assert card["editable"]["image_model"] == "gemini"
    assert db.scalars(select(Task).where(
        Task.product_id == product.id, Task.assigned_agent == "content_factory")).first() is None
    await service.confirm(db, ctx, card["confirmation_id"], accept=True)
    task = db.scalars(select(Task).where(
        Task.product_id == product.id, Task.assigned_agent == "content_factory")).first()
    assert task is not None and task.status == TaskStatus.QUEUED
    request = db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "content_request")).first()
    data = json.loads(request.content)
    assert data["channel"] == "vk"
    assert data["image_model"] == "gemini"
    assert data["image_ratio"] == "1:1"


def test_invalid_dispatcher_post_image_scope():
    from app.dispatcher.editable import CreateHints, EditCreation
    with pytest.raises(ValueError):
        CreateHints(format="post", topic="Тема для поста", platform="telegram",
                    image_model="auto", aspect_ratio="4:5")
    with pytest.raises(ValueError):
        EditCreation(confirmation_id="test", revision=0, format="post",
                     topic="Тема для поста", platform="vk",
                     image_model="auto", aspect_ratio="9:16")


def test_owner_rewrite_invalidates_old_vk_picture(db):
    from app.content.manual_edits import save_manual_edit
    from app.content.revisions import source_fingerprint
    from uuid import uuid4
    product = Product(name="Редакция", slug="vk-rewrite-picture")
    db.add(product)
    db.flush()
    post = Artifact(product_id=product.id, kind="channel_text",
                    title="Старый пост", status="awaiting_approval",
                    content=json.dumps({
                        "channel": "vk", "title": "Старый пост", "body": "Старая мысль",
                        "image_model": "gemini", "image_ratio": "4:5",
                        "image_state": "ready", "image_artifact_id": str(uuid4()),
                        "image_sha256": "f" * 64,
                    }))
    db.add(post)
    db.commit()
    revised = save_manual_edit(db, product.id, post.id, {
        "source_version": str(post.version),
        "source_digest": source_fingerprint(post),
        "title": "Новый пост",
        "edit_0": "Теперь совсем другая мысль",
    })
    data = json.loads(revised.content)
    assert data["image_model"] == "gemini" and data["image_ratio"] == "4:5"
    assert data["image_state"] == "stale"
    assert "image_artifact_id" not in data and "image_sha256" not in data
