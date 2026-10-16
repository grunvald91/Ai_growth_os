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


@pytest.mark.asyncio
async def test_quick_post_with_enabled_critic_persists_findings_and_usage(db, tmp_path):
    """The second opinion is queryable even when the author declines an edit."""
    from app.models.domain import CostRecord
    company=Product(name="Цветочная мастерская",slug="critic-post-audit")
    db.add(company);db.commit()
    task=queue_content(db,company.id,"Букет в подарок",format="post",channel="telegram")
    artifact=db.scalar(select(Artifact).where(
        Artifact.task_id==task.id,Artifact.kind=="content_request"
    ))
    req=QuickPostRequest.model_validate_json(artifact.content).model_copy(
        update={"task_id":task.id}
    )
    original_provider=FakePostProvider()
    finding={"findings":[{
        "id":"extra1", "severity":"major",
        "problem":"The post could include a helpful preparation tip",
        "evidence_in_draft":"Current text lacks the preparation tip",
        "proposed_fix":"Add only the supported practical instruction",
        "link_to_original_goal":"Request asks for useful advice for a customer",
    }]}
    author={"decisions":[{
        "finding_id":"extra1","decision":"reject",
        "rationale":"The request does not actually require this change",
    }],"updated_output":{"ignored":"yes"}}

    class Provider:
        def __init__(self):
            self.calls=[]
            self.review_round=0
        async def generate(self, request):
            self.calls.append(request)
            if request.metadata.get("task_type")=="quality_review":
                self.review_round+=1
                data={"findings":[]} if self.review_round==1 else finding
                return LLMResponse(content=json.dumps(data),model="fake",
                                   usage=LLMUsage(input_tokens=70,output_tokens=25),
                                   stop_reason="end_turn")
            if request.metadata.get("task_type")=="apply_quality_feedback":
                return LLMResponse(content=json.dumps(author),model="fake",
                                   usage=LLMUsage(input_tokens=70,output_tokens=25),
                                   stop_reason="end_turn")
            return await original_provider.generate(request)

    provider=Provider()
    settings=Settings(_env_file=None,products_root=tmp_path,agent_critic_enabled=True)
    assert await run_quick_post(db,settings,req,provider=provider)
    assert len(provider.calls)==5
    review_rows=list(db.scalars(select(Artifact).where(
        Artifact.task_id==task.id,Artifact.kind=="agent_quality_review"
    )))
    checks=[json.loads(row.content) for row in review_rows]
    assert {check["status"] for check in checks}=={"no_material_issues","revision_pending","all_rejected"}
    rejected=next(check for check in checks if check["status"]=="all_rejected")
    assert rejected["findings"][0]["id"]=="extra1"
    assert rejected["decisions"][0]["decision"]=="reject"
    published=db.scalar(select(Artifact).where(
        Artifact.task_id==task.id,Artifact.kind=="channel_text"
    ))
    assert published is not None
    costs=list(db.scalars(select(CostRecord).where(CostRecord.product_id==company.id)))
    assert len(costs)==5
    assert [c.details["llm_stage"] for c in costs].count("critic")==2
    assert [c.details["llm_stage"] for c in costs].count("revision")==1

@pytest.mark.asyncio
async def test_invalid_evidence_still_persists_both_model_calls_and_review(db, tmp_path):
    from app.models.domain import CostRecord
    company = Product(name="Salon", slug="post-invalid-audit")
    db.add(company)
    db.commit()
    task = queue_content(db, company.id, "How to choose care", format="post", channel="vk")
    row = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "content_request"))
    bad_author = FakePostProvider(bad_evidence=True)

    class Provider:
        async def generate(self, request):
            if request.metadata.get("task_type") == "quality_review":
                return LLMResponse(content='{"findings":[]}', model="critic",
                                   usage=LLMUsage(input_tokens=6, output_tokens=8))
            return await bad_author.generate(request)

    success = await run_quick_post(
        db, Settings(_env_file=None, products_root=tmp_path, agent_critic_enabled=True),
        QuickPostRequest.model_validate_json(row.content).model_copy(update={"task_id": task.id}),
        provider=Provider())
    assert success is False
    assert db.get(Task, task.id).status == TaskStatus.FAILED
    costs = list(db.scalars(select(CostRecord).where(CostRecord.product_id == company.id)))
    # The rejected draft gets one repair attempt (recorded too), then the job fails.
    assert len(costs) == 3
    assert sorted((x.details["llm_stage"], x.input_tokens) for x in costs) == [
        ("author", 90), ("author", 90), ("critic", 6)]
    reviews = list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "agent_quality_review")))
    assert len(reviews) == 1
    assert json.loads(reviews[0].content)["status"] == "no_material_issues"
    assert not list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "channel_text")))


