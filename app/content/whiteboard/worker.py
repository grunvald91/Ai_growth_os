"""Owner-approved, at-most-once whiteboard jobs on the existing persistent worker."""
from __future__ import annotations

import asyncio
import json
import os
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.models.domain import AgentRun, Artifact, ArtifactStatus, Task, TaskStatus
from app.llm.service import LLMService
from app.privacy import PrivacyGateway
from .adapters import LLMStoryboardProvider, ClaudeHubGeminiImageProvider, OpenRouterRegionProvider, OpenRouterSpeechProvider
from .checkpoints import CheckpointStore, CheckpointError, _Lock
from .jobs import (AGENT, CallLedger, GuardedStage, job_directory, owner_task, record_error, records, refund_unanswered,
                   retry_due, schedule_auto_resume)
from .pipeline import WhiteboardPipeline
from .render import MarkerSweepRenderer
from .review import OpenRouterVisualCritic, LLMVisualAuthor
from .runtime import ReadinessError, SingleCallStoryboardLLM, preflight, safe_error, scoped_settings


def build_pipeline(settings, payload, store):
    if payload.image_provider != 'claudehub' or payload.models.image != 'gemini-3.1-flash-image':
        raise ReadinessError('image_provider_required')
    privacy = PrivacyGateway()
    service = LLMService(privacy_gateway=privacy, provider=SingleCallStoryboardLLM(settings, diagnostic_directory=store.directory))
    return WhiteboardPipeline(
        storyboard=LLMStoryboardProvider(service, model=payload.models.storyboard),
        images=ClaudeHubGeminiImageProvider(api_key=settings.xai_api_key, base_url=settings.xai_base_url, model=payload.models.image),
        speech=OpenRouterSpeechProvider(api_key=settings.openrouter_api_key, model=payload.models.speech, proxy_url=settings.whiteboard_proxy_url),
        regions=OpenRouterRegionProvider(api_key=settings.openrouter_api_key, model=payload.models.regions, proxy_url=settings.whiteboard_proxy_url),
        renderer=MarkerSweepRenderer(), sanitizer=privacy, checkpoints=store,
        visual_review=payload.visual_review, max_api_calls=payload.max_api_calls,
        critic=OpenRouterVisualCritic(api_key=settings.openrouter_api_key, model=payload.models.regions,
            proxy_url=settings.whiteboard_proxy_url) if payload.visual_review else None,
        visual_author=LLMVisualAuthor(service, model=payload.models.storyboard) if payload.visual_review else None)


