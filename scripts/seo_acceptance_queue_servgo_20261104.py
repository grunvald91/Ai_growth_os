"""Controlled real-source ServGo SEO acceptance batch.

Queues exactly three B2B topics, only after real suggestions from both engines.
No fake conversion, no made-up search volume, no price, no manual bypass.
The existing production worker performs real model generation and critic review
under its ordinary per-company limits and schedule. Never invokes worker directly.
"""
import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import select
from app.company_profile import load_company_profile
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import Artifact, Product, Task
from app.seo.activation import is_enabled
from app.seo.clustering import classify_intent, normalize
from app.seo.contracts import QueryCluster, SemanticCore, SeoContentPlan, SourceStatus, PlanItem
from app.seo.planner import planned_queries, validate_plan
from app.seo.publishing import servgo_buyer_intent_problems  # required deployed release guard
from app.seo.queue import queue_plan
from app.seo.suggest import collect
from app.services.artifacts import ArtifactService

BATCH = "seo-acceptance-three-b2b-20261104"
TOPICS = (
    {
        "seed": "программа онлайн записи клиентов",
        "query": "программа онлайн записи клиентов",
        "title": "Программа онлайн-записи клиентов: что проверить владельцу бизнеса",
        "problem": "Владелец бизнеса в сфере услуг выбирает программу для расписания и клиентской базы, сравнивает ручную запись сотрудников и самостоятельную онлайн-запись.",
        "questions": ["Как выбрать программу онлайн-записи и расписания?", "Нужна ли самостоятельная запись, если клиентов записывает администратор?"],
        "next": "Запросить демонстрацию календаря и CRM ServGo",
    },
    {
        "seed": "как привлечь клиентов в салон красоты",
        "query": "как привлечь клиентов в салон красоты",
        "title": "Как заполнить свободные часы мастеров и привлечь клиентов в салон",
        "problem": "Владелец салона красоты хочет уменьшить пустые интервалы в расписании мастеров без выдуманных результатов и навязчивых рассылок.",
        "questions": ["Как салону найти и сократить свободные окна в расписании?", "Как возвращать клиентов корректно и с учётом согласия на маркетинг?"],
        "next": "Запросить демонстрацию расписания и клиентской базы ServGo",
    },
    {
        "seed": "ии для продвижения бизнеса",
        "query": "ии для продвижения бизнеса",
        "title": "ИИ для продвижения бизнеса: задачи, ограничения и критерии выбора",
        "problem": "Владелец небольшого сервисного бизнеса изучает ИИ для маркетинга, ищет проверяемые задачи и способы оценивать результат без необоснованных обещаний.",
        "questions": ["Какие маркетинговые задачи можно поручить ИИ?", "Как проверить качество контента и не приписывать ИИ недоступные функции?"],
        "next": "Обсудить доступные функции Growth и запросить демонстрацию ServGo",
    },
)
STAGE = {"informational": "awareness", "navigational": "awareness",
         "commercial": "consideration", "transactional": "decision"}

def emit(name, data):
    print("SEO_REAL_ACCEPTANCE " + name + "=" +
          json.dumps(data, default=str, ensure_ascii=False, sort_keys=True), flush=True)

async def verify_real_sources():
    sources = {"yandex": {"requests": 0, "failed": 0, "found": 0},
               "google": {"requests": 0, "failed": 0, "found": 0}}
    evidence = []
    for topic in TOPICS:
        result = await collect([topic["seed"]], max_requests=24,
                               total_timeout=55, concurrency=4)
        from_engines = []
        for engine, data in result.items():
            sources[engine]["requests"] += data.requests
            sources[engine]["failed"] += data.failed
            sources[engine]["found"] += len(data.queries)
            if any(normalize(q) == normalize(topic["query"]) for q in data.queries):
                from_engines.append(engine)
        if set(from_engines) != {"yandex", "google"}:
            emit("blocked_unverified_query", {"query": topic["query"], "engines": from_engines})
            raise RuntimeError("Real search evidence missing in one or both providers")
        evidence.append({"query": topic["query"], "engines": sorted(from_engines)})
    return sources, evidence

