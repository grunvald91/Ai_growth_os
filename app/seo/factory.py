"""The SEO factory as one background job: seeds -> real suggestions -> clusters -> funnel plan -> article tasks."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from uuid import UUID

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.composition import build_seo_llm
from app.llm.errors import ProviderEmptyResponseError, ProviderMalformedResponseError
from app.models.domain import Artifact, Product, Task, TaskStatus
from app.seo.planner import PlanError, PLAN_VALIDATION_KIND, plan_content, plan_seeds
from app.seo.queue import queue_plan
from app.seo.service import SeoSuggestionsUnavailable, build_semantic_core
from app.services.artifacts import ArtifactService
from app.services.tasks import TaskService
from app.seo.activation import require_enabled, enabled_product_ids, editorial_preferences

_logger = logging.getLogger(__name__)
AGENT = "seo_factory"
REQUEST_KIND = "seo_factory_request"
REPORT_KIND = "seo_factory_report"
ATTEMPT_KIND = "seo_factory_attempt"
STAGE_KIND = "seo_factory_stage"


def record_stage(db: Session, product_id: UUID, task_id: UUID, stage: str) -> None:
    """Persist progress before external calls; no customer prompts or credentials in logs."""
    ArtifactService(db).create(
        product_id=product_id, task_id=task_id, kind=STAGE_KIND,
        title="SEO stage", content=json.dumps({
            "stage": stage, "recorded_at": datetime.now(timezone.utc).isoformat(),
        }),
    )
    db.commit()
    _logger.info("seo_factory_stage task=%s stage=%s", task_id, stage)


def failure_details(exc: Exception) -> dict:
    """Only machine-readable, non-sensitive error metadata; never persist provider bodies."""
    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
    if isinstance(exc, SeoSuggestionsUnavailable):
        code = exc.reason
    elif isinstance(exc, PlanError):
        code = "plan_validation_failed"
    elif isinstance(exc, (ProviderEmptyResponseError, ProviderMalformedResponseError)):
        code = "model_unusable_response"
    elif isinstance(exc, httpx.HTTPStatusError):
        code = "provider_http_error"
    elif isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        code = "upstream_timeout"
    elif isinstance(exc, httpx.TransportError):
        code = "network_failure"
    elif isinstance(exc, ValueError):
        code = "invalid_data_or_configuration"
    else:
        code = "internal_exception"
    # Store only a bounded category, not full LLM output, private prompts or profile.
    plan_issue = None
    if isinstance(exc, PlanError):
        message = str(exc)
        plan_issue = (
            "unknown_cluster" if message.startswith("Unknown cluster") else
            "cluster_stage_intent_mismatch" if message.startswith("Stage or intent") else
            "query_not_in_collected_clusters" if message.startswith("Primary query of") else
            "duplicate_or_previously_written_query" if message.startswith("Primary query repeated") else
            "no_fresh_clusters" if message.startswith("Every collected query") else
            "empty_company_profile" if message.startswith("The company profile") else
            "other_plan_validation"
        )
    return {
        "error": type(exc).__name__, "error_code": code,
        **({"plan_issue": plan_issue} if plan_issue else {}),
        **({"http_status": status} if status is not None else {}),
    }


def recover_stale_factory(db):
    """The lease exceeds the hard run deadline, so expired work cannot still run."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    if db.bind.dialect.name == "sqlite":
        cutoff = cutoff.replace(tzinfo=None)
    for task in db.scalars(select(Task).where(Task.assigned_agent == AGENT,
            Task.status == TaskStatus.RUNNING, Task.updated_at < cutoff)):
        attempts = list(db.scalars(select(Artifact.id).where(
            Artifact.task_id == task.id, Artifact.kind == ATTEMPT_KIND)))
        task.status = TaskStatus.QUEUED if len(attempts) < 3 else TaskStatus.FAILED
    db.commit()


