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




def test_story_requested_count_is_validated_without_changing_other_formats():
    from uuid import uuid4
    product_id = uuid4()
    request = CreativeRequest(mode="creative", product_id=product_id,
                              topic="Пять советов при выборе цветов",
                              format="story", platform="telegram", frame_count=5)
    assert request.frame_count == 5
    with pytest.raises(ValidationError):
        CreativeRequest(mode="creative", product_id=product_id,
                        topic="Пятнадцать историй", format="story",
                        platform="telegram", frame_count=15)
    with pytest.raises(ValidationError):
        CreativeRequest(mode="creative", product_id=product_id,
                        topic="Рисованное видео", format="short_video",
                        platform="youtube_shorts", frame_count=5)


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
    assert {r["status"] for r in reviews}=={"no_material_issues","revision_pending","rejected_domain_revision"}
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
    # Author, critic, then exactly one repair attempt that is still invalid.
    assert len(provider.calls) == 3
    assert "rejected by validation" in provider.calls[2].system_prompt
    assert db.get(Task, task.id).status == TaskStatus.FAILED
    costs = list(db.scalars(select(CostRecord).where(CostRecord.product_id == company.id)))
    assert len(costs) == 3
    assert [x.details["llm_stage"] for x in costs] == ["author", "critic", "author"]
    reviews = list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "agent_quality_review")))
    assert len(reviews) == 1
    assert json.loads(reviews[0].content)["status"] == "no_material_issues"


@pytest.mark.asyncio
async def test_rejected_creative_stage_is_repaired_once(db, tmp_path):
    company = Product(name="Flower studio", slug="creative-repair")
    db.add(company)
    db.commit()
    request = CreativeRequest(mode="creative", product_id=company.id, format="static_image",
                              platform="telegram", topic="Choose flowers")
    task = queue_creative(db, request)
    invalid = {**strategy_payload(), "evidence_ids": ["nonexistent-source"]}
    answers = [invalid, strategy_payload(), creative_payload("static_image", "telegram")]

    class Provider:
        def __init__(self):
            self.calls = []
        async def generate(self, task_request):
            self.calls.append(task_request)
            return LLMResponse(content=json.dumps(answers[len(self.calls) - 1], ensure_ascii=False),
                               model="fake", usage=LLMUsage(input_tokens=10, output_tokens=20))

    provider = Provider()
    assert await run_creative_once(db, Settings(_env_file=None, products_root=tmp_path), request,
                                   task_id=task.id, provider=provider)
    assert len(provider.calls) == 3
    assert "rejected by validation" in provider.calls[1].system_prompt
    assert db.get(Task, task.id).status == TaskStatus.COMPLETED


def test_story_and_image_prompts_are_value_first():
    from app.prompts import text as prompt_text
    director = prompt_text("creative.director")
    assert "польза из core_takeaway" in director and "STORY:" in director and "STATIC_IMAGE:" in director
    assert "0-30 seconds" in director  # short video rules left as they were
    assert "core_takeaway" in prompt_text("creative.strategist")
    assert "один конкретный шаг" in prompt_text("article.rules.story_director")
    assert "обещает пользу" in prompt_text("article.rules.visual_designer")


def test_creative_json_parser_accepts_safe_wrappers_but_not_truncated_prose():
    from app.content.creative import parse_creative_json
    value = strategy_payload()
    plain = json.dumps(value, ensure_ascii=False)
    fence = chr(96) * 3
    assert parse_creative_json(plain) == value
    assert parse_creative_json(fence + "json\n" + plain + "\n" + fence) == value
    assert parse_creative_json(fence + "\n" + plain + "\n" + fence) == value
    assert parse_creative_json(json.dumps(plain, ensure_ascii=False)) == value
    with pytest.raises(json.JSONDecodeError):
        parse_creative_json(plain[:-5])
    with pytest.raises(json.JSONDecodeError):
        parse_creative_json("Пояснение\n" + plain)
    with pytest.raises(ValueError, match="JSON object"):
        parse_creative_json("[1, 2]")
    with pytest.raises(ValueError, match="too large"):
        parse_creative_json(" " * 65001)


@pytest.mark.asyncio
async def test_failed_strategy_records_exact_stage_and_retry_only_creates_real_runs(db, tmp_path):
    from app.models.domain import AgentRun
    product = Product(name="Ремонт", slug="creative-strategy-malformed-json")
    db.add(product)
    db.commit()
    request = CreativeRequest(mode="creative", product_id=product.id,
                              topic="Три совета по уходу", format="story",
                              platform="instagram_reels", frame_count=3)
    task = queue_creative(db, request)
    answers = ["{invalid", "{invalid", strategy_payload(),
               creative_payload("story", "instagram_reels")]

    class Provider:
        def __init__(self):
            self.calls = []
        async def generate(self, call):
            self.calls.append(call)
            result = answers.pop(0)
            return LLMResponse(
                content=result if isinstance(result, str) else json.dumps(result, ensure_ascii=False),
                model="fake", usage=LLMUsage(input_tokens=10, output_tokens=20))

    provider = Provider()
    cfg = Settings(_env_file=None, products_root=tmp_path, agent_critic_enabled=False)
    assert not await run_creative_once(db, cfg, request, task_id=task.id, provider=provider)
    assert db.get(Task, task.id).status == TaskStatus.FAILED
    runs = list(db.scalars(select(AgentRun).where(AgentRun.task_id == task.id)))
    assert len(runs) == 1 and runs[0].agent_name == "creative_strategist"
    details = list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "creative_stage_error")))
    assert len(details) == 1
    metadata = json.loads(details[0].content)
    assert metadata["stage"] == "creative_strategist"
    assert metadata["exception_type"] == "JSONDecodeError"
    assert "json_position" in metadata and "output_length" in metadata
    assert "invalid" not in details[0].content

    task.status = TaskStatus.QUEUED
    db.commit()
    assert await run_creative_once(db, cfg, request, task_id=task.id, provider=provider)
    assert db.get(Task, task.id).status == TaskStatus.COMPLETED
    assert len(provider.calls) == 4
    assert len(list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "creative_brief")))) == 1


