"""The SEO factory job: seeds -> real suggestions -> plan -> queued article tasks; and the dispatcher entry."""
import json

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.models import Base
from app.dispatcher import executors
from app.dispatcher.outcome import Confirmation, Context
from app.dispatcher.policy import Verdict, verdict
from app.dispatcher.registry import CAPABILITIES
from app.llm.schemas import LLMResponse, LLMUsage
from app.models.domain import Artifact, Product, Task, TaskStatus
from app.seo.factory import queue_factory, run_factory_once
from app.seo.activation import set_enabled
from app.workflows.article_contracts import ArticleProductionInput


class Provider:
    """Answers seeds first, then builds a plan from whatever real clusters it was given."""

    def __init__(self):
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        role = request.metadata["team_role"]
        payload = json.loads(request.messages[0].content)
        if role == "semantic_researcher":
            body = {"seeds": ["запись клиентов", "программа для записи", "crm для салона"]}
        else:
            first = payload["clusters"][0]
            body = {"items": [dict(
                id="a001", cluster_ids=[first["id"]], primary_query=first["queries"][0],
                working_title="Как вести запись клиентов и не терять их", stage=first["stage"], intent=first["intent"],
                reader_problem="Мастер теряет клиентов из-за записи в мессенджерах.",
                ai_questions=["как лучше вести запись клиентов"], next_step="Показать онлайн-запись")]}
        return LLMResponse(content=json.dumps(body), model="fake", usage=LLMUsage(), stop_reason="end_turn")


