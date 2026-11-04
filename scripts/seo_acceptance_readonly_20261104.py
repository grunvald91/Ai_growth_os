"""Single-use read-only production SEO acceptance inventory. No company mutations."""
import json
from sqlalchemy import select, func
from app.db.session import SessionLocal
from app.models.domain import Product, Artifact, Task, Publication
from app.seo.activation import is_enabled
from app.seo.contracts import SemanticCore, SeoContentPlan
from app.core.config import get_settings
from app.workflows.article_contracts import ArticleArtifact

TOPIC_TOKENS = {
    "booking_software": ("программ", "онлайн", "запис"),
    "empty_slots": ("окн", "мастер"),
    "ai_marketing": ("ии", "продвиж"),
}

def safe_print(key, value):
    print("SEO_PREFLIGHT " + key + "=" + json.dumps(value, ensure_ascii=False, default=str, sort_keys=True), flush=True)

with SessionLocal() as db:
    settings = get_settings()
    safe_print("global_autorun", bool(settings.seo_autorun_enabled))
    safe_print("servgo_autorun", bool(settings.seo_servgo_autorun_enabled))
    safe_print("provider_ready", bool(settings.xai_api_key if settings.llm_provider == "xai" else settings.anthropic_api_key))
    safe_print("llm_provider", settings.llm_provider)
    company = db.scalar(select(Product).where(Product.slug == "servgo").limit(1))
    safe_print("company_present", bool(company))
    if company is None:
        raise SystemExit(2)
    safe_print("company_opted_in", is_enabled(db, company.id))
    from app.company_profile import load_company_profile
    loaded = load_company_profile(settings.products_root, company.slug)
    profile = loaded.profile
    safe_print("runtime_company_profile", {
        "products_root": str(settings.products_root),
        "profile_source": loaded.source,
        "validation_errors": list(loaded.validation_errors),
        "category": profile.identity.category if profile else None,
        "primary_segments": list(profile.customers.primary_segments) if profile else [],
        "ideal_customer": profile.customers.ideal_customer if profile else None,
        "offer_count": len(profile.offer.products_services) if profile else 0,
        "approved_facts_count": len(profile.proof.approved_facts) if profile else 0,
    })

    safe_print("company_article_limit", settings.seo_articles_per_day)
    safe_print("company_publication_limit", settings.seo_publications_per_day)
    counts = {}
    for kind in ("semantic_core", "seo_content_plan", "seo_factory_report", "seo_final_review", "seo_article_started"):
        counts[kind] = db.scalar(select(func.count()).select_from(Artifact).where(
            Artifact.product_id == company.id, Artifact.kind == kind))
    safe_print("artifacts", counts)
    for kind in ("semantic_core", "seo_content_plan"):
        rows = list(db.scalars(select(Artifact).where(
            Artifact.product_id == company.id, Artifact.kind == kind)
            .order_by(Artifact.created_at.desc()).limit(3)))
        for idx, row in enumerate(rows):
            try:
                if kind == "semantic_core":
                    core = SemanticCore.model_validate_json(row.content)
                    queries = [q for cluster in core.clusters for q in cluster.queries]
                    matched = {name: [q for q in queries if any(token in q.casefold() for token in tokens)][:8]
                        for name, tokens in TOPIC_TOKENS.items()}
                    safe_print(f"semantic_{idx}", {
                        "collected_at": core.collected_at, "sources": [s.model_dump() for s in core.sources],
                        "clusters": len(core.clusters), "queries": len(queries), "topic_matches": matched})
                else:
                    plan = SeoContentPlan.model_validate_json(row.content)
                    safe_print(f"plan_{idx}", {"created_at": row.created_at,
                        "items": len(plan.items),
                        "queries": [i.primary_query for i in plan.items][:25]})
            except Exception as exc:
                safe_print(f"{kind}_{idx}_parse_error", type(exc).__name__)
    publications = list(db.execute(select(Publication, Artifact)
        .join(Artifact, Publication.artifact_id == Artifact.id)
        .where(Publication.product_id == company.id, Publication.channel == "own_site")
        .order_by(Publication.published_at.desc()).limit(20)))
    public = []
    for pub, artifact in publications:
        try:
            article = ArticleArtifact.model_validate_json(artifact.content)
            query = article.seo.primary_query
            critic_status = None
            if artifact.task_id:
                reviews = list(db.scalars(select(Artifact).where(
                    Artifact.product_id == company.id,
                    Artifact.task_id == artifact.task_id,
                    Artifact.kind == "seo_final_review")
                    .order_by(Artifact.created_at.desc()).limit(1)))
                if reviews:
                    critic_status = json.loads(reviews[0].content).get("status")
            public.append({"slug": pub.external_reference, "status": pub.status,
                "query": query, "article_status": str(artifact.status),
                "critic_status": critic_status, "published_at": pub.published_at})
        except Exception as exc:
            public.append({"slug": pub.external_reference, "status": pub.status,
                "parse_error": type(exc).__name__})
    safe_print("public_articles", public)
    jobs = list(db.scalars(select(Task).where(Task.product_id == company.id,
        Task.assigned_agent.in_(("seo_factory", "content_factory")))
        .order_by(Task.created_at.desc()).limit(150)))
    kinds = {}
    for task in jobs:
        if task.assigned_agent == "content_factory" and not task.title.startswith("SEO-ARTICLE:"):
            continue
        key = f"{task.assigned_agent}:{str(task.status)}"
        kinds[key] = kinds.get(key, 0) + 1
    safe_print("seo_jobs_by_status_last_150", kinds)
    # Only the SEO request topic, never contact details or client bookings.
    from app.workflows.article_contracts import ArticleProductionInput
    pending = []
    for task in jobs:
        if task.assigned_agent != "content_factory" or not task.title.startswith("SEO-ARTICLE:"):
            continue
        if str(task.status).split(".")[-1] != "QUEUED":
            continue
        request = db.scalar(select(Artifact).where(Artifact.task_id == task.id,
            Artifact.kind == "content_request").limit(1))
        if not request:
            continue
        try:
            data = ArticleProductionInput.model_validate_json(request.content)
            pending.append({"query": data.seo_brief.primary_query if data.seo_brief else None,
                "due_at": task.due_at, "status": str(task.status)})
        except Exception:
            pending.append({"parse_error": True})
    safe_print("queued_seo_request_queries", pending)