def _append_error_log(directory, entry):
    """Durable per-job JSON lines next to the checkpoints; never secrets or provider text."""
    if directory is None:
        return
    try:
        with open(directory / 'errors.jsonl', 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except OSError:
        logging.getLogger(__name__).warning('whiteboard_error_log_failed')


def _failure(session_factory, product_id, task_id, code, *, exception=None, directory=None, auto_resume_attempts=0):
    with session_factory() as db:
        task = owner_task(db, product_id, task_id, lock=True)
        if task.status == TaskStatus.CANCELLED:
            code = 'cancelled'
        else:
            task.status = TaskStatus.FAILED
        state = db.scalar(select(Artifact).where(Artifact.task_id == task.id,
                                                Artifact.kind == 'whiteboard_job_state'))
        if state:
            value = json.loads(state.content)
            value['failed_stage'] = value.get('stage')
            details = {}
            if exception is not None:
                details = {'exception_type': type(exception).__name__}
                from pydantic import ValidationError
                import httpx
                if isinstance(exception, ValidationError):
                    details['validation_types'] = sorted({e['type'] for e in exception.errors(include_input=False)})
                if isinstance(exception, httpx.HTTPStatusError):
                    details['http_status'] = exception.response.status_code
                if type(exception) in (ValueError, CheckpointError):
                    # Our own fixed messages; never provider responses or validation input.
                    details['reason'] = str(exception)[:200]
                value['diagnostic'] = details
            entry = record_error(value, code, details=details)
            refund_unanswered(value)
            value.update(stage='failed', error=code)
            retried = False
            if code != 'cancelled' and auto_resume_attempts:
                try:
                    source, _, approval, payload = records(db, task, lock=True)
                except ReadinessError:
                    approval = None
                if approval is not None and approval.status == 'approved':
                    retried = schedule_auto_resume(db, task, source, value, payload, code,
                                                   attempts=auto_resume_attempts)
            entry['auto_resume'] = value.get('retry_at') if retried else None
            logging.getLogger(__name__).warning('whiteboard_failed %s', json.dumps({
                'task_id': str(task_id), **entry}))
            _append_error_log(directory, {'task_id': str(task_id), **entry})
            state.content = json.dumps(value)
        db.execute(update(AgentRun).where(AgentRun.task_id == task_id, AgentRun.status == 'running')
                   .values(status='failed', error=code, finished_at=datetime.now(timezone.utc)))
        db.commit()


def recover_interrupted_jobs(session_factory, settings):
    # A live process holds this filesystem lock for the entire job. A short
    # claim grace avoids racing the interval between SQL claim and flock.
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=90)
    with session_factory() as db:
        rows = list(db.execute(select(Task.id, Task.product_id).where(
            Task.assigned_agent == AGENT, Task.status == TaskStatus.RUNNING,
            Task.updated_at < cutoff).limit(20)))
    for task_id, product_id in rows:
        directory = job_directory(settings.products_root, product_id, task_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with _Lock(directory / 'job.lock'):
                _failure(session_factory, product_id, task_id, 'interrupted', directory=directory,
                         auto_resume_attempts=settings.whiteboard_auto_resume_attempts)
        except CheckpointError:
            continue


async def run_whiteboard_once(session_factory, settings, *, factory=None, readiness=None):
    recover_interrupted_jobs(session_factory, settings)
    with session_factory() as db:
        queued = list(db.scalars(select(Task).where(Task.assigned_agent == AGENT, Task.status == TaskStatus.QUEUED)
                                 .order_by(Task.priority.desc(), Task.created_at, Task.id).limit(20)))
        task = None
        for candidate in queued:
            # A job waiting out its automatic-retry pause stays queued until the pause ends.
            saved = db.scalar(select(Artifact.content).where(Artifact.task_id == candidate.id,
                                                             Artifact.kind == 'whiteboard_job_state'))
            if saved is None or retry_due(json.loads(saved)):
                task = candidate
                break
        if task is None:
            return 0
        task_id, product_id = task.id, task.product_id
        claimed = db.execute(update(Task).where(Task.id == task_id, Task.status == TaskStatus.QUEUED)
                             .values(status=TaskStatus.RUNNING, updated_at=datetime.now(timezone.utc)))
        if claimed.rowcount != 1:
            db.rollback(); return 0
        db.commit()
    directory = None
    try:
        directory = job_directory(settings.products_root, product_id, task_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        with _Lock(directory / 'job.lock'):
            with session_factory() as db:
                task = owner_task(db, product_id, task_id)
                source, state, approval, payload = records(db, task)
                if approval.status != 'approved' or source.status != ArtifactStatus.APPROVED:
                    raise ReadinessError('approval_invalid')
                value = json.loads(state.content); value.update(stage='preflight', error=None)
                for key in ('retry_at', 'failed_stage', 'diagnostic'):
                    value.pop(key, None)
                resuming = bool(value.pop('resume_requested', False))
                refund_unanswered(value)
                state.content = json.dumps(value); db.commit()
            # The immutable approved request owns this job's model/voice choice.
            # Copy only these public fields; never alter shared settings or .env.
            job_settings = scoped_settings(settings, payload.models, payload.request.voice)
            await (readiness or preflight)(job_settings, payload.models, payload.request.voice)
            store = CheckpointStore(directory / 'stages', payload.request, models=payload.models.model_dump(),
                                    visual_review=payload.visual_review,
                                    image_provider='claudehub' if payload.image_provider == 'claudehub' else None)
            if resuming:
                # The owner confirmed a paid continuation of the failed stage.
                store.reset_unfinished()
            if payload.visual_repair_from is not None:
                from .repair import prime_visual_repair
                try:
                    await prime_visual_repair(session_factory, job_settings, product_id, payload, store)
                except (CheckpointError, ValueError):
                    raise ReadinessError('repair_source_invalid') from None
            pipeline = (factory or build_pipeline)(job_settings, payload, store)
            pipeline.cached_audio_only = payload.visual_repair_from is not None
            def progress(update):
                with session_factory() as db:
                    current = owner_task(db, product_id, task_id, lock=True)
                    _, saved, approved, checked = records(db, current, lock=True)
                    if current.status != TaskStatus.RUNNING or approved.status != 'approved' or checked != payload:
                        raise ReadinessError('cancelled' if current.status == TaskStatus.CANCELLED else 'approval_invalid')
                    value = json.loads(saved.content)
                    value.update(update)
                    saved.content = json.dumps(value)
                    db.commit()
            pipeline.progress = progress
            ledger = CallLedger(session_factory, product_id, task_id)
            for attribute, stage in (('storyboard', 'storyboard'), ('images', 'image'), ('speech', 'speech'), ('regions', 'regions')):
                setattr(pipeline, attribute, GuardedStage(getattr(pipeline, attribute), stage, ledger))
            if payload.visual_review:
                if not pipeline.visual_review:
                    raise ReadinessError('approval_invalid')
                pipeline.revision_images = GuardedStage(pipeline.images.provider, 'image_revision', ledger)
                pipeline.critic = GuardedStage(pipeline.critic, 'visual_review', ledger)
                pipeline.visual_author = GuardedStage(pipeline.visual_author, 'visual_decision', ledger)
            output_dir = directory / 'output'
            output_dir.mkdir(exist_ok=True, mode=0o700)
            # Up to ~19 sequential provider calls of several minutes each (twice as many for the one-minute video).
            async with asyncio.timeout(3600 if payload.request.scenes == 3 else 7200):
                output = await pipeline.produce(payload.request, output_dir / 'video.mp4', approval_verified=True)
            with session_factory() as db:
                task = owner_task(db, product_id, task_id, lock=True)
                source, state, approval, checked = records(db, task, lock=True)
                if task.status != TaskStatus.RUNNING or approval.status != 'approved' or checked != payload:
                    raise ReadinessError('cancelled' if task.status == TaskStatus.CANCELLED else 'approval_invalid')
                result = Artifact(product_id=product_id, task_id=task_id, kind='whiteboard_video',
                    title='Рисованное видео: ' + payload.request.topic[:180], status=ArtifactStatus.AWAITING_APPROVAL,
                    content=json.dumps({'task_id': str(task_id), 'duration_seconds': output.duration_seconds,
                        'scene_count': output.scene_count, 'storyboard': output.storyboard.model_dump(),
                        'cost_usd': None, 'cost_known': False, 'human_review_required': True,
                        'visual_review': output.visual_review}, ensure_ascii=False))
                db.add(result); db.flush()
                value = json.loads(state.content); value.update(stage='completed', error=None, artifact_id=str(result.id))
                if value.get('progress_total') is not None:
                    value['progress_done'] = value['progress_total']
                state.content = json.dumps(value)
                task.status = TaskStatus.COMPLETED
                db.commit()
    except asyncio.CancelledError:
        _failure(session_factory, product_id, task_id, 'interrupted', directory=directory,
                 auto_resume_attempts=settings.whiteboard_auto_resume_attempts)
        raise
    except Exception as exc:
        import httpx
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                SingleCallStoryboardLLM(settings, diagnostic_directory=directory / 'stages').record_response(
                    exc.response, name='provider-error')
            except Exception:
                logging.getLogger(__name__).warning('whiteboard_diagnostic_capture_failed task_id=%s', task_id)
        _failure(session_factory, product_id, task_id, safe_error(exc), exception=exc, directory=directory,
                 auto_resume_attempts=settings.whiteboard_auto_resume_attempts)
    return 1