def suggest_client(fail=False):
    def handler(request):
        if fail:
            return httpx.Response(500)
        part = request.url.params.get("part") or request.url.params.get("q")
        return httpx.Response(200, text=json.dumps([part, [f"{part} цена", f"{part} отзывы"]]))
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def env(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    root = tmp_path / "products" / "servgo" / ".claude"
    root.mkdir(parents=True)
    (root / "product-marketing-context.md").write_text("ServGo: онлайн-запись клиентов для малого бизнеса.", encoding="utf-8")

    def factory():
        return Session(engine, expire_on_commit=False)

    with factory() as db:
        product = Product(name="ServGo", slug="servgo")
        db.add(product)
        db.commit()
        product_id = product.id
        set_enabled(db, product_id, enabled=True, actor='fixture-owner')
    yield factory, Settings(products_root=tmp_path / "products"), product_id
    engine.dispose()


def test_asking_twice_returns_the_same_active_run(env):
    factory, _, product_id = env
    with factory() as db:
        first = queue_factory(db, product_id, "салоны красоты")
        assert queue_factory(db, product_id).id == first.id
        assert len(list(db.scalars(select(Task).where(Task.assigned_agent == "seo_factory")))) == 1


@pytest.mark.asyncio
async def test_factory_turns_company_profile_into_queued_search_articles(env):
    factory, settings, product_id = env
    with factory() as db:
        queue_factory(db, product_id, "салоны красоты")
    provider = Provider()
    assert await run_factory_once(factory, settings, provider=provider, client=suggest_client()) == 1
    with factory() as db:
        factory_task = db.scalar(select(Task).where(Task.assigned_agent == "seo_factory"))
        assert factory_task.status == TaskStatus.COMPLETED
        kinds = {a.kind for a in db.scalars(select(Artifact))}
        assert {"semantic_core", "seo_content_plan", "seo_factory_report", "content_request"} <= kinds
        article_task = db.scalar(select(Task).where(Task.assigned_agent == "content_factory"))
        assert article_task.status == TaskStatus.QUEUED
        request = ArticleProductionInput.model_validate_json(
            db.scalar(select(Artifact).where(Artifact.task_id == article_task.id, Artifact.kind == "content_request")).content)
        assert request.channel == "own_site" and request.seo_brief.plan_item_id == "a001"
        assert request.seo_brief.related_queries and not request.produce_ready_assets and not request.request_publication
    seed_request = json.loads(provider.requests[0].messages[0].content)
    assert seed_request["owner_focus"] == "салоны красоты"
    assert await run_factory_once(factory, settings, provider=provider, client=suggest_client()) == 0


@pytest.mark.asyncio
async def test_one_factory_launch_queues_full_plan_without_three_article_cap(env):

    class SeveralTopics(Provider):
        async def generate(self, request):
            if request.metadata["team_role"] == "semantic_researcher":
                return await super().generate(request)
            self.requests.append(request)
            clusters = json.loads(request.messages[0].content)["clusters"][:5]
            assert len(clusters) >= 4
            items = [
                dict(id=f"a{index:03d}", cluster_ids=[cluster["id"]],
                     primary_query=cluster["queries"][0],
                     working_title=f"Полезная статья по вопросу номер {index}",
                     stage=cluster["stage"], intent=cluster["intent"],
                     reader_problem="Выбрать нужную услугу и понять процесс",
                     ai_questions=["Что важно знать перед записью?"],
                     next_step="Посмотреть каталог услуг компании")
                for index, cluster in enumerate(clusters, 1)
            ]
            return LLMResponse(content=json.dumps({"items": items}, ensure_ascii=False),
                               model="fake", usage=LLMUsage(), stop_reason="end_turn")

    factory, settings, product_id = env
    with factory() as db:
        queue_factory(db, product_id)
    assert await run_factory_once(factory, settings, provider=SeveralTopics(), client=suggest_client()) == 1
    with factory() as db:
        jobs = list(db.scalars(select(Task).where(
            Task.product_id == product_id, Task.title.like("SEO-ARTICLE:%"))))
        assert len(jobs) >= 4
        from collections import Counter
        from app.seo.schedule import day
        assert all(job.due_at is not None for job in jobs)
        assert max(Counter(day(job.due_at) for job in jobs).values()) <= 3
        report = db.scalar(select(Artifact).where(Artifact.kind == "seo_factory_report"))
        summary = json.loads(report.content)
        assert summary["articles_queued"] == len(jobs)
        assert summary["articles_deferred"] == 0
        assert summary["planning_horizon_days"] == 7


@pytest.mark.asyncio
async def test_search_engines_down_stops_honestly_without_article_tasks(env):
    factory, settings, product_id = env
    with factory() as db:
        queue_factory(db, product_id)
    await run_factory_once(factory, settings, provider=Provider(), client=suggest_client(fail=True))
    with factory() as db:
        assert db.scalar(select(Task).where(Task.assigned_agent == "seo_factory")).status == TaskStatus.FAILED
        report = db.scalar(select(Artifact).where(Artifact.kind == "seo_factory_report"))
        diagnostic = json.loads(report.content)
        assert diagnostic["error"] == "SeoSuggestionsUnavailable"
        assert diagnostic["error_code"] == "search_upstream_failures"
        assert diagnostic["stage"] == "collect_suggestions"
        assert diagnostic["retryable"] is False
        assert not list(db.scalars(select(Task).where(Task.assigned_agent == "content_factory")))


@pytest.mark.asyncio
async def test_dispatcher_always_asks_first_and_queues_only_after_confirmation(env):
    factory, settings, product_id = env
    capability = CAPABILITIES["seo_factory"]
    assert verdict(capability.operation, capability.cost) == Verdict.CONFIRM_FIRST
    with factory() as db:
        ctx = Context(db=db, product=db.get(Product, product_id), settings=settings, account="owner")
        card = await executors.DESCRIBERS["seo_factory"](ctx, capability.params(focus="парикмахерские", horizon_days=30))
        assert isinstance(card, Confirmation) and card.paid and "парикмахерские" in card.details[0]
        assert card.params["horizon_days"] == 30
        assert any("месяц" in line for line in card.details)
        assert not list(db.scalars(select(Task).where(Task.assigned_agent == "seo_factory")))
        outcome = await executors.CONFIRMERS["seo_factory"](ctx, card.params, "text")
        assert outcome.ok and outcome.task_id
        assert db.get(Task, outcome.task_id).assigned_agent == "seo_factory"
        monthly = db.scalar(select(Artifact).where(
            Artifact.task_id == outcome.task_id, Artifact.kind == "seo_factory_request"))
        assert json.loads(monthly.content)["horizon_days"] == 30



@pytest.mark.asyncio
async def test_long_specific_seed_recovers_from_real_short_source_suggestions(env):
    """Regression: ServGo got HTTP 200 + [] on long AI-generated searches."""
    class LongSeedProvider(Provider):
        async def generate(self, request):
            if request.metadata["team_role"] == "semantic_researcher":
                return LLMResponse(content=json.dumps({"seeds": [
                    "как записаться на маникюр онлайн без переписки",
                    "программа для автоматизации записи в салоне красоты",
                    "онлайн запись к косметологу или парикмахеру",
                ]}), model="fake", usage=LLMUsage(), stop_reason="end_turn")
            return await super().generate(request)

    def suggestions(request):
        prefix = request.url.params.get("part") or request.url.params.get("q")
        results = [prefix + " цена", prefix + " отзывы"] if (
            len(prefix.split()) <= 4 and "маникюр" in prefix
        ) else []
        return httpx.Response(200, text=json.dumps([prefix, results]))

    factory, settings, product_id = env
    with factory() as db:
        task_id = queue_factory(db, product_id, horizon_days=7).id
    async with httpx.AsyncClient(transport=httpx.MockTransport(suggestions)) as client:
        assert await run_factory_once(factory, settings, provider=LongSeedProvider(), client=client) == 1
    with factory() as db:
        assert db.get(Task, task_id).status == TaskStatus.COMPLETED
        for kind in ["semantic_core", "seo_content_plan", "seo_factory_stage"]:
            assert db.scalar(select(Artifact).where(Artifact.task_id == task_id, Artifact.kind == kind))
        checkpoint = db.scalar(select(Artifact).where(
            Artifact.task_id == task_id, Artifact.kind == "seo_suggest_checkpoint"))
        summary = json.loads(checkpoint.content)["results"]
        assert sum(v.get("empty", 0) for v in summary.values()) > 0
        assert sum(len(v["queries"]) for v in summary.values()) > 0
        assert list(db.scalars(select(Task).where(
            Task.product_id == product_id, Task.assigned_agent == "content_factory",
            Task.title.like("SEO-ARTICLE:%"))))



@pytest.mark.asyncio
async def test_invalid_ai_plan_gets_one_correction_then_queues_articles(env):
    """An invented cluster must be repaired by the model, never silently accepted."""
    class CorrectingPlan(Provider):
        def __init__(self):
            super().__init__()
            self.plan_attempts = 0

        async def generate(self, request):
            response = await super().generate(request)
            if request.metadata["team_role"] == "seo_editor":
                self.plan_attempts += 1
                if self.plan_attempts == 1:
                    data = json.loads(response.content)
                    data["items"][0]["cluster_ids"] = ["c99999"]
                    return LLMResponse(content=json.dumps(data), model="fake",
                                       usage=LLMUsage(), stop_reason="end_turn")
            return response

    factory, settings, product_id = env
    provider = CorrectingPlan()
    with factory() as db:
        task_id = queue_factory(db, product_id, horizon_days=7).id
    async with suggest_client() as client:
        assert await run_factory_once(factory, settings, provider=provider, client=client) == 1
    assert provider.plan_attempts == 2
    with factory() as db:
        assert db.get(Task, task_id).status == TaskStatus.COMPLETED
        assert db.scalar(select(Artifact).where(
            Artifact.task_id == task_id, Artifact.kind == "seo_content_plan"))
        assert list(db.scalars(select(Task).where(
            Task.product_id == product_id, Task.title.like("SEO-ARTICLE:%"))))


@pytest.mark.asyncio
async def test_persisting_invalid_ai_plan_reports_specific_error_without_publishing(env):
    """After one bounded correction, retain the stage and safe integrity error."""
    class BrokenPlan(Provider):
        async def generate(self, request):
            response = await super().generate(request)
            if request.metadata["team_role"] != "seo_editor":
                return response
            data = json.loads(response.content)
            data["items"][0]["cluster_ids"] = ["c99999"]
            return LLMResponse(content=json.dumps(data), model="fake",
                               usage=LLMUsage(), stop_reason="end_turn")

    factory, settings, product_id = env
    with factory() as db:
        task_id = queue_factory(db, product_id, horizon_days=7).id
    async with suggest_client() as client:
        assert await run_factory_once(factory, settings, provider=BrokenPlan(), client=client) == 1
    with factory() as db:
        assert db.get(Task, task_id).status == TaskStatus.FAILED
        report = db.scalar(select(Artifact).where(
            Artifact.task_id == task_id, Artifact.kind == "seo_factory_report"))
        error = json.loads(report.content)
        assert error["error"] == "PlanError"
        assert error["error_code"] == "plan_validation_failed"
        assert error["plan_issue"] == "unknown_cluster"
        assert error["stage"] == "build_plan"
        assert not list(db.scalars(select(Task).where(Task.title.like("SEO-ARTICLE:%"))))


def test_factory_stage_summary_is_company_scoped(env):
    from app.api.seo_run_view import seo_runs
    factory, _, product_id = env
    with factory() as db:
        task = queue_factory(db, product_id)
        ArtifactService(db).create(product_id=product_id, task_id=task.id,
            kind="seo_factory_stage", title="stage", content=json.dumps({"stage": "collect_suggestions"}))
        ArtifactService(db).create(product_id=product_id, task_id=task.id,
            kind="seo_factory_report", title="error", content=json.dumps({
                "error": "ProviderEmptyResponseError", "error_code": "model_unusable_response",
                "stage": "prepare_queries"}))
        db.commit()
        row = seo_runs(db, product_id)[0]
        assert row["current_stage"] == "collect_suggestions"
        assert row["last_error_code"] == "model_unusable_response"
        assert row["last_error_stage"] == "prepare_queries"

# ---- daily limit of blog articles and light mode ----------------------------------------------------

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.content import worker
from app.models.domain import AgentRun
from app.seo.queue import articles_started_today, quota_exhausted, SEO_TASK_PREFIX
from app.services.artifacts import ArtifactService
from app.services.tasks import TaskService


def seo_task(db, product_id, key, status, updated=None):
    task = TaskService(db).create(product_id=product_id, title=f"{SEO_TASK_PREFIX}{key}", assigned_agent="content_factory",
                                  status=status, priority=100)
    ArtifactService(db).create(product_id=product_id, task_id=task.id, kind="content_request", title="r", content="{}")
    if status != TaskStatus.QUEUED:
        from app.seo.queue import record_article_start
        record_article_start(db, task)
        started = db.scalar(select(Artifact).where(Artifact.task_id == task.id, Artifact.kind == "seo_article_started"))
        if updated:
            started.created_at = updated
    if updated:
        task.updated_at = updated
    db.commit()
    return task


def test_only_todays_moscow_day_counts_and_failed_attempts_count(env):
    factory, _, product_id = env
    now = datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc)  # 23:00 in Moscow; the Moscow day began at 21:00 UTC on the 27th
    with factory() as db:
        seo_task(db, product_id, "a", TaskStatus.COMPLETED, now - timedelta(hours=1))
        seo_task(db, product_id, "b", TaskStatus.FAILED, now - timedelta(hours=2))
        seo_task(db, product_id, "old", TaskStatus.COMPLETED, now - timedelta(hours=24))
        seo_task(db, product_id, "waiting", TaskStatus.QUEUED, now)
        assert articles_started_today(db, now) == 2
        assert not quota_exhausted(db, 3, now) and quota_exhausted(db, 2, now)