# Real Yandex/Google suggestion checks, without DB writes or language-model calls.
import asyncio
from app.seo.suggest import collect

_BUYER_PROBES = {
    "booking_software": "программа онлайн записи клиентов",
    "empty_slots": "как привлечь клиентов в салон красоты",
    "empty_slots_precise": "как заполнить пустые окна в салоне красоты",
    "ai_marketing": "ии для продвижения бизнеса",
}

def _buyer_relevant(topic, query):
    q = " " + query.casefold().replace("ё", "е") + " "
    if topic == "booking_software":
        return "запис" in q and any(v in q for v in ("программ", "сервис", "онлайн", "систем", "crm", "црм"))
    if topic.startswith("empty_slots"):
        return (any(v in q for v in ("привлеч", "заполн", "свободн", "пусты", "нехватк"))
            and any(v in q for v in ("клиент", "салон", "мастер", "расписан"))
            and not any(v in q for v in ("пластиков", "стеклопакет", "окна пвх", "город мастеров")))
    if topic == "ai_marketing":
        return any(v in q for v in (" ии ", "нейросет", "искусствен", " ai ")) and any(
            v in q for v in ("продвиж", "маркетинг", "реклам", "бизнес", "контент"))
    return False

async def probe_buyer_sources():
    for topic, seed in _BUYER_PROBES.items():
        results = await collect([seed], max_requests=24, concurrency=4, total_timeout=48)
        evidence = {}
        for engine, data in results.items():
            candidates = sorted(q for q in data.queries if _buyer_relevant(topic, q))
            evidence[engine] = {"requests": data.requests, "failed": data.failed,
                "returned": len(data.queries), "buyer_queries": candidates[:14]}
        safe_print("buyer_source_" + topic, {"seed": seed, "sources": evidence})

asyncio.run(probe_buyer_sources())
