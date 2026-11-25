"""Russian explainer-video requests: reliable routing, no model call, safe approval and tenant isolation."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.dispatcher import service
from app.dispatcher.decision import DispatcherDecision
from app.dispatcher.executors import Context
from app.dispatcher.fast_routes import quick_video_request
from app.models.domain import Product, Task, TaskStatus


@pytest.mark.parametrize(("utterance", "topic", "length"), [
    ("Давай сделаем видео. Объяснение. на тему «Горячий предмет тяжелее холодного?»",
     "Горячий предмет тяжелее холодного?", "30"),
    ("Давай сделаем видеообъяснение на тему «Горячий предмет тяжелее холодного»",
     "Горячий предмет тяжелее холодного", "30"),
    ("Минутное видео на тему: как шумоподавление в наушниках убирает шум метро.",
     "как шумоподавление в наушниках убирает шум метро", "60"),
    ("Полуминутный ролик на тему: как устроены наушники.",
     "как устроены наушники", "30"),
    ("Создай рисованное видео про закон сохранения энергии",
     "закон сохранения энергии", "30"),
    ("Подготовь минутное видеообъяснение: почему заряженная батарея массивнее",
     "Почему заряженная батарея массивнее", "60"),
    ("Сделай объясняющее видео на тему тепловой энергии, 60 секунд",
     "тепловой энергии", "60"),
])
def test_explicit_video_explanations_have_an_exact_topic(utterance, topic, length):
    assert quick_video_request(utterance) == {"topic": topic, "length": length}


@pytest.mark.parametrize("utterance", [
    "Давай сделаем объяснение на тему «Горячий предмет тяжелее холодного»",
    "Сделай видео про нашу компанию",
    "Сделай сценарий рисованного видео на тему энергии",
    "Сделай промпт для видеообъяснения на тему тепловой энергии",
    "Покажи готовое видео про энергию",
    "Отмени рисованное видео про энергию",
    "Не делай видеообъяснение на тему тепловой энергии",
    "Сделай видеообъяснение",
    "Минутное видео на тему: как устроены наушники?",
    "Минутное видео на тему: шум метро; опубликуй его",
    "Минутное видео на тему: шум метро без рисунков",
])
def test_ambiguous_or_non_rendering_requests_do_not_start_video(utterance):
    assert quick_video_request(utterance) is None


@pytest.mark.asyncio
async def test_video_explainer_works_without_llm_and_requires_paid_confirmation(db, tmp_path):
    product = Product(name="Business A", slug="explain-business-a")
    another = Product(name="Business B", slug="explain-business-b")
    db.add_all([product, another])
    db.commit()

    settings = Settings(
        _env_file=None, products_root=tmp_path,
        whiteboard_storyboard_model="story-model",
        whiteboard_image_model="google/image",
        whiteboard_speech_model="speech-model",
        whiteboard_regions_model="regions-model",
    )

    class OfflineLLM:
        async def generate(self, *args, **kwargs):
            raise AssertionError("The dispatcher must not call the LLM for a clear video request")

    request = "Минутное видео на тему: как шумоподавление в наушниках убирает шум метро."
    ctx_a = Context(db=db, product=product, settings=settings, account="owner")
    first = await service.handle_message(
        db, ctx_a, OfflineLLM(), text=request, client_key="same-mobile-message", mode="voice",
    )
    answer = first["messages"][1]
    assert answer["intent"] == "execute"
    assert any(block["type"] == "confirm" and block["paid"] for block in answer["blocks"])
    task_a = db.scalar(select(Task).where(Task.product_id == product.id, Task.assigned_agent == "whiteboard_renderer"))
    assert task_a is not None and task_a.status == TaskStatus.WAITING_APPROVAL
    assert task_a.product_id == product.id

    repeated = await service.handle_message(
        db, ctx_a, OfflineLLM(), text=request, client_key="same-mobile-message", mode="voice",
    )
    assert repeated["replayed"] is True
    assert len(list(db.scalars(select(Task).where(Task.product_id == product.id)))) == 1

    ctx_b = Context(db=db, product=another, settings=settings, account="owner")
    second = await service.handle_message(
        db, ctx_b, OfflineLLM(), text=request, client_key="same-mobile-message", mode="text",
    )
    assert second["replayed"] is False
    assert second["messages"][1]["intent"] == "execute"
    task_b = db.scalar(select(Task).where(Task.product_id == another.id,
                                           Task.assigned_agent == "whiteboard_renderer"))
    assert task_b is not None and task_b.id != task_a.id
    assert task_b.status == TaskStatus.WAITING_APPROVAL


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", ["unsupported", "unknown"])
async def test_dispatcher_recovers_semantic_rejection_before_acting(db, tmp_path, monkeypatch, initial):
    product = Product(name="Recovery", slug="dispatcher-recovery")
    db.add(product)
    db.commit()
    ctx = Context(db=db, product=product, settings=Settings(_env_file=None, products_root=tmp_path),
                  account="owner")
    attempts = []

    async def fake_ask(*args, recovery=False):
        attempts.append(recovery)
        if recovery:
            return DispatcherDecision(intent="execute", capability="show_work", confidence=0.95), "test-model"
        if initial == "unknown":
            return DispatcherDecision(intent="execute", capability="made_up_tool", confidence=0.95), "test-model"
        return DispatcherDecision(intent="unsupported", confidence=0.95), "test-model"

    monkeypatch.setattr(service, "ask_model", fake_ask)
    response = await service.handle_message(
        db, ctx, object(), text="Расскажи, какие поручения сейчас выполняются",
        client_key="semantic-recovery",
    )
    assert attempts == [False, True]
    answer = response["messages"][-1]
    assert answer["intent"] == "execute"
    from app.dispatcher.models import DispatcherMessage
    from uuid import UUID
    stored = db.get(DispatcherMessage, UUID(answer["id"]))
    assert stored.capability == "show_work"
    assert "Этого я пока не умею" not in answer["text"]


@pytest.mark.asyncio
async def test_dispatcher_exhausted_recovery_offers_clarification(db, tmp_path, monkeypatch):
    product = Product(name="Clarification", slug="dispatcher-clarification")
    db.add(product)
    db.commit()
    ctx = Context(db=db, product=product, settings=Settings(_env_file=None, products_root=tmp_path),
                  account="owner")
    attempts = []

    async def fake_ask(*args, recovery=False):
        attempts.append(recovery)
        return DispatcherDecision(intent="unsupported", confidence=0.95), "test-model"

    monkeypatch.setattr(service, "ask_model", fake_ask)
    response = await service.handle_message(
        db, ctx, object(), text="Уладь это по-другому", client_key="exhausted-recovery",
    )
    assert attempts == [False, True]
    answer = response["messages"][-1]
    assert answer["intent"] == "clarify"
    assert any(block["type"] == "chips" for block in answer["blocks"])
    assert "Я записал" not in answer["text"]
    assert not list(db.scalars(select(Task).where(Task.product_id == product.id)))


@pytest.mark.asyncio
async def test_submitted_video_form_creates_only_one_unapproved_job(db, tmp_path, monkeypatch):
    from app.api.dispatcher import FormSubmissionIn
    product = Product(name="Video form", slug="video-form")
    db.add(product)
    db.commit()
    ctx = Context(db=db, product=product, account="owner", settings=Settings(
        _env_file=None, products_root=tmp_path, whiteboard_storyboard_model="story-model",
        whiteboard_image_model="google/image", whiteboard_speech_model="speech-model",
        whiteboard_regions_model="regions-model"))

    async def fake_ask(*args, recovery=False):
        return DispatcherDecision(intent="form", capability="whiteboard_video",
            confidence=0.95, params={"topic": "Как устроена онлайн-запись", "length": "60"}), "test-model"

    monkeypatch.setattr(service, "ask_model", fake_ask)
    response = await service.handle_message(db, ctx, object(), text="Покажи форму настроек",
                                            client_key="initial-video-form")
    source = response["messages"][-1]
    assert not list(db.scalars(select(Task).where(Task.product_id == product.id)))
    request = FormSubmissionIn(message_id=source["id"], revision=0,
        params={"topic": "Как устроена онлайн-запись", "length": "60",
                "max_api_calls": "45", "visual_review": "false"})
    prepared = await service.handle_message(db, ctx, None, text="", client_key="submit-video",
                                             form_submission=request)
    card = next(b for b in prepared["messages"][-1]["blocks"] if b["type"] == "confirm")
    assert card["paid"] and card["state"] == "pending"
    tasks = list(db.scalars(select(Task).where(Task.product_id == product.id)))
    assert len(tasks) == 1 and tasks[0].status == TaskStatus.WAITING_APPROVAL
    repeated = await service.handle_message(db, ctx, None, text="", client_key="second-tab",
                                             form_submission=request)
    assert repeated["replayed"]
    assert len(list(db.scalars(select(Task).where(Task.product_id == product.id)))) == 1