@pytest.mark.asyncio
async def test_cancelled_author_revision_keeps_critic_findings_in_database(db, tmp_path):
    import asyncio
    from app.models.domain import AgentRun, CostRecord

    company = Product(name="Studio", slug="post-cancelled-author-revision")
    db.add(company)
    db.commit()
    task = queue_content(db, company.id, "Gift selection", format="post", channel="vk")
    row = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "content_request"))
    request = QuickPostRequest.model_validate_json(row.content).model_copy(
        update={"task_id": task.id})
    initial = FakePostProvider()

    class Provider:
        def __init__(self):
            self.calls = []
        async def generate(self, llm_request):
            self.calls.append(llm_request)
            stage = llm_request.metadata.get("task_type")
            if stage == "apply_quality_feedback":
                raise asyncio.CancelledError()
            if stage == "quality_review":
                data = {"findings": [{
                    "id": "urgent", "severity": "major",
                    "problem": "A required customer preparation step is missing",
                    "evidence_in_draft": "Draft only asks for a product choice",
                    "proposed_fix": "Add a verified preparation step to the plan",
                    "link_to_original_goal": "The goal is actionable purchasing advice",
                }]}
                return LLMResponse(
                    content=json.dumps(data), model="critic",
                    usage=LLMUsage(input_tokens=6, output_tokens=8),
                    stop_reason="end_turn")
            return await initial.generate(llm_request)

    provider = Provider()
    with pytest.raises(asyncio.CancelledError):
        await run_quick_post(
            db, Settings(_env_file=None, products_root=tmp_path,
                         agent_critic_enabled=True),
            request, provider=provider,
        )
    assert len(provider.calls) == 3
    assert db.get(Task, task.id).status == TaskStatus.FAILED

    reviews = list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "agent_quality_review")))
    payloads = [json.loads(row.content) for row in reviews]
    assert {p["status"] for p in payloads} == {
        "revision_pending", "revision_cancelled",
    }
    assert all(p["findings"][0]["id"] == "urgent" for p in payloads)

    costs = list(db.scalars(select(CostRecord).where(
        CostRecord.product_id == company.id)))
    assert len(costs) == 2
    assert [c.details["llm_stage"] for c in costs] == ["author", "critic"]
    assert [c.input_tokens for c in costs] == [90, 6]
    runs = list(db.scalars(select(AgentRun).where(AgentRun.task_id == task.id)))
    assert all(run.status == "failed" for run in runs)
    references = [
        {"kind": "artifact", "id": str(row.id)} for row in reviews
    ]
    assert all(reference in runs[0].output_references for reference in references)
    assert not list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "channel_text")))


def test_post_cta_missing_from_body_is_appended_not_rejected():
    from app.content.quick_post import QuickPostDraft
    body = "Записи клиентов показывают, какие услуги берут чаще и когда пустуют окна. " * 3
    draft = QuickPostDraft.model_validate({"title": "Данные о записях", "body": body,
                                           "cta": "Запишитесь на демонстрацию"})
    assert draft.body.endswith("Запишитесь на демонстрацию")
    same = QuickPostDraft.model_validate({"title": "Данные о записях", "body": body + "Запишитесь  на демонстрацию.",
                                          "cta": "Запишитесь на демонстрацию"})
    assert same.body.count("Запишитесь") == 1


@pytest.mark.asyncio
async def test_rejected_post_draft_is_repaired_once_with_the_validation_problem(db, tmp_path):
    company = Product(name="Salon", slug="post-repair-once")
    db.add(company)
    db.commit()
    task = queue_content(db, company.id, "How to choose care", format="post", channel="vk")
    row = db.scalar(select(Artifact).where(Artifact.task_id == task.id, Artifact.kind == "content_request"))
    good = FakePostProvider()
    calls = []

    class Provider:
        async def generate(self, request):
            calls.append(request)
            if request.metadata.get("team_role") == "post_writer_editor" and \
                    sum(c.metadata.get("team_role") == "post_writer_editor" for c in calls) == 1:
                return LLMResponse(content='{"title": "x"}', model="author",
                                   usage=LLMUsage(input_tokens=5, output_tokens=5), stop_reason="end_turn")
            return await good.generate(request)

    success = await run_quick_post(
        db, Settings(_env_file=None, products_root=tmp_path),
        QuickPostRequest.model_validate_json(row.content).model_copy(update={"task_id": task.id}),
        provider=Provider())
    assert success is True
    retry = [c for c in calls if c.metadata.get("team_role") == "post_writer_editor"][-1]
    assert "rejected by validation" in retry.system_prompt and "body:missing" in retry.system_prompt
