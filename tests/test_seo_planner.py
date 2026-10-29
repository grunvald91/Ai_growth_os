"""SEO content plan: every article must rest on real collected queries; seeds come from the company profile."""
import json
from datetime import datetime, timezone

import pytest

from app.llm.schemas import LLMResponse, LLMUsage
from app.llm.service import LLMService
from app.models.domain import Product, Task, TaskStatus
from app.privacy import PrivacyGateway
from app.seo.contracts import QueryCluster, SemanticCore, SeoContentPlan, SourceStatus
from app.seo.planner import PlanError, plan_content, plan_seeds, validate_plan


class Provider:
    def __init__(self, *outputs):
        self.outputs, self.requests = list(outputs), []

    async def generate(self, request):
        self.requests.append(request)
        return LLMResponse(content=json.dumps(self.outputs.pop(0)), model="fake", usage=LLMUsage(), stop_reason="end_turn")


def core():
    return SemanticCore(
        seeds=["запись клиентов"], collected_at=datetime.now(timezone.utc),
        sources=[SourceStatus(engine="yandex", requests=1, failed_requests=0, queries_found=3)],
        clusters=[
            QueryCluster(id="c001", head_query="как вести запись клиентов", intent="informational", stage="awareness",
                         queries=["как вести запись клиентов", "как вести запись клиентов в салоне"], engines=["yandex"]),
            QueryCluster(id="c002", head_query="программа для записи клиентов цена", intent="commercial",
                         stage="consideration", queries=["программа для записи клиентов цена"], engines=["yandex"]),
        ])


def item(**over):
    base = dict(id="a001", cluster_ids=["c001"], primary_query="как вести запись клиентов",
                working_title="Как вести запись клиентов и не терять их", stage="awareness", intent="informational",
                reader_problem="Мастер теряет клиентов из-за записи в мессенджерах.",
                ai_questions=["как лучше вести запись клиентов мастеру"], next_step="Показать онлайн-запись ServGo")
    base.update(over)
    return base


def plan(*items):
    return SeoContentPlan.model_validate({"items": list(items)})


def test_valid_plan_passes_even_with_different_case_and_punctuation():
    validate_plan(plan(item(primary_query="Как вести запись клиентов?")), core())


@pytest.mark.parametrize("over,message", [
    ({"cluster_ids": ["c999"]}, "Unknown cluster"),
    ({"primary_query": "как открыть салон красоты"}, "not a collected query"),
    ({"stage": "decision"}, "differs from cluster"),
])
def test_invented_or_mismatched_items_are_rejected(over, message):
    with pytest.raises(PlanError, match=message):
        validate_plan(plan(item(**over)), core())


def test_repeated_primary_query_rejected():
    with pytest.raises(PlanError, match="repeated"):
        validate_plan(plan(item(), item(id="a002")), core())


@pytest.fixture
def product(db, tmp_path):
    row = Product(name="ServGo", slug="servgo")
    db.add(row)
    db.commit()
    products = tmp_path / "products" / "servgo"
    products.mkdir(parents=True)
    (products / ".claude").mkdir()
    (products / ".claude" / "product-marketing-context.md").write_text("ServGo: онлайн-запись клиентов для малого бизнеса.", encoding="utf-8")
    return row


def service(provider):
    return LLMService(privacy_gateway=PrivacyGateway(), provider=provider)


@pytest.mark.asyncio
async def test_plan_saved_as_artifact_with_completed_task(db, product, tmp_path):
    provider = Provider({"items": [item()]})
    artifact = await plan_content(db, service(provider), PrivacyGateway(), product.id, core(),
                                  products_root=tmp_path / "products", model="fake")
    assert artifact.kind == "seo_content_plan" and artifact.task_id
    assert db.get(Task, artifact.task_id).status == TaskStatus.COMPLETED
    sent = json.loads(provider.requests[0].messages[0].content)
    assert [c["id"] for c in sent["clusters"]] == ["c001", "c002"]  # most evidenced cluster first


@pytest.mark.asyncio
async def test_invented_plan_fails_task_and_saves_nothing(db, product, tmp_path):
    provider = Provider({"items": [item(primary_query="выдуманный запрос")]})
    with pytest.raises(PlanError):
        second = await plan_content(db, service(provider), PrivacyGateway(), product.id, core(),
                           products_root=tmp_path / "products", model="fake")
    task = db.query(Task).one()
    assert task.status == TaskStatus.FAILED
    assert not [a for a in db.query(__import__("app.models.domain", fromlist=["Artifact"]).Artifact)
                if a.kind == "seo_content_plan"]


@pytest.mark.asyncio
async def test_seeds_use_company_profile(db, product, tmp_path):
    provider = Provider({"seeds": ["онлайн запись клиентов", "программа для записи к мастеру", "crm для салона"]})
    seeds = await plan_seeds(db, service(provider), PrivacyGateway(), product.id,
                             products_root=tmp_path / "products", model="fake")
    assert seeds[0] == "онлайн запись клиентов"
    assert "онлайн-запись клиентов" in provider.requests[0].messages[0].content


def c002_item():
    return item(id="a002", cluster_ids=["c002"], primary_query="программа для записи клиентов цена",
                stage="consideration", intent="commercial")


@pytest.mark.asyncio
async def test_next_plan_skips_topics_already_written(db, product, tmp_path):
    from app.seo.queue import SEO_TASK_PREFIX
    first = await plan_content(db, service(Provider({"items": [item()]})), PrivacyGateway(), product.id, core(),
                               products_root=tmp_path / "products", model="fake")
    from app.seo.queue import queue_plan
    queue_plan(db, first, {c.id: c for c in core().clusters})
    provider = Provider({"items": [c002_item()]})
    second = await plan_content(db, service(provider), PrivacyGateway(), product.id, core(),
                       products_root=tmp_path / "products", model="fake")
    queue_plan(db, second, {c.id: c for c in core().clusters})
    sent = json.loads(provider.requests[0].messages[0].content)
    assert [c["id"] for c in sent["clusters"]] == ["c002"]  # c001 already has its article
    # a failed article frees its query for a later plan
    from sqlalchemy import select
    db.scalar(select(Task).where(Task.title == f"{SEO_TASK_PREFIX}{first.id}:a001")).status = TaskStatus.FAILED
    db.commit()
    from app.seo.planner import planned_queries
    assert planned_queries(db, product.id) == {"программа для записи клиентов цена"}


@pytest.mark.asyncio
async def test_plan_stops_when_everything_is_covered(db, product, tmp_path):
    saved = await plan_content(db, service(Provider({"items": [item(), c002_item()]})), PrivacyGateway(), product.id, core(),
                       products_root=tmp_path / "products", model="fake")
    from app.seo.queue import queue_plan
    queue_plan(db, saved, {c.id: c for c in core().clusters})
    with pytest.raises(PlanError, match="already covered"):
        await plan_content(db, service(Provider()), PrivacyGateway(), product.id, core(),
                           products_root=tmp_path / "products", model="fake")



@pytest.mark.asyncio
async def test_plan_without_article_task_does_not_reserve_query(db, product, tmp_path):
    from app.seo.planner import planned_queries
    await plan_content(db, service(Provider({"items": [item()]})), PrivacyGateway(), product.id, core(),
                       products_root=tmp_path / "products", model="fake")
    assert planned_queries(db, product.id) == set()
