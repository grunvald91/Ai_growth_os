"""Offline tests for single-format creative jobs: shorts, stories and static images."""
import json
from contextlib import contextmanager

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.content.creative import (
    CreativeRequest, CreativeDraft, queue_creative, run_creative_once,
)
from app.content.worker import run_content_once
from app.core.config import Settings
from app.llm.schemas import LLMResponse, LLMUsage
from app.models.domain import Artifact, Product, Task, TaskStatus


def strategy_payload():
    return {
        "audience_pain": "Покупателю трудно выбрать подходящий подарок с первого раза",
        "audience_insight": "Практические подсказки полезнее общих рекламных обещаний",
        "hook": "Что спросить перед выбором букета",
        "core_takeaway": "Выяснить повод, предпочтения и доставку",
        "cta": "Уточните детали подарка перед выбором",
        "evidence_ids": [],
    }


def creative_payload(fmt, platform):
    frames = []
    count = {"short_video":3, "story":3, "static_image":1}[fmt]
    edges = [0, 8, 21, 30] if fmt == "short_video" else [0, 5, 10, 15]
    for i in range(count):
        frames.append({
            "number": i + 1,
            "start_second": 0 if fmt == "static_image" else edges[i],
            "end_second": 0 if fmt == "static_image" else edges[i + 1],
            "purpose": "Узнаваемая ситуация и конкретный совет",
            "visual_prompt": (
                "Вертикальный реалистичный кадр: небольшая цветочная мастерская, "
                "чистая композиция, мягкий естественный свет, свободное место для текста."
            ),
            "on_screen_text": "Уточните важные детали",
            "action_or_animation": "Спокойный переход к полезной подсказке",
            "spoken_text": "Подбирайте цветы с учётом человека и повода",
            "transition": "Плавная склейка",
        })
    return {
        "format": fmt,
        "platform": platform,
        "title": "Как выбрать букет в подарок",
        "hook": "Сначала выясните предпочтения",
        "cta": "Уточните детали подарка перед выбором",
        "voiceover_script": "Спросите о поводе, вкусах и условиях хранения.",
        "caption": "Выбор букета начинается с простых вопросов о человеке и поводе.",
        "cover_text": "Как выбрать букет",
        "visual_style": "Светлый спокойный реализм без выдуманных эффектов и обещаний.",
        "frames": frames,
        "production_notes": ["Проверить шрифт и читаемость текста"],
        "evidence_ids": [],
    }


class FakeCreativeProvider:
    def __init__(self, fmt, platform):
        self.calls = []
        self.responses = [strategy_payload(), creative_payload(fmt, platform)]

    async def generate(self, request):
        self.calls.append(request)
        return LLMResponse(
            content=json.dumps(self.responses[len(self.calls)-1], ensure_ascii=False),
            model="fake", usage=LLMUsage(input_tokens=80, output_tokens=210),
        )


@pytest.mark.parametrize("fmt,platform", [
    ("short_video", "youtube_shorts"),
    ("short_video", "instagram_reels"),
    ("short_video", "vk_clips"),
    ("story", "telegram"), ("story", "vk"),
    ("static_image", "dzen"), ("static_image", "telegram"),
])
@pytest.mark.asyncio
async def test_creative_job_runs_only_two_specialists_and_keeps_approval(db, tmp_path, fmt, platform):
    product = Product(name="Цветочная мастерская", slug=("floral-" + fmt + platform).replace("_", "-"))
    db.add(product)
    db.commit()
    request = CreativeRequest(
        mode="creative", product_id=product.id, format=fmt,
        platform=platform, topic="Как выбрать букет",
    )
    task = queue_creative(db, request)
    fake = FakeCreativeProvider(fmt, platform)
    assert await run_creative_once(
        db, Settings(products_root=tmp_path), request,
        task_id=task.id, provider=fake,
    ) is True
    assert len(fake.calls) == 2
    assert [call.metadata["team_role"] for call in fake.calls] == [
        "creative_strategist", "creative_director",
    ]
    db.refresh(task)
    assert task.status == TaskStatus.COMPLETED
    artifact = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "creative_brief",
    ))
    assert artifact is not None and artifact.status.value == "awaiting_approval"
    meta = json.loads(artifact.content)
    assert meta["format"] == fmt and meta["platform"] == platform
    assert "не готовый MP4" in meta["preview_text"]
    assert not list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "video_render",
    )))


def test_creative_timeline_and_platform_mismatch_fail_closed(db):
    from uuid import uuid4
    with pytest.raises(ValidationError):
        CreativeRequest(mode="creative", product_id=uuid4(), topic="Сделать ролик",
                        format="short_video", platform="dzen")
    invalid = creative_payload("short_video", "youtube_shorts")
    invalid["frames"][1]["start_second"] = 11
    with pytest.raises(ValidationError):
        CreativeDraft.model_validate(invalid)
    mismatch = creative_payload("story", "vk")
    mismatch["format"] = "static_image"
    with pytest.raises(ValidationError):
        CreativeDraft.model_validate(mismatch)