@pytest.mark.asyncio
async def test_director_failure_resume_reuses_completed_strategy_without_another_paid_call(db, tmp_path):
    product = Product(name="Ремонт", slug="creative-director-malformed-json")
    db.add(product)
    db.commit()
    request = CreativeRequest(mode="creative", product_id=product.id,
                              topic="Три совета по уходу", format="story",
                              platform="instagram_reels", frame_count=3)
    task = queue_creative(db, request)
    answers = [strategy_payload(), "bad JSON", "bad JSON",
               creative_payload("story", "instagram_reels")]

    class Provider:
        def __init__(self):
            self.calls = []
        async def generate(self, call):
            self.calls.append(call)
            result = answers.pop(0)
            return LLMResponse(
                content=result if isinstance(result, str) else json.dumps(result, ensure_ascii=False),
                model="fake", usage=LLMUsage(input_tokens=10, output_tokens=20))

    provider = Provider()
    cfg = Settings(_env_file=None, products_root=tmp_path, agent_critic_enabled=False)
    assert not await run_creative_once(db, cfg, request, task_id=task.id, provider=provider)
    checkpoints = list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "content_stage_output")))
    assert len(checkpoints) == 1 and checkpoints[0].title == "creative_strategist: draft"
    task.status = TaskStatus.QUEUED
    db.commit()
    assert await run_creative_once(db, cfg, request, task_id=task.id, provider=provider)
    assert len(provider.calls) == 4
    assert [call.metadata["team_role"] for call in provider.calls] == [
        "creative_strategist", "creative_director", "creative_director", "creative_director",
    ]
    assert db.get(Task, task.id).status == TaskStatus.COMPLETED
    assert len(list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "creative_brief")))) == 1


@pytest.mark.asyncio
async def test_fenced_creative_stages_do_not_trigger_extra_repair_calls(db, tmp_path):
    company = Product(name="Ремонт", slug="creative-fenced-json")
    db.add(company)
    db.commit()
    request = CreativeRequest(mode="creative", product_id=company.id,
                              topic="Три совета", format="story", platform="instagram_reels")
    task = queue_creative(db, request)

    class Provider:
        def __init__(self):
            self.calls = []
        async def generate(self, call):
            self.calls.append(call)
            obj = strategy_payload() if len(self.calls) == 1 else creative_payload("story", "instagram_reels")
            return LLMResponse(content=chr(96)*3 + "json\n" + json.dumps(obj, ensure_ascii=False)
                               + "\n" + chr(96)*3, model="fake",
                               usage=LLMUsage(input_tokens=10, output_tokens=20))

    provider = Provider()
    assert await run_creative_once(db, Settings(_env_file=None, products_root=tmp_path,
                                                agent_critic_enabled=False),
                                   request, task_id=task.id, provider=provider)
    assert len(provider.calls) == 2
    assert db.get(Task, task.id).status == TaskStatus.COMPLETED


def test_creative_json_parser_accepts_one_complete_envelope_after_prose():
    from app.content.creative import parse_creative_json
    valid = json.dumps(strategy_payload(), ensure_ascii=False)
    assert parse_creative_json("Готово. Ниже структура:\n" + valid + "\nПроверьте.") == strategy_payload()
    assert parse_creative_json("<think>План построен.</think>\n" + valid) == strategy_payload()
    assert parse_creative_json("Ответ модели:\n" + valid + "\n") == strategy_payload()
    with pytest.raises(json.JSONDecodeError):
        parse_creative_json("Вариант A:\n" + valid + "\nВариант B:\n" + valid)
    with pytest.raises(json.JSONDecodeError):
        parse_creative_json("JSON: " + valid[:-7])
    with pytest.raises(json.JSONDecodeError):
        parse_creative_json("К сожалению, JSON подготовить не удалось.")
    with pytest.raises(json.JSONDecodeError):
        parse_creative_json("План: {только пример} и ответ: {ещё пример}")


@pytest.mark.asyncio
async def test_story_uses_wrapped_but_valid_grok_response_without_repeat(db, tmp_path):
    from app.content.creative import parse_creative_json
    product = Product(name="Цветочная мастерская", slug="creative-grok-envelope")
    db.add(product)
    db.commit()
    task_request = CreativeRequest(mode="creative", product_id=product.id,
                                   topic="Три совета", format="story",
                                   platform="instagram_reels", frame_count=3)
    task = queue_creative(db, task_request)

    class Provider:
        def __init__(self):
            self.calls = []
        async def generate(self, request):
            self.calls.append(request)
            output = strategy_payload() if len(self.calls) == 1 else creative_payload("story", "instagram_reels")
            raw = "Ответ JSON:\n" + json.dumps(output, ensure_ascii=False) + "\nГотово."
            return LLMResponse(content=raw, model="grok-4.7",
                               usage=LLMUsage(input_tokens=10, output_tokens=40),
                               stop_reason="end_turn")

    provider = Provider()
    assert await run_creative_once(
        db, Settings(_env_file=None, products_root=tmp_path, agent_critic_enabled=False),
        task_request, task_id=task.id, provider=provider)
    assert len(provider.calls) == 2
    assert db.get(Task, task.id).status == TaskStatus.COMPLETED
    saved = list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "creative_stage_error")))
    assert saved == []