async def main():
    settings = get_settings()
    if settings.seo_autorun_enabled or settings.seo_servgo_autorun_enabled:
        raise RuntimeError("Global autorun must stay OFF for the acceptance batch")
    with SessionLocal() as db:
        company = db.scalar(select(Product).where(Product.slug == "servgo").limit(1))
        if company is None or not is_enabled(db, company.id):
            raise RuntimeError("ServGo company missing or SEO not opted in")
        prior = db.scalar(select(Artifact.id).where(
            Artifact.product_id == company.id,
            Artifact.kind == "seo_acceptance_batch", Artifact.title == BATCH).limit(1))
        if prior:
            emit("already_queued", {"batch": BATCH})
            return

    sources, evidence = await verify_real_sources()
    with SessionLocal() as db:
        company = db.scalar(select(Product).where(Product.slug == "servgo").with_for_update())
        if company is None or not is_enabled(db, company.id):
            raise RuntimeError("ServGo SEO disabled while collecting real sources")
        prior = db.scalar(select(Artifact.id).where(
            Artifact.product_id == company.id,
            Artifact.kind == "seo_acceptance_batch", Artifact.title == BATCH).limit(1))
        if prior:
            emit("already_queued", {"batch": BATCH})
            return
        profile = load_company_profile(settings.products_root, company.slug).profile
        if profile is None:
            raise RuntimeError("Verified ServGo marketing profile unavailable")
        buyers = tuple(s for s in profile.customers.primary_segments if s.strip())
        category = (profile.identity.category or "").casefold()
        if not any(word in category for word in ("платформ", "saas", "программ", "систем", "b2b")):
            raise RuntimeError("Live ServGo profile is not a software/platform offer")
        owner_buyers = [s for s in buyers if any(word in s.casefold()
            for word in ("владельц", "управляющ", "предпринимател", "администратор"))]
        if not owner_buyers:
            raise RuntimeError("Live ServGo profile lacks an actual owner/manager buyer segment")
        buyer = owner_buyers[0]
        emit("selected_declared_buyer", {"buyer_segment": buyer, "segment_count": len(buyers)})
        if any(t["query"] in planned_queries(db, company.id) for t in TOPICS):
            raise RuntimeError("One acceptance query is already queued/published; no duplicates allowed")
        core_clusters = []
        plan_items = []
        for i, item in enumerate(TOPICS, start=1):
            intent = classify_intent(item["query"])
            stage = STAGE[intent]
            cid = f"c{i:03d}"
            core_clusters.append(QueryCluster(id=cid, head_query=item["query"], queries=[item["query"]],
                intent=intent, stage=stage, engines=["google", "yandex"]))
            plan_items.append(PlanItem(id=f"a{i:03d}", cluster_ids=[cid],
                primary_query=item["query"], working_title=item["title"], stage=stage,
                intent=intent, target_segment=buyer, reader_problem=item["problem"],
                ai_questions=item["questions"], next_step=item["next"]))
        core = SemanticCore(seeds=[t["seed"] for t in TOPICS],
            collected_at=datetime.now(timezone.utc), demand_data="none",
            sources=[SourceStatus(engine=engine, requests=data["requests"],
                failed_requests=data["failed"], queries_found=data["found"])
                for engine, data in sources.items()],
            clusters=core_clusters)
        plan = SeoContentPlan(items=plan_items)
        validate_plan(plan, core, frozenset(planned_queries(db, company.id)),
            buyer_segments=buyers, b2b_provider=True)
        artifacts = ArtifactService(db)
        artifacts.create(product_id=company.id, kind="seo_acceptance_batch",
            title=BATCH, content=json.dumps({"topics": evidence,
                "query_type": "search_suggestions_not_frequency", "created_at": datetime.now(timezone.utc).isoformat()},
                ensure_ascii=False))
        artifacts.create(product_id=company.id, kind="semantic_core",
            title="SEO acceptance: 3 real-source B2B queries", content=core.model_dump_json())
        plan_row = artifacts.create(product_id=company.id, kind="seo_content_plan",
            title="SEO acceptance: three B2B buyer journeys", content=plan.model_dump_json())
        db.flush()
        tasks = queue_plan(db, plan_row, {c.id: c for c in core.clusters})
        if len(tasks) != 3:
            raise AssertionError("Expected exactly three tasks; queue transaction must be inspected")
        emit("queued", {"batch": BATCH, "sources": sources, "topics": evidence,
            "tasks": [{"query": item.primary_query, "status": str(task.status),
                       "due_at": task.due_at}
                      for item, task in zip(plan.items, tasks)]})

if __name__ == "__main__":
    asyncio.run(main())
