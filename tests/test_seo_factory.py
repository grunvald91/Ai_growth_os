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
async def test_search_engines_down_stops_honestly_without_article_tasks(env):
    factory, settings, product_id = env
    with factory() as db:
        queue_factory(db, product_id)
    await run_factory_once(factory, settings, provider=Provider(), client=suggest_client(fail=True))
    with factory() as db:
        assert db.scalar(select(Task).where(Task.assigned_agent == "seo_factory")).status == TaskStatus.FAILED
        report = db.scalar(select(Artifact).where(Artifact.kind == "seo_factory_report"))
        assert json.loads(report.content) == {"failed_stage": "semantic_core", "error": "RuntimeError"}
        assert not list(db.scalars(select(Task).where(Task.assigned_agent == "content_factory")))


@pytest.mark.asyncio
async def test_dispatcher_always_asks_first_and_queues_only_after_confirmation(env):
    factory, settings, product_id = env
    capability = CAPABILITIES["seo_factory"]
    assert verdict(capability.operation, capability.cost) == Verdict.CONFIRM_FIRST
    with factory() as db:
        ctx = Context(db=db, product=db.get(Product, product_id), settings=settings, account="owner")
        card = await executors.DESCRIBERS["seo_factory"](ctx, capability.params(focus="парикмахерские"))
        assert isinstance(card, Confirmation) and card.paid and "парикмахерские" in card.details[0]
        assert not list(db.scalars(select(Task).where(Task.assigned_agent == "seo_factory")))
        outcome = await executors.CONFIRMERS["seo_factory"](ctx, card.params, "text")
        assert outcome.ok and outcome.task_id
        assert db.get(Task, outcome.task_id).assigned_agent == "seo_factory"
