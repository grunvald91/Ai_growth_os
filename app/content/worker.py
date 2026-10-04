import asyncio
from sqlalchemy import select, update

from app.core.composition import build_article_production
from app.models.domain import Artifact, Task, TaskStatus
from app.workflows.article_contracts import ArticleProductionInput


async def run_content_once(session_factory, settings):
    """Run content preparation only; video rendering is intentionally disabled."""
    with session_factory() as db:
        # Owner decision: AI Growth OS produces video scripts/storyboards only.
        # Cancel legacy renderer work, including a stale RUNNING row left by a replaced worker.
        cancelled = db.execute(
            update(Task)
            .where(
                Task.assigned_agent == 'content_renderer',
                Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]),
            )
            .values(status=TaskStatus.CANCELLED)
        ).rowcount
        if cancelled:
            db.commit()

        task = db.scalar(
            select(Task)
            .where(Task.assigned_agent == 'content_factory', Task.status == TaskStatus.QUEUED)
            .order_by(Task.priority.desc(), Task.created_at, Task.id)
            .limit(1)
        )
        if task is None:
            return 1 if cancelled else 0

        task_id, product_id = task.id, task.product_id
        request = db.scalar(
            select(Artifact).where(
                Artifact.task_id == task_id,
                Artifact.kind == 'content_request',
            )
        )
        try:
            if request is None:
                raise ValueError('Missing content request')
            data = ArticleProductionInput.model_validate_json(request.content)
            if data.product_id != product_id:
                raise ValueError('Wrong product')
            await build_article_production(db, settings).run(
                data.model_copy(update={'task_id': task_id})
            )
            return 1
        except (Exception, asyncio.CancelledError) as exc:
            db.rollback()
            db.execute(
                update(Task)
                .where(Task.id == task_id, Task.status == TaskStatus.QUEUED)
                .values(status=TaskStatus.FAILED)
            )
            db.commit()
            if isinstance(exc, asyncio.CancelledError):
                raise
            return 1
