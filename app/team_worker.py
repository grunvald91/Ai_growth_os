import asyncio

from sqlalchemy import select

from app.core.composition import build_team_cycle
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import Product, Task, TaskStatus


async def run_once() -> int:
    settings = get_settings()
    with SessionLocal() as db:
        product_ids = list(
            db.scalars(
                select(Product.id)
                .join(Task, Task.product_id == Product.id)
                .where(Task.status == TaskStatus.QUEUED)
                .distinct()
                .limit(10)
            )
        )

    completed_products = 0
    for product_id in product_ids:
        with SessionLocal() as db:
            try:
                cycle = build_team_cycle(db, settings)
                result = await cycle.run(
                    product_id,
                    max_tasks=settings.team_worker_batch_size,
                    followup_cmo=True,
                )
                if result.task_results:
                    completed_products += 1
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