@pytest.mark.asyncio
async def test_worker_leaves_blog_articles_for_tomorrow_after_the_daily_limit(env, monkeypatch):
    factory, settings, product_id = env
    settings = settings.model_copy(update={"seo_articles_per_day": 3})
    ran = []

    class Fake:
        async def run(self, data):
            ran.append(data.seo_brief.plan_item_id if data.seo_brief else None)
            return SimpleNamespace(status="failed", artifact_ids=[])

    monkeypatch.setattr(worker, "build_article_production", lambda db, settings, **kwargs: Fake())
    brief = dict(plan_item_id="a009", primary_query="запись клиентов", stage="awareness", intent="informational",
                 reader_problem="Мастер теряет клиентов.", ai_questions=["как вести запись"], next_step="Онлайн-запись")
    with factory() as db:
        for key in "abc":
            seo_task(db, product_id, key, TaskStatus.COMPLETED, datetime.now(timezone.utc))
        waiting = seo_task(db, product_id, "d", TaskStatus.QUEUED)
        db.scalar(select(Artifact).where(Artifact.task_id == waiting.id)).content = ArticleProductionInput(
            product_id=product_id, topic="Как вести запись", channel="own_site", light=True, seo_brief=brief).model_dump_json()
        db.commit()
        waiting_id = waiting.id
    assert await worker.run_content_once(factory, settings) == 0 and ran == []
    with factory() as db:
        assert db.get(Task, waiting_id).status == TaskStatus.QUEUED
    settings = settings.model_copy(update={"seo_articles_per_day": 4})
    assert await worker.run_content_once(factory, settings) == 1 and ran == ["a009"]


