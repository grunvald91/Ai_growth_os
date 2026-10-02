import asyncio

from sqlalchemy import select

from app.core.composition import build_team_cycle
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import Product, Task, TaskStatus
from app.services.control import ControlService
from app.telegram_control_bot import TelegramAPI


async def _notify_owner(settings, *, product_name: str, result, decisions_waiting: int) -> None:
    if not settings.telegram_bot_token or settings.telegram_owner_chat_id is None:
        return
    completed = len(result.completed_task_ids)
    failed = len(result.failed_task_ids)
    skipped = len(result.skipped_task_ids)
    lines = [
        "🤖 Внутренний цикл команды завершён",
        "",
        product_name,
        f"Выполнено задач: {completed}",
        f"Ошибок: {failed}",
        f"Пропущено: {skipped}",
        f"Решений ждёт тебя: {decisions_waiting}",
    ]
    if result.followup_review is not None:
        lines.append(f"CMO review: {result.followup_review.status}")
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📨 Решения", "callback_data": "menu:inbox"},
                {"text": "📊 Статус", "callback_data": "menu:status"},
            ],
            [{"text": "🧪 Эксперименты", "callback_data": "menu:experiments"}],
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


async def run_once() -> int:
    settings = get_settings()
    with SessionLocal() as db:
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


async def main() -> None:
    settings = get_settings()
    while True:
        count = await run_once()
        await asyncio.sleep(
            settings.team_worker_busy_poll_seconds
            if count
            else settings.team_worker_idle_poll_seconds
        )


if __name__ == "__main__":
    asyncio.run(main())
