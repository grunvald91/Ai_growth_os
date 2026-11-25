"""Independent SEO watchdog. No model calls or automatic approval overrides.

Repairs use existing bounded worker policies; alerts describe durable DB evidence
and are rate-limited across processes/restarts. A publication receipt is NOT an
HTTP reachability check.
"""
from datetime import datetime, timedelta, timezone
import json
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import select, exists
from sqlalchemy.orm import aliased

from app.models.domain import Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus
from app.seo.activation import SeoFactoryActivation, editorial_preferences, is_enabled
from app.seo.diagnostics import audit_seo
from app.seo.publishing import public_blog_ready
from app.seo.reserve import coverage
from app.seo.schedule import db_time, day_bounds, utc

KIND = "seo_watchdog_state"
PERSISTENCE = timedelta(minutes=30)
RESERVE_GRACE = timedelta(hours=24)
ALERT_COOLDOWN = timedelta(hours=24)
ATTEMPT_COOLDOWN = timedelta(hours=6)
logger = logging.getLogger(__name__)

MESSAGES = {
    "missed_daily_publication": "За предыдущие сутки нет подтверждённой SEO-публикации",
    "reserve_shortage": "Готовый запас статей менее семи дней",
    "pipeline_shortage": "В плане есть дни без материалов",
    "publication_mismatch": "Расходятся статусы статьи и публикации",
    "generation_failed": "Сохраняются ошибки генерации",
    "running_long": "Обнаружены длительные или прерванные задания",
    "missing_article": "Завершённые задания без статьи",
    "public_blog_unavailable": "Не подтверждена привязка публичного блога",
    "model_wait": "Задания длительно ожидают доступности модели",
}


def _date(value):
    try:
        return utc(datetime.fromisoformat(value))
    except (ValueError, TypeError):
        return None


def _in_scope(settings, product):
    return settings.seo_autorun_enabled or (
        settings.seo_servgo_autorun_enabled and product.slug == "servgo"
    )


def _previous_day_published(db, product_id, now, timezone_name):
    tz = ZoneInfo(timezone_name)
    start_today, _ = day_bounds(now, tz=tz)
    start, stop = day_bounds(start_today - timedelta(seconds=1), tz=tz)
    # Require a matching SEO task and article status. Distinct receipts are
    # counted only as evidence of publication, not public URL reachability.
    conflict = aliased(Publication)
    found = db.scalar(select(Publication.id).join(Artifact, Publication.artifact_id == Artifact.id)
        .join(Task, Artifact.task_id == Task.id).where(
            Publication.product_id == product_id, Artifact.product_id == product_id,
            Task.product_id == product_id, Task.assigned_agent == "content_factory",
            Task.title.like("SEO-ARTICLE:%"), Artifact.kind == "article",
            Artifact.status == ArtifactStatus.PUBLISHED,
            Publication.channel == "own_site", Publication.status == "published",
            Publication.published_at >= db_time(db, start),
            Publication.published_at < db_time(db, stop),
            ~exists(select(conflict.id).where(conflict.product_id == product_id,
                conflict.artifact_id == Artifact.id, conflict.channel == "own_site",
                conflict.status != "published")),
        ).limit(1))
    return start, found is not None


def observe(db, product, activation, settings, now):
    """Current evidence only; no raw prompts, company descriptions or article bodies."""
    prefs = editorial_preferences(db, product.id,
        default_interval_days=settings.seo_autorun_interval_days,
        default_publications_per_day=settings.seo_publications_per_day,
        default_articles_per_day=settings.seo_articles_per_day)
    issues = {}
    audit = audit_seo(db, product.id, now=now)
    for code in ("publication_mismatch", "generation_failed", "running_long", "missing_article"):
        if audit.counts.get(code):
            issues[code] = audit.counts[code]
    waiting = set()
    for task_id, retry_at in db.execute(select(Artifact.task_id, Artifact.title).join(Task, Task.id == Artifact.task_id).where(
            Artifact.product_id == product.id, Task.product_id == product.id,
            Task.status == TaskStatus.QUEUED, Artifact.kind == 'seo_model_wait')):
        next_check = _date(retry_at)
        if next_check and next_check >= now - timedelta(minutes=30):
            waiting.add(task_id)
    if waiting:
        issues['model_wait'] = len(waiting)
    if not public_blog_ready(db, product.id):
        issues["public_blog_unavailable"] = 1
        return issues
    yesterday, has_publication = _previous_day_published(db, product.id, now, prefs.timezone_name)
    if utc(activation.changed_at) <= yesterday and not has_publication:
        issues["missed_daily_publication"] = 1
    if settings.seo_reserve_enabled:
        stock = coverage(db, product.id, now=now, timezone_name=prefs.timezone_name,
                         daily_limit=prefs.publications_per_day)
        today = now.astimezone(ZoneInfo(prefs.timezone_name)).date().isoformat()
        missing_ready = sum(d != today for d in stock.missing_ready_days)
        missing_pipeline = sum(d != today for d in stock.missing_pipeline_days)
        if missing_ready:
            issues["reserve_shortage"] = missing_ready
        if missing_pipeline:
            issues["pipeline_shortage"] = missing_pipeline
    return issues