@pytest.mark.asyncio
async def test_daily_limit_is_per_company(env, monkeypatch):
    factory, settings, product_id = env
    settings = settings.model_copy(update={"seo_articles_per_day": 3})
    ran = []

    class Fake:
        async def run(self, data):
            ran.append(str(data.product_id))
            return SimpleNamespace(status="failed", artifact_ids=[])

    monkeypatch.setattr(worker, "build_article_production", lambda db, settings, **kwargs: Fake())
    brief = dict(plan_item_id="a010", primary_query="ремонт обуви", stage="awareness", intent="informational",
                 reader_problem="Клиент не знает, где починить обувь.", ai_questions=["где починить обувь"],
                 next_step="Записаться в мастерскую")
    with factory() as db:
        other = Product(name="Мастерская", slug="shoes")
        db.add(other)
        db.commit()
        set_enabled(db, other.id, enabled=True, actor="fixture-owner")
        for key in "abc":
            seo_task(db, product_id, key, TaskStatus.COMPLETED, datetime.now(timezone.utc))
        waiting = seo_task(db, other.id, "x", TaskStatus.QUEUED)
        db.scalar(select(Artifact).where(Artifact.task_id == waiting.id)).content = ArticleProductionInput(
            product_id=other.id, topic="Где починить обувь", channel="own_site", light=True, seo_brief=brief).model_dump_json()
        db.commit()
        other_id = str(other.id)
        assert quota_exhausted(db, 3, product_id=product_id) and not quota_exhausted(db, 3, product_id=other.id)
    assert await worker.run_content_once(factory, settings) == 1 and ran == [other_id]


