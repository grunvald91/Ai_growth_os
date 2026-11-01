"""Russian explainer-video requests: reliable routing, no model call, safe approval and tenant isolation."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.dispatcher import service
from app.dispatcher.executors import Context
from app.dispatcher.fast_routes import quick_video_request
from app.models.domain import Product, Task, TaskStatus


@pytest.mark.parametrize(("utterance", "topic", "length"), [
    ("Давай сделаем видео. Объяснение. на тему «Горячий предмет тяжелее холодного?»",
     "Горячий предмет тяжелее холодного?", "30"),
    ("Давай сделаем видеообъяснение на тему «Горячий предмет тяжелее холодного»",
     "Горячий предмет тяжелее холодного", "30"),
    ("Создай рисованное видео про закон сохранения энергии",
     "закон сохранения энергии", "30"),
    ("Подготовь минутное видеообъяснение: почему заряженная батарея массивнее",
     "Почему заряженная батарея массивнее", "60"),
    ("Сделай объясняющее видео на тему тепловой энергии, 60 секунд",
     "тепловой энергии, 60 секунд", "60"),
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

    request = "Давай сделаем видео. Объяснение. на тему «Горячий предмет тяжелее холодного?»"
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
