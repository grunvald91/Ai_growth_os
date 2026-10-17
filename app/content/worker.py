import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select, update
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
        task = db.scalar(select(Task).where(Task.assigned_agent.in_(['content_factory', 'content_renderer', 'content_editor', 'content_creative']),
            Task.status == TaskStatus.QUEUED).order_by(Task.priority.desc(), Task.created_at, Task.id).limit(1))
        if task is None:
            return 0
        task_id, agent, product_id = task.id, task.assigned_agent, task.product_id
        if agent == 'content_factory':
            request = db.scalar(select(Artifact).where(Artifact.task_id == task_id, Artifact.kind == 'content_request'))
            try:
                if request is None:
                    raise ValueError('Missing content request')
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
                    await build_article_production(db, settings).run(
                        data.model_copy(update={'task_id': task_id})
                    )
                return 1
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