def queue_factory(db: Session, product_id: UUID, focus: str | None = None, *,
                  horizon_days: int = 7, commit: bool = True) -> Task:
    """One active factory run per company, including simultaneous dispatcher/UI requests."""
    if horizon_days not in (7, 30):
        raise ValueError("SEO planning horizon must be 7 or 30 days")
    # Serialize *all* producer paths (dispatcher and Studio form) on the
    # company row. The partial unique constraint is not present in old schemas.
    product = db.scalar(select(Product.id).where(Product.id == product_id).with_for_update())
    if product is None:
        raise LookupError("Компания не найдена")
    require_enabled(db, product_id)
    active = db.scalar(select(Task).where(Task.product_id == product_id, Task.assigned_agent == AGENT,
                                          Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING])))
    if active is not None:
        return active
    task = TaskService(db).create(product_id=product_id, title="SEO-завод: запросы, план и статьи", assigned_agent=AGENT,
                                  status=TaskStatus.QUEUED, priority=50,
                                  description="Собрать запросы клиентов, составить контент-план под воронку и поставить статьи в работу.")
    ArtifactService(db).create(product_id=product_id, task_id=task.id, kind=REQUEST_KIND, title="Запрос на SEO-завод",
                               content=json.dumps({"focus": focus, "horizon_days": horizon_days}, ensure_ascii=False))
    if commit:
        db.commit()
    return task


async def run_factory_once(session_factory, settings, *, provider=None, client: httpx.AsyncClient | None = None) -> int:
    with session_factory() as db:
        recover_stale_factory(db)
        enabled = enabled_product_ids(db)
        if not enabled:
            return 0
        task = db.scalar(select(Task).where(
            Task.assigned_agent == AGENT, Task.status == TaskStatus.QUEUED,
            Task.product_id.in_(enabled),
        ).order_by(Task.priority.desc(), Task.created_at, Task.id).limit(1))
        if task is None:
            return 0
        task_id, product_id = task.id, task.product_id
        claimed = db.execute(update(Task).where(Task.id == task_id, Task.status == TaskStatus.QUEUED)
                             .values(status=TaskStatus.RUNNING))
        if claimed.rowcount != 1:
            db.rollback()
            return 0
        ArtifactService(db).create(product_id=product_id, task_id=task_id, kind=ATTEMPT_KIND,
                                   title="SEO attempt", content="{}")
        db.commit()
        request = db.scalar(select(Artifact).where(Artifact.task_id == task_id, Artifact.kind == REQUEST_KIND))
        try:
            parameters = json.loads(request.content) if request else {}
            focus = parameters.get("focus") or None
            horizon_days = parameters.get("horizon_days", 7)
            if horizon_days not in (7, 30):
                raise ValueError("Invalid SEO planning horizon")
            async with asyncio.timeout(1200):
                await _execute(db, settings, task_id, product_id, focus, provider, client, horizon_days)
        except Exception as exc:
            db.rollback()
            stage_rows = list(db.scalars(select(Artifact).where(
                Artifact.task_id == task_id, Artifact.kind == STAGE_KIND,
            )))
            # DB creation timestamps may have only second precision; UUID ordering is not time ordering.
            stage_artifact = max(
                stage_rows,
                key=lambda row: json.loads(row.content).get("recorded_at")
                or row.created_at.isoformat(),
            ) if stage_rows else None
            try:
                stage = json.loads(stage_artifact.content).get("stage") if stage_artifact else "starting"
            except (TypeError, ValueError):
                stage = "unknown"
            details = failure_details(exc)
            from app.seo.planner import PLAN_KIND
            attempts = list(db.scalars(select(Artifact.id).where(
                Artifact.task_id == task_id, Artifact.kind == ATTEMPT_KIND)))
            resumable = db.scalar(select(Artifact.id).where(
                Artifact.task_id == task_id, Artifact.kind == PLAN_KIND,
            )) is not None
            transient = isinstance(exc, (TimeoutError, httpx.TransportError,
                                          ProviderEmptyResponseError, ProviderMalformedResponseError))
            if isinstance(exc, httpx.HTTPStatusError):
                transient = exc.response.status_code in {404, 408, 425, 429, 500, 502, 503, 504}
            retry = len(attempts) < 3 and (resumable or transient)
            details.update(stage=stage, attempt=len(attempts), retryable=retry)
            _logger.warning(
                "seo_factory_failed task=%s stage=%s code=%s error_type=%s attempt=%s retry=%s",
                task_id, stage, details["error_code"], details["error"], len(attempts), retry,
            )
            ArtifactService(db).create(
                product_id=product_id, task_id=task_id, kind=REPORT_KIND,
                title="SEO-завод остановился", content=json.dumps(details),
            )
            TaskService(db).update(task_id, status=TaskStatus.QUEUED if retry else TaskStatus.FAILED)
            db.commit()
        return 1