def test_light_mode_is_only_for_search_articles(env):
    _, _, product_id = env
    with pytest.raises(ValueError, match="Light mode"):
        ArticleProductionInput(product_id=product_id, topic="Любая тема", light=True)



def test_stale_factory_recovery_is_bounded(env):
    from app.seo.factory import recover_stale_factory, ATTEMPT_KIND
    factory, _, product_id = env
    with factory() as db:
        task = queue_factory(db, product_id)
        for count in range(1, 4):
            ArtifactService(db).create(product_id=product_id, task_id=task.id,
                kind=ATTEMPT_KIND, title="attempt", content="{}")
            task.status = TaskStatus.RUNNING
            task.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
            db.commit()
            recover_stale_factory(db)
            assert task.status == (TaskStatus.QUEUED if count < 3 else TaskStatus.FAILED)


def test_completion_does_not_move_daily_quota(env):
    factory, _, product_id = env
    now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    with factory() as db:
        task = seo_task(db, product_id, "yesterday", TaskStatus.RUNNING, now - timedelta(days=1))
        task.status = TaskStatus.COMPLETED
        task.updated_at = now
        db.commit()
        assert articles_started_today(db, now) == 0


@pytest.mark.asyncio
async def test_factory_resumes_committed_plan_without_new_llm_calls(env, monkeypatch):
    import app.seo.factory as module
    factory, settings, product_id = env
    original = module.queue_plan
    with factory() as db:
        task_id = queue_factory(db, product_id).id
    def interrupted(db, plan, clusters, **kwargs):
        original(db, plan, clusters, **kwargs)
        raise RuntimeError("interrupted after queue commit")
    monkeypatch.setattr(module, "queue_plan", interrupted)
    await run_factory_once(factory, settings, provider=Provider(), client=suggest_client())
    with factory() as db:
        task = db.get(Task, task_id)
        task.status = TaskStatus.QUEUED
        db.commit()
    monkeypatch.setattr(module, "queue_plan", original)
    provider = Provider()
    await run_factory_once(factory, settings, provider=provider, client=suggest_client())
    assert provider.requests == []
    with factory() as db:
        assert db.get(Task, task_id).status == TaskStatus.COMPLETED
        assert len(list(db.scalars(select(Task).where(Task.title.like("SEO-ARTICLE:%"))))) == 1