def inspect_and_claim(db, product_id, settings, *, now=None, can_notify=False):
    """Observe one tenant under the same Product lock used by publishing.

    Return an alert payload only after a durable send claim. A crash between
    send and acknowledgement cannot cause immediate duplicate notifications.
    """
    now = utc(now or datetime.now(timezone.utc))
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    activation = db.get(SeoFactoryActivation, product_id)
    if (product is None or activation is None or not is_enabled(db, product_id)
            or not _in_scope(settings, product)):
        db.rollback()
        return None
    marker = db.scalar(select(Artifact).where(Artifact.product_id == product_id,
        Artifact.kind == KIND).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(1))
    state = {}
    if marker:
        try:
            state = json.loads(marker.content)
            if not isinstance(state, dict):
                state = {}
        except (ValueError, TypeError):
            pass
    issues = observe(db, product, activation, settings, now)
    previous = state.get("since", {})
    if not isinstance(previous, dict):
        previous = {}
    since = {code: (min(_date(previous.get(code)) or now, now)).isoformat()
             for code in issues}
    mature = {}
    for code, count in issues.items():
        age = now - _date(since[code])
        grace = RESERVE_GRACE if code in {"reserve_shortage", "pipeline_shortage", "generation_failed", "model_wait"} else PERSISTENCE
        if age >= grace:
            mature[code] = count
    last_attempt, last_sent = _date(state.get("last_attempt")), _date(state.get("last_sent"))
    send = bool(can_notify and mature
        and (last_attempt is None or now - last_attempt >= ATTEMPT_COOLDOWN)
        and (last_sent is None or now - last_sent >= ALERT_COOLDOWN))
    state.update(observed_at=now.isoformat(), issues=issues, since=since)
    if send:
        state["last_attempt"] = now.isoformat()
    if marker is None:
        marker = Artifact(product_id=product_id, kind=KIND, title="SEO self-monitoring", content="{}")
        db.add(marker)
    marker.content = json.dumps(state, ensure_ascii=False)
    db.commit()
    if not send:
        return None
    # Only the platform operator receives this summary. No private article
    # contents or provider error bodies are sent to Telegram.
    return {"product_id": product_id, "marker_id": marker.id,
            "claim": state["last_attempt"], "slug": product.slug, "issues": mature}


def acknowledge(db, alert, *, now=None):
    product_id = alert["product_id"]
    db.scalar(select(Product.id).where(Product.id == product_id).with_for_update())
    row = db.get(Artifact, alert["marker_id"])
    if row and row.product_id == product_id and row.kind == KIND:
        state = json.loads(row.content)
        if state.get("last_attempt") == alert["claim"]:
            state["last_sent"] = utc(now or datetime.now(timezone.utc)).isoformat()
            row.content = json.dumps(state, ensure_ascii=False)
            db.commit()


def recover_once(session_factory, settings):
    """Use existing guarded recovery; never start paid inference in watchdog."""
    from app.seo.article_retry import recover_failed_schema_once
    from app.seo.autoschedule import enqueue_enabled_if_due
    # Lease recovery already exists in the content worker. Import here so
    # the watchdog also works while older branches are tested independently.
    try:
        from app.seo.interruption import recover_interrupted_articles
    except ImportError:
        recover_interrupted_articles = None
    def schema():
        with session_factory() as db:
            recover_failed_schema_once(db, enabled=settings.seo_schema_recovery_enabled)

    def plan():
        with session_factory() as db:
            enqueue_enabled_if_due(db, settings)

    actions = [schema, plan]
    if recover_interrupted_articles:
        actions.insert(0, lambda: recover_interrupted_articles(session_factory, settings.products_root))
    for action in actions:
        try:
            action()
        except Exception as exc:
            logger.warning("seo_watchdog_recovery_failed type=%s", type(exc).__name__)


async def run_watchdog_once(session_factory, settings, *, api=None, now=None):
    if not settings.seo_watchdog_enabled:
        return 0
    import asyncio
    await asyncio.to_thread(recover_once, session_factory, settings)
    with session_factory() as db:
        products = list(db.scalars(select(SeoFactoryActivation.product_id).where(
            SeoFactoryActivation.enabled.is_(True))))
    if api is None and settings.telegram_bot_token and settings.telegram_owner_chat_id is not None:
        from app.telegram_control_bot import TelegramAPI
        api = TelegramAPI(settings.telegram_bot_token)
    sent = 0
    for product_id in products:
        try:
            with session_factory() as db:
                alert = inspect_and_claim(db, product_id, settings, now=now,
                    can_notify=api is not None and settings.telegram_owner_chat_id is not None)
            if alert is None:
                continue
            lines = ["SEO-завод: сохраняется проблема", "Компания: " + alert["slug"][:100]]
            lines.extend(f"• {MESSAGES[code]} ({count})" for code, count in alert["issues"].items())
            lines.append("Автоматическое восстановление продолжается в рамках действующих ограничений.")
            await api.call("sendMessage", {"chat_id": settings.telegram_owner_chat_id,
                                          "text": "\n".join(lines)})
            with session_factory() as db:
                acknowledge(db, alert, now=now)
            sent += 1
        except Exception as exc:
            logger.warning("seo_watchdog_company_failed product=%s type=%s", product_id, type(exc).__name__)
    return sent
