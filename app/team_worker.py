import asyncio

from sqlalchemy import select

from app.core.composition import build_team_cycle
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import AgentRun, Product, Task, TaskStatus
from app.services.control import ControlService
from app.telegram_control_bot import TelegramAPI


async def _notify_owner(settings, *, product_name: str, result, decisions_waiting: int) -> None:
    if not settings.telegram_bot_token or settings.telegram_owner_chat_id is None:
        return
    completed = len(result.completed_task_ids)
    failed = len(result.failed_task_ids)
    skipped = len(result.skipped_task_ids)
    lines = [
        "🤖 Команда завершила очередной этап работы",
        "",
        f"📌 Проект: {product_name}",
        f"✅ Выполнено задач: {completed}",
        f"⚠️ С ошибкой: {failed}",
        f"⏭ Пропущено: {skipped}",
        "",
        (
            f"🙋 Нужно твоё решение: {decisions_waiting}"
            if decisions_waiting
            else "✅ От тебя сейчас ничего не требуется."
        ),
    ]
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ На согласование", "callback_data": "menu:inbox"},
                {"text": "📊 Главное", "callback_data": "menu:status"},
            ],
            [{"text": "🧪 Тесты", "callback_data": "menu:experiments"}],
        ]
    }
    try:
        await TelegramAPI(
            settings.telegram_bot_token,
            settings.telegram_proxy_url,
        ).call(
            "sendMessage",
            {
                "chat_id": settings.telegram_owner_chat_id,
                "text": "\n".join(lines),
                "reply_markup": keyboard,
            },
        )
    except Exception:
        # A control notification must never change task outcome or crash the worker.
        return




def _recover_transient_failed_experiment_tasks(
    db,
    current_model: str | None = None,
    fallback_enabled: bool = False,
    repair_enabled: bool = False,
    reuse_enabled: bool = False,
) -> int:
    """Recover a bounded transient failure, including one model-failover attempt.

    A first research attempt creates up to four child runs and may be retried once.
    If two generations (up to eight runs) all failed transiently on a *different*
    model than the worker now uses, one additional failover attempt is allowed.
    No recovery is allowed beyond that.
    """
    parents = list(
        db.scalars(
            select(Task)
            .where(
                Task.status == TaskStatus.FAILED,
                Task.parent_id.is_(None),
                Task.title.like("EXP-%"),
            )
            .order_by(Task.created_at.asc(), Task.id)
            .limit(20)
        )
    )
    recovered = 0
    for parent in parents:
        child_tasks = list(
            db.scalars(select(Task).where(Task.parent_id == parent.id))
        )
        child_ids = [task.id for task in child_tasks]
        if not child_ids:
            continue
        runs = list(
            db.scalars(
                select(AgentRun)
                .where(AgentRun.task_id.in_(child_ids))
                .order_by(AgentRun.started_at.asc(), AgentRun.id)
            )
        )
        if not runs:
            continue
        transient_only = all(
            run.error == "research_worker_failed:HTTPStatusError" for run in runs
        )
        first_generation = len(runs) <= 4
        model_failover = (
            current_model is not None
            and len(runs) <= 8
            and all(run.model and run.model != current_model for run in runs)
        )
        fallback_recovery = fallback_enabled and len(runs) <= 8
        validation_errors = sum(
            run.error == "research_worker_failed:ValidationError" for run in runs
        )
        repair_recovery = (
            repair_enabled
            and 9 <= len(runs) <= 12
            and 1 <= validation_errors <= 4
            and all(
                run.error in {
                    None,
                    "research_worker_failed:HTTPStatusError",
                    "research_worker_failed:ValidationError",
                }
                for run in runs
            )
        )
        completed_roles = {
            task.assigned_agent
            for task in child_tasks
            if task.status == TaskStatus.COMPLETED
        }
        reuse_recovery = (
            reuse_enabled
            and 13 <= len(runs) <= 16
            and len(completed_roles) >= 2
        )
        if (
            transient_only and (first_generation or model_failover or fallback_recovery)
        ) or repair_recovery or reuse_recovery:
            parent.status = TaskStatus.QUEUED
            recovered += 1
    if recovered:
        db.commit()
    return recovered

async def run_once() -> int:
    settings = get_settings()
    from app.content.whiteboard.worker import run_whiteboard_once
    if await run_whiteboard_once(SessionLocal, settings):
        return 1
    from app.competitive_intelligence.worker import run_intelligence_once
    if await run_intelligence_once(SessionLocal, settings):
        return 1
    from app.content.calendar import run_planner_once, tick_calendar, recover_stale_planner
    with SessionLocal() as db:
        recover_stale_planner(db)
        tick_calendar(db, settings)
    if await run_planner_once(SessionLocal, settings):
        return 1
    # Only explicitly scheduled, owner-approved posts can leave the system.
    # The dispatcher is a durable at-most-once sender and is OFF by default.
    if settings.content_telegram_autopost_enabled:
        from app.content.telegram_publishing import run_due_telegram_post_once
        if await run_due_telegram_post_once(SessionLocal, settings):
            return 1
    from app.content.worker import run_content_once
    content_count = await run_content_once(SessionLocal, settings)
    if content_count:
        return content_count
    with SessionLocal() as db:
        _recover_transient_failed_experiment_tasks(
            db,
            current_model=settings.llm_model,
            fallback_enabled=bool(settings.llm_fallback_models.strip()),
            repair_enabled=True,
            reuse_enabled=True,
        )
        product_ids = list(
            db.scalars(
                select(Product.id)
                .join(Task, Task.product_id == Product.id)
                .where(
                    Task.status == TaskStatus.QUEUED,
                    Task.title.like("EXP-%"),
                )
                .distinct()
                .limit(10)
            )
        )

    completed_products = 0
    for product_id in product_ids:
        with SessionLocal() as db:
            try:
                product = db.get(Product, product_id)
                cycle = build_team_cycle(db, settings)
                result = await cycle.run(
                    product_id,
                    max_tasks=1,
                    followup_cmo=True,
                )
                if result.task_results:
                    completed_products += 1
                    decisions_waiting = ControlService(db).product_status(
                        product_id
                    ).decisions_waiting
                    await _notify_owner(
                        settings,
                        product_name=product.name if product is not None else str(product_id),
                        result=result,
                        decisions_waiting=decisions_waiting,
                    )
            except Exception:
                # The worker must survive provider/network failures. Individual
                # workflow code persists bounded failure state where applicable.
                continue
    return completed_products


async def _calendar_loop(settings):
    from app.content.calendar import tick_calendar
    while True:
        try:
            with SessionLocal() as db:
                tick_calendar(db, settings)
        except Exception:
            import logging
            logging.getLogger(__name__).warning('calendar_tick_failed')
        await asyncio.sleep(settings.team_worker_idle_poll_seconds)


async def main() -> None:
    settings = get_settings()
    calendar_task = asyncio.create_task(_calendar_loop(settings))
    try:
        while True:
            count = await run_once()
            await asyncio.sleep(
                settings.team_worker_busy_poll_seconds
                if count
                else settings.team_worker_idle_poll_seconds
            )
    finally:
        calendar_task.cancel()
        try:
            await calendar_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