def test_servgo_autoschedule_weekly_and_off_by_default(env):
    from datetime import datetime, timedelta, timezone
    from app.seo.autoschedule import enqueue_servgo_if_due

    factory, settings, product_id = env
    now = datetime.now(timezone.utc)
    enabled = settings.model_copy(update={"seo_servgo_autorun_enabled": True})
    with factory() as db:
        assert not enqueue_servgo_if_due(db, settings, now=now)
        assert enqueue_servgo_if_due(db, enabled, now=now)
        assert not enqueue_servgo_if_due(db, enabled, now=now)
        task = db.scalar(select(Task).where(Task.assigned_agent == "seo_factory"))
        task.status = TaskStatus.COMPLETED
        db.add(Task(product_id=product_id, title="SEO-ARTICLE:backlog",
                    assigned_agent="content_factory", status=TaskStatus.QUEUED))
        db.commit()
        assert not enqueue_servgo_if_due(db, enabled, now=now + timedelta(days=6))
        assert enqueue_servgo_if_due(db, enabled, now=now + timedelta(days=8))


def test_servgo_autoschedule_honors_owner_opt_out(env):
    from datetime import datetime, timezone
    from app.seo.autoschedule import enqueue_servgo_if_due
    from app.seo.activation import set_enabled

    factory, settings, product_id = env
    enabled = settings.model_copy(update={"seo_servgo_autorun_enabled": True})
    with factory() as db:
        set_enabled(db, product_id, enabled=False, actor="owner")
        assert not enqueue_servgo_if_due(db, enabled, now=datetime.now(timezone.utc))


def test_monthly_factory_request_preserves_horizon(env):
    factory, _, product_id = env
    with factory() as db:
        task = queue_factory(db, product_id, "CRM для салона", horizon_days=30)
        artifact = db.scalar(select(Artifact).where(
            Artifact.task_id == task.id, Artifact.kind == "seo_factory_request"))
        assert json.loads(artifact.content) == {
            "focus": "CRM для салона", "horizon_days": 30,
        }


def test_seo_article_forces_critic_without_enabling_global_reviews(env):
    from app.core.composition import build_article_production

    factory, settings, _product_id = env
    assert settings.agent_critic_enabled is False
    with factory() as db:
        general = build_article_production(db, settings, provider=Provider())
        seo = build_article_production(db, settings, provider=Provider(), force_critic=True)
        # SEO review should remain compulsory, but general article production
        # must not incur extra critic calls when the global flag is disabled.
        assert general.llm.extra_timeout_seconds == 0
        assert seo.llm.extra_timeout_seconds > 0


def test_opted_in_weekly_autorun_queues_one_per_company_without_cross_tenant_leaks(env):
    from datetime import datetime, timedelta, timezone
    from app.seo.autoschedule import enqueue_enabled_if_due
    from app.models.domain import Product, Artifact
    from sqlalchemy import select
    import json

    factory, defaults, servgo = env
    now = datetime.now(timezone.utc)
    enabled = defaults.model_copy(update={"seo_autorun_enabled": True})
    with factory() as db:
        second = Product(name="Ремонтная мастерская", slug="repair-shop")
        demo = Product(name="Demo salon", slug="demo-salon")
        db.add_all([second, demo])
        db.commit()
        set_enabled(db, second.id, enabled=True, actor="owner:second")
        # Explicit activation is required even with the global switch on.
        assert not enqueue_enabled_if_due(db, defaults, now=now)
        assert enqueue_enabled_if_due(db, enabled, now=now)
        assert enqueue_enabled_if_due(db, enabled, now=now)
        assert not enqueue_enabled_if_due(db, enabled, now=now)
        jobs = list(db.scalars(select(Task).where(Task.assigned_agent == "seo_factory")))
        assert {job.product_id for job in jobs} == {servgo, second.id}
        assert all(job.product_id != demo.id for job in jobs)
        for job in jobs:
            request = db.scalar(select(Artifact).where(
                Artifact.task_id == job.id, Artifact.kind == "seo_factory_request"))
            assert json.loads(request.content)["horizon_days"] == 7
            assert json.loads(request.content)["focus"] is None  # no ServGo prompt for other businesses
            job.status = TaskStatus.COMPLETED
        db.commit()
        assert not enqueue_enabled_if_due(db, enabled, now=now + timedelta(days=6))
        set_enabled(db, second.id, enabled=False, actor="owner:second")
        assert enqueue_enabled_if_due(db, enabled, now=now + timedelta(days=8))
        fresh = list(db.scalars(select(Task).where(Task.assigned_agent == "seo_factory")))
        assert sum(task.product_id == servgo for task in fresh) == 2
        assert sum(task.product_id == second.id for task in fresh) == 1