async def _execute(db, settings, task_id, product_id, focus, provider, client, horizon_days=7):
    from app.seo.contracts import SemanticCore, SeoContentPlan
    from app.seo.planner import PLAN_KIND
    from app.seo.service import KIND
    require_enabled(db, product_id)
    record_stage(db, product_id, task_id, "prepare_queries")
    llm, privacy = build_seo_llm(settings, provider)
    core_artifact = db.scalar(select(Artifact).where(Artifact.task_id == task_id, Artifact.kind == KIND))
    if core_artifact is None:
        checkpoint = db.scalar(select(Artifact).where(Artifact.task_id == task_id,
                                                       Artifact.kind == "seo_suggest_checkpoint"))
        seeds = (json.loads(checkpoint.content)["seeds"] if checkpoint else
                 await plan_seeds(db, llm, privacy, product_id, products_root=settings.products_root,
                                  model=settings.llm_model, focus=focus))
        record_stage(db, product_id, task_id, "collect_suggestions")
        core_artifact = await build_semantic_core(db, product_id, seeds, client=client, task_id=task_id)
    core = SemanticCore.model_validate_json(core_artifact.content)
    plan = db.scalar(select(Artifact).where(Artifact.task_id == task_id, Artifact.kind == PLAN_KIND))
    if plan is None:
        record_stage(db, product_id, task_id, "build_plan")
        # Alternate only among already configured compatible gateway models;
        # never silently choose an unconfigured paid provider. Two models max.
        backups = tuple(dict.fromkeys(
            name.strip() for name in settings.llm_fallback_models.split(",")
            if name.strip() and name.strip() != settings.llm_model
        )) if (provider is None and settings.llm_provider == "xai"
               and not settings.model_router_enabled) else ()
        prior_invalid = db.scalar(select(Artifact.id).where(
            Artifact.product_id == product_id, Artifact.task_id == task_id,
            Artifact.kind == PLAN_VALIDATION_KIND,
        ).limit(1)) is not None
        if not prior_invalid and backups:
            # Backwards-compatible detection for a failed plan from the
            # release before structured validation artifacts existed.
            prior_reports = list(db.scalars(select(Artifact.content).where(
                Artifact.product_id == product_id, Artifact.task_id == task_id,
                Artifact.kind == REPORT_KIND,
            )))
            for content in prior_reports:
                try:
                    data = json.loads(content)
                except (TypeError, ValueError):
                    continue
                if (data.get("error") == "PlanError"
                        and data.get("stage") == "build_plan"):
                    prior_invalid = True
                    break
        model = backups[0] if prior_invalid and backups else settings.llm_model
        alternate = backups[:1] if model == settings.llm_model else ()
        plan = await plan_content(
            db, llm, privacy, product_id, core,
            products_root=settings.products_root, model=model,
            fallback_models=alternate, factory_task_id=task_id,
            horizon_days=horizon_days,
        )
    require_enabled(db, product_id)
    # All supported topics of the selected weekly or monthly plan are queued.
    all_items = SeoContentPlan.model_validate_json(plan.content).items
    record_stage(db, product_id, task_id, "queue_articles")
    prefs = editorial_preferences(
        db, product_id,
        default_publications_per_day=settings.seo_publications_per_day,
        default_articles_per_day=settings.seo_articles_per_day)
    queued = queue_plan(db, plan, {c.id: c for c in core.clusters},
                        per_day=prefs.publications_per_day, timezone_name=prefs.timezone_name)
    ArtifactService(db).create(product_id=product_id, task_id=task_id, kind=REPORT_KIND,
        title=f"SEO-завод: поставлено статей — {len(queued)}",
        content=json.dumps({"seeds": len(core.seeds), "clusters": len(core.clusters),
                            "articles_queued": len(queued), "articles_deferred": len(all_items) - len(queued),
                            "planning_horizon_days": horizon_days,
                            "sources": [s.model_dump() for s in core.sources]}))
    TaskService(db).update(task_id, status=TaskStatus.COMPLETED)
    record_stage(db, product_id, task_id, "completed")
    db.commit()
