import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID
from sqlalchemy import and_, select, update
from app.content.render import asset_path, render_video
from app.content.quick_post import QuickPostRequest, run_quick_post
from app.content.revisions import EditRequest, run_post_edit
from app.content.creative import CreativeRequest, run_creative_once
from app.core.composition import build_article_production
from app.models.domain import AgentRun, Artifact, Product, Task, TaskStatus
from app.services.artifacts import ArtifactService
from app.workflows.article_contracts import ArticleProductionInput, ShortVideoDerivativeDraft

async def run_content_once(session_factory, settings):
    with session_factory() as db:
        from app.seo.queue import SEO_TASK_PREFIX, exhausted_products, SeoQuotaDeferred
        from app.seo.activation import enabled_product_ids, require_enabled
        query = select(Task).where(Task.assigned_agent.in_(['content_factory', 'content_renderer', 'content_editor', 'content_creative']),
            Task.status == TaskStatus.QUEUED)
        # Never run paid SEO content work for a company that did not opt in.
        enabled = enabled_product_ids(db)
        from sqlalchemy import or_
        query = query.where(or_(
            ~Task.title.like(f'{SEO_TASK_PREFIX}%'),
            Task.product_id.in_(enabled) if enabled else False,
        ))
        exhausted = (exhausted_products(db, settings.seo_articles_per_day)
                     if settings.seo_articles_per_day > 0 else set())
        if exhausted:
            # Existing queued retries that already occupied a slot today may
            # continue. New jobs wait for the next local day at capacity.
            from app.seo.queue import quota_task_ids_today
            previously_counted = {
                task_id for task_ids in quota_task_ids_today(db).values()
                for task_id in task_ids
            }
            query = query.where(or_(
                ~and_(Task.title.like(f'{SEO_TASK_PREFIX}%'), Task.product_id.in_(exhausted)),
                Task.id.in_(previously_counted),
            ))
        # SEO articles enter production only on their assigned calendar date.
        # Other content workers and old unscheduled jobs are unaffected.
        from app.seo.schedule import db_time
        from sqlalchemy import or_
        query = query.where(or_(
            ~Task.title.like(f'{SEO_TASK_PREFIX}%'),
            Task.due_at.is_(None),
            Task.due_at <= db_time(db, datetime.now(timezone.utc)),
        ))
        task = db.scalar(query.order_by(Task.priority.desc(), Task.created_at, Task.id).limit(1))
        if task is None:
            return 0
        task_id, agent, product_id = task.id, task.assigned_agent, task.product_id
        if agent == 'content_factory':
            request = db.scalar(select(Artifact).where(Artifact.task_id == task_id, Artifact.kind == 'content_request'))
            try:
                if request is None:
                    raise ValueError('Missing content request')
                if task.title.startswith(SEO_TASK_PREFIX):
                    require_enabled(db, product_id)
                    # The workflow records the start after winning the task claim.
                raw = json.loads(request.content)
                if raw.get("mode") == "post":
                    data = QuickPostRequest.model_validate(raw)
                    if data.product_id != product_id:
                        raise ValueError("Wrong product")
                    await run_quick_post(db, settings, data.model_copy(update={"task_id": task_id}))
                else:
                    data = ArticleProductionInput.model_validate(raw)
                    if data.product_id != product_id:
                        raise ValueError('Wrong product')
                    if data.seo_brief is not None:
                        require_enabled(db, product_id)
                    result = await build_article_production(
                        db, settings, force_critic=True
                    ).run(data.model_copy(update={'task_id': task_id}))
                    if (data.seo_brief is not None and result.status == 'failed'
                            and getattr(result, 'retryable', False)):
                        # No more than ONE automatic retry; do not rerun a
                        # validation/factual failure or previously finished page.
                        started = list(db.scalars(select(Artifact.id).where(
                            Artifact.task_id == task_id,
                            Artifact.kind == 'seo_article_started')))
                        if len(started) < 2:
                            retry_at = datetime.now(timezone.utc) + timedelta(minutes=10)
                            moved = db.execute(update(Task).where(
                                Task.id == task_id, Task.product_id == product_id,
                                Task.status == TaskStatus.FAILED).values(
                                status=TaskStatus.QUEUED,
                                due_at=db_time(db, retry_at)))
                            if moved.rowcount == 1:
                                ArtifactService(db).create(product_id=product_id, task_id=task_id,
                                    kind='seo_article_retry_scheduled', title='SEO retry after transient failure',
                                    content=json.dumps({'retry_delay_minutes': 10, 'reason': 'provider_transient'}))
                            db.commit()
                    if data.seo_brief is not None and result.status == 'completed':
                        # Clean critic verdict + static checks publish automatically; failures wait for review.
                        from app.seo.publishing import publish_if_clear
                        try:
                            publish_if_clear(db, result.artifact_ids[0], daily_limit=settings.seo_publications_per_day)
                        except Exception:
                            db.rollback()
                            logging.getLogger(__name__).exception('SEO article publication failed for task %s', task_id)
                return 1
            except SeoQuotaDeferred:
                # Another worker took the last daily slot; keep this task queued.
                db.rollback()
                return 0
            except (Exception, asyncio.CancelledError) as exc:
                db.rollback()
                db.execute(update(Task).where(Task.id == task_id, Task.status == TaskStatus.QUEUED).values(status=TaskStatus.FAILED))
                db.commit()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return 1
        if agent == 'content_creative':
            try:
                record = db.scalar(select(Artifact).where(
                    Artifact.task_id == task_id, Artifact.product_id == product_id,
                    Artifact.kind == 'creative_request'
                ))
                if record is None:
                    raise ValueError('Missing creative request')
                data = CreativeRequest.model_validate_json(record.content)
                if data.product_id != product_id:
                    raise ValueError('Wrong creative company')
                await run_creative_once(db, settings, data, task_id=task_id)
                return 1
            except (Exception, asyncio.CancelledError) as exc:
                db.rollback()
                db.execute(update(Task).where(
                    Task.id == task_id, Task.status == TaskStatus.QUEUED
                ).values(status=TaskStatus.FAILED))
                db.commit()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return 1
        if agent == 'content_editor':
            try:
                edit = db.scalar(select(Artifact).where(
                    Artifact.task_id == task_id,
                    Artifact.product_id == product_id,
                    Artifact.kind == 'content_edit_request'
                ))
                if edit is None:
                    raise ValueError('Missing owner edit request')
                data = EditRequest.model_validate_json(edit.content)
                if data.product_id != product_id:
                    raise ValueError('Wrong edit company')
                await run_post_edit(db, settings, data, task_id=task_id)
                return 1
            except (Exception, asyncio.CancelledError) as exc:
                db.rollback()
                db.execute(update(Task).where(
                    Task.id == task_id, Task.status == TaskStatus.QUEUED
                ).values(status=TaskStatus.FAILED))
                db.commit()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return 1
        claimed = db.execute(update(Task).where(Task.id == task_id, Task.status == TaskStatus.QUEUED)
                             .values(status=TaskStatus.RUNNING))
        if claimed.rowcount != 1:
            db.rollback()
            return 0
        run = AgentRun(task_id=task_id, agent_name='content_renderer', provider='local', model='ffmpeg',
                       status='running', started_at=datetime.now(timezone.utc))
        db.add(run)
        db.flush()
        run_id = run.id
        db.commit()
        try:
            request = db.scalar(select(Artifact).where(Artifact.task_id == task_id, Artifact.kind == 'render_request'))
            data = json.loads(request.content)
            article = db.get(Artifact, UUID(data['article_id']))
            if article is None or article.product_id != product_id:
                raise ValueError('Wrong source article')
            product = db.get(Product, product_id)
            for raw_id in data['script_ids']:
                script = db.get(Artifact, UUID(raw_id))
                if script is None or script.product_id != product_id or script.task_id != article.task_id or script.kind != 'video_script':
                    raise ValueError('Wrong source script')
                brief = ShortVideoDerivativeDraft.model_validate({k:v for k,v in json.loads(script.content).items() if k in ShortVideoDerivativeDraft.model_fields})
                target = asset_path(settings.products_root, product_id, script.id, 'video.mp4').parent
                metadata = await asyncio.to_thread(render_video, brief.production_brief, target, product.name)
                ArtifactService(db).create(product_id=product_id, task_id=task_id, agent_run_id=run_id,
                    kind='video_render', title=script.title,
                    content=json.dumps({'article_id': str(article.id), 'script_id': str(script.id), **metadata}),
                    status='awaiting_approval')
            db.get(Task, task_id).status = TaskStatus.COMPLETED
            db.get(AgentRun, run_id).status = 'completed'
            db.get(AgentRun, run_id).finished_at = datetime.now(timezone.utc)
            db.commit()
        except (Exception, asyncio.CancelledError) as exc:
            db.rollback()
            db.get(Task, task_id).status = TaskStatus.FAILED
            db.get(AgentRun, run_id).status = 'failed'
            db.get(AgentRun, run_id).error = 'local_render_failed:' + type(exc).__name__
            db.get(AgentRun, run_id).finished_at = datetime.now(timezone.utc)
            db.commit()
            if isinstance(exc, asyncio.CancelledError):
                raise
    return 1
