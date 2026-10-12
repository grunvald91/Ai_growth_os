"""Offline acceptance tests for the new short single-post production route."""
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.content.quick_post import QuickPostRequest, run_quick_post
from app.content.service import queue_content
from app.core.config import Settings
from app.llm.schemas import LLMResponse, LLMUsage
from app.models.domain import Artifact, Product, Task, TaskStatus, Publication


class FakePostProvider:
    def __init__(self, *, bad_evidence=False, brand_leak=False):
        self.requests = []
        self.bad_evidence = bad_evidence
        self.brand_leak = brand_leak

    async def generate(self, request):
        self.requests.append(request.model_copy(deep=True))
        if len(self.requests) == 1:
            result = {
                "audience_pain": "Люди боятся ошибиться при выборе подарка для близкого человека",
                "hook": "Как выбрать букет, который точно порадует",
                "useful_takeaway": "Спросить о поводе, любимых цветах и условиях доставки",
                "suggested_cta": "Расскажите, для кого подбираете букет",
                "evidence_ids": ["made-up-source"] if self.bad_evidence else [],
            }
        else:
            result = {
                "title": "Как выбрать букет для близкого",
                "body": (
                    "Не всякий красивый букет подходит конкретному человеку. "
                    "Для начала вспомните, какие оттенки и ароматы ему нравятся. "
                    "Учитывайте повод, условия перевозки и то, как долго цветы будут без воды. "
                    "Лучше спросить о предпочтениях, чем выбирать только по размеру композиции. "
                    "Расскажите, для кого подбираете букет"
                ),
                "cta": "Расскажите, для кого подбираете букет",
                "evidence_ids": [],
                "editor_notes": ["Не обещать конкретный срок жизни букета без данных"],
            }
            if self.brand_leak:
                result["body"] += " Также советуем приобрести подписку ServGo."
        return LLMResponse(content=json.dumps(result, ensure_ascii=False), model="fake",
                           usage=LLMUsage(input_tokens=90, output_tokens=140))


@pytest.mark.asyncio
async def test_single_post_uses_two_llm_calls_and_queues_review_not_publication(db, tmp_path):
    company = Product(name="Цветочная мастерская", slug="florist-quickpost")
    db.add(company)
    db.commit()
    task = queue_content(db, company.id, "Как выбрать букет",
                         format="post", channel="telegram", audience="Покупатели цветов")
    request_artifact = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "content_request"
    ))
    parsed = QuickPostRequest.model_validate_json(request_artifact.content)
    provider = FakePostProvider()
    result = await run_quick_post(
        db, Settings(products_root=tmp_path),
        parsed.model_copy(update={"task_id": task.id}), provider=provider,
    )
    assert result is True
    assert len(provider.requests) == 2
    assert provider.requests[0].metadata["team_role"] == "post_strategist"
    assert provider.requests[1].metadata["team_role"] == "post_writer_editor"
    assert "Цветочная мастерская" in provider.requests[0].messages[0].content
    assert db.get(Task, task.id).status == TaskStatus.COMPLETED
    posts = list(db.scalars(select(Artifact).where(
        Artifact.product_id == company.id, Artifact.kind == "channel_text"
    )))
    assert len(posts) == 1
    assert posts[0].status.value == "awaiting_approval"
    assert json.loads(posts[0].content)["channel"] == "telegram"
    assert json.loads(posts[0].content)["origin"] == "quick_post"
    assert not list(db.scalars(select(Publication)))
    assert len(list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "content_stage_output"
    )))) == 2


@pytest.mark.asyncio
async def test_single_post_rejects_fake_evidence_and_marks_job_failed(db, tmp_path):
    company = Product(name="Салон", slug="salon-quickpost")
    db.add(company)
    db.commit()
    task = queue_content(db, company.id, "Как выбрать уход",
                         format="post", channel="vk")
    request_artifact = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "content_request"
    ))
    result = await run_quick_post(
        db, Settings(products_root=tmp_path),
        QuickPostRequest.model_validate_json(request_artifact.content).model_copy(
            update={"task_id": task.id}
        ),
        provider=FakePostProvider(bad_evidence=True),
    )
    assert result is False
    assert db.get(Task, task.id).status == TaskStatus.FAILED
    assert list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "channel_text"
    ))) == []


def test_single_post_rejects_unsupported_channels_and_dedupes_queue(db):
    company = Product(name="Ремонт", slug="repair-post")
    db.add(company)
    db.commit()
    with pytest.raises(ValidationError):
        queue_content(db, company.id, "Поломка инструмента",
                      format="post", channel="unknown")
    with pytest.raises(ValueError, match="Unknown content format"):
        queue_content(db, company.id, "Поломка инструмента",
                      format="reels")
    first = queue_content(db, company.id, "Поломка инструмента",
                          format="post", channel="telegram")
    again = queue_content(db, company.id, "Поломка инструмента",
                          format="post", channel="telegram")
    other = queue_content(db, company.id, "Поломка инструмента",
                          format="post", channel="vk")
    assert first.id == again.id
    assert other.id != first.id


@pytest.mark.asyncio
async def test_single_post_rejects_unrequested_servgo_brand_for_other_company(db, tmp_path):
    company = Product(name="Кафе", slug="local-cafe")
    db.add(company)
    db.commit()
    task = queue_content(db, company.id, "Выбираем сезонное меню",
                         format="post", channel="telegram")
    request_artifact = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "content_request"
    ))
    success = await run_quick_post(
        db, Settings(products_root=tmp_path),
        QuickPostRequest.model_validate_json(request_artifact.content).model_copy(
            update={"task_id": task.id}
        ),
        provider=FakePostProvider(brand_leak=True),
    )
    assert success is False
    assert db.get(Task, task.id).status == TaskStatus.FAILED
    assert not list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "channel_text"
    )))