def test_global_weekly_autorun_interval_and_servgo_legacy_pilot_are_separate(env):
    from datetime import datetime, timedelta, timezone
    from app.seo.autoschedule import enqueue_enabled_if_due
    from sqlalchemy import select

    factory, defaults, servgo = env
    now = datetime.now(timezone.utc)
    pilot = defaults.model_copy(update={"seo_servgo_autorun_enabled": True})
    long_interval = defaults.model_copy(update={
        "seo_autorun_enabled": True, "seo_autorun_interval_days": 30})
    with factory() as db:
        assert enqueue_enabled_if_due(db, pilot, now=now)
        assert not enqueue_enabled_if_due(db, pilot, now=now)
        task = db.scalar(select(Task).where(Task.assigned_agent == "seo_factory"))
        task.status = TaskStatus.COMPLETED
        db.commit()
        assert not enqueue_enabled_if_due(db, long_interval, now=now + timedelta(days=8))
        assert enqueue_enabled_if_due(db, long_interval, now=now + timedelta(days=31))



def test_autorun_uses_individual_company_interval_horizon_and_keeps_demo_off(env):
    from app.seo.autoschedule import enqueue_enabled_if_due
    from app.seo.activation import set_editorial_preferences
    from app.models.domain import Product, Artifact
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    import json

    factory, defaults, servgo_id = env
    settings = defaults.model_copy(update={"seo_autorun_enabled": True})
    now = datetime.now(timezone.utc)
    with factory() as db:
        second = Product(name="Другой бизнес", slug="different-shop")
        demo = Product(name="Демо", slug="demo-tenant")
        db.add_all([second, demo])
        db.commit()
        set_enabled(db, second.id, enabled=True, actor="owner-second")
        set_editorial_preferences(db, servgo_id, timezone_name="Europe/Moscow",
            autorun_interval_days=14, autorun_horizon_days=30,
            publications_per_day=2, articles_per_day=1, actor="owner-servgo")
        set_editorial_preferences(db, second.id, timezone_name="Asia/Tokyo",
            autorun_interval_days=7, autorun_horizon_days=7,
            publications_per_day=4, articles_per_day=4, actor="owner-second")
        assert enqueue_enabled_if_due(db, settings, now=now)
        assert enqueue_enabled_if_due(db, settings, now=now)
        assert not enqueue_enabled_if_due(db, settings, now=now)
        jobs=list(db.scalars(select(Task).where(Task.assigned_agent == "seo_factory")))
        assert {j.product_id for j in jobs} == {servgo_id, second.id}
        horizons={}
        for job in jobs:
            request=db.scalar(select(Artifact).where(Artifact.task_id == job.id,
                        Artifact.kind == "seo_factory_request"))
            horizons[job.product_id] = json.loads(request.content)["horizon_days"]
            job.status = TaskStatus.COMPLETED
        db.commit()
        assert horizons[servgo_id] == 30
        assert horizons[second.id] == 7
        assert not enqueue_enabled_if_due(db, settings, now=now + timedelta(days=6))
        assert enqueue_enabled_if_due(db, settings, now=now + timedelta(days=8))
        assert not enqueue_enabled_if_due(db, settings, now=now + timedelta(days=8))
        assert enqueue_enabled_if_due(db, settings, now=now + timedelta(days=15))
        assert all(job.product_id != demo.id for job in db.scalars(
            select(Task).where(Task.assigned_agent == "seo_factory")))