@pytest.mark.asyncio
async def test_worker_dispatches_creative_job(monkeypatch, db, tmp_path):
    product = Product(name="Салон", slug="salon-creative-worker")
    db.add(product)
    db.commit()
    request = CreativeRequest(mode="creative", product_id=product.id,
                              topic="Как ухаживать за руками", format="story",
                              platform="telegram")
    task = queue_creative(db, request)
    calls = []

    async def fake_runner(db_arg, settings, data, *, task_id, provider=None):
        calls.append((data.format, data.platform, task_id))
        db_arg.get(Task, task_id).status = TaskStatus.COMPLETED
        db_arg.commit()
        return True

    @contextmanager
    def session():
        yield db

    monkeypatch.setattr("app.content.worker.run_creative_once", fake_runner)
    assert await run_content_once(session, Settings(products_root=tmp_path)) == 1
    assert calls == [("story", "telegram", task.id)]



@pytest.mark.asyncio
async def test_critic_invalid_timeline_preserves_valid_creative_and_audit(db, tmp_path):
    """Reproduce P1: JSON Schema accepts a broken timeline, Pydantic rejects it."""
    from app.models.domain import CostRecord
    original = creative_payload("short_video", "youtube_shorts")
    invalid = json.loads(json.dumps(original))
    invalid["frames"][1]["start_second"] = 12  # Original boundary is 8.
    problem = {"findings": [{
        "id": "timeline", "severity": "major",
        "problem": "Storyboard transition lacks necessary visual continuity",
        "evidence_in_draft": "Storyboard cut between two adjacent frames",
        "proposed_fix": "Improve the connecting shot without dropping the interval",
        "link_to_original_goal": "A continuous short video with no missing action",
    }]}
    author = {"decisions": [{
        "finding_id":"timeline", "decision":"accept",
        "rationale":"Continuity is necessary for this video",
    }], "updated_output":invalid}

    class Provider:
        def __init__(self):
            self.outputs=[strategy_payload(), {"findings":[]}, original, problem, author]
            self.calls=[]
        async def generate(self, request):
            self.calls.append(request)
            return LLMResponse(content=json.dumps(self.outputs.pop(0), ensure_ascii=False),
                               model="fake", usage=LLMUsage(input_tokens=80,output_tokens=100),
                               stop_reason="end_turn")

    company=Product(name="Цветочная мастерская",slug="critic-creative-timeline")
    db.add(company);db.commit()
    task_request=CreativeRequest(mode="creative",product_id=company.id,format="short_video",
                                 platform="youtube_shorts",topic="Выбираем букет")
    task=queue_creative(db,task_request)
    provider=Provider()
    settings=Settings(_env_file=None, products_root=tmp_path,agent_critic_enabled=True)
    assert await run_creative_once(db,settings,task_request,task_id=task.id,provider=provider)
    assert db.get(Task,task.id).status==TaskStatus.COMPLETED
    assert len(provider.calls)==5
    rows=list(db.scalars(select(Artifact).where(
        Artifact.task_id==task.id,Artifact.kind=="content_stage_output"
    )))
    review_artifacts=list(db.scalars(select(Artifact).where(
        Artifact.task_id==task.id,Artifact.kind=="agent_quality_review"
    )))
    reviews=[json.loads(row.content) for row in review_artifacts]
    assert {r["status"] for r in reviews}=={"no_material_issues","rejected_domain_revision"}
    retained=next(row for row in rows if json.loads(row.content).get("frames"))
    assert json.loads(retained.content)["frames"][1]["start_second"]==8
    rejected=next(review for review in reviews if review["status"]=="rejected_domain_revision")
    assert rejected["findings"][0]["id"]=="timeline"
    assert rejected["decisions"][0]["decision"]=="accept"
    final=db.scalar(select(Artifact).where(Artifact.task_id==task.id,Artifact.kind=="creative_brief"))
    assert final is not None
    costs=list(db.scalars(select(CostRecord).where(CostRecord.product_id==company.id)))
    assert len(costs)==5
    assert [c.details["llm_stage"] for c in costs].count("critic")==2
    assert [c.details["llm_stage"] for c in costs].count("revision")==1

@pytest.mark.asyncio
async def test_invalid_creative_evidence_keeps_call_costs_and_critic_audit(db, tmp_path):
    from app.models.domain import CostRecord
    company = Product(name="Flower studio", slug="creative-invalid-evidence")
    db.add(company)
    db.commit()
    request = CreativeRequest(mode="creative", product_id=company.id, format="story",
                              platform="telegram", topic="Choose flowers")
    task = queue_creative(db, request)
    invalid = strategy_payload()
    invalid["evidence_ids"] = ["nonexistent-source"]

    class Provider:
        def __init__(self):
            self.calls = []
        async def generate(self, task_request):
            self.calls.append(task_request)
            value = {"findings": []} if task_request.metadata.get("task_type") == "quality_review" else invalid
            return LLMResponse(content=json.dumps(value), model="fake",
                               usage=LLMUsage(input_tokens=11, output_tokens=12))

    provider = Provider()
    assert not await run_creative_once(
        db, Settings(_env_file=None, products_root=tmp_path, agent_critic_enabled=True),
        request, task_id=task.id, provider=provider)
    assert len(provider.calls) == 2
    assert db.get(Task, task.id).status == TaskStatus.FAILED
    costs = list(db.scalars(select(CostRecord).where(CostRecord.product_id == company.id)))
    assert len(costs) == 2
    assert [x.details["llm_stage"] for x in costs] == ["author", "critic"]
    reviews = list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "agent_quality_review")))
    assert len(reviews) == 1
    assert json.loads(reviews[0].content)["status"] == "no_material_issues"
