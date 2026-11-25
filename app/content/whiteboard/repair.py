"""Owner-approved visual-only repair: immutable story and cached MP3s, never TTS."""
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select

from app.models.domain import Task, TaskStatus, Product, Artifact, ArtifactStatus, Approval
from .checkpoints import CheckpointStore
from .contracts import WhiteboardStoryboard
from .jobs import (AGENT, ACTION, JobRequest, can_resume, can_top_up, digest,
                   records, owner_task, job_directory, started_attempts)
from .runtime import ReadinessError, CLAUDEHUB_VISION_MODEL, repair_image_model, CLAUDEHUB_IMAGE_MODELS
from .science import FactReview, ScienceSources, evidence_supported, requires_science_review


def source_store(db, settings, product_id, source_id):
    task = owner_task(db, product_id, source_id)
    source, state, approval, payload = records(db, task)
    if task.status != TaskStatus.COMPLETED or approval.status != 'approved' or source.status != ArtifactStatus.APPROVED:
        raise ReadinessError('repair_source_invalid')
    if payload.visual_repair_from is not None:
        raise ReadinessError('repair_source_invalid')
    directory = job_directory(settings.products_root, product_id, source_id) / 'stages'
    if not (directory / 'manifest.json').is_file():
        raise ReadinessError('repair_source_invalid')
    store = CheckpointStore(directory, payload.request, models=payload.models.model_dump(), visual_review=payload.visual_review,
                           image_provider='claudehub' if payload.image_provider == 'claudehub' else None)
    return source, json.loads(state.content), payload, store


def queue_visual_repair(db, settings, product_id, source_id, *, image_model=None):
    """Queue a fresh, visible visual regeneration with a separate paid approval.

    The source MP3s/storyboard are immutable; source call history does not consume
    the new job's allowance. Repeated clicks on an active request are idempotent.
    A finished or failed repair does not permanently disable future repairs.
    """
    db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    source, value, original, store = source_store(db, settings, product_id, source_id)
    chosen = repair_image_model(original.models) if image_model is None else image_model
    if chosen not in CLAUDEHUB_IMAGE_MODELS:
        raise ReadinessError('repair_model_invalid')
    prefix = 'WHITEBOARD-VISUAL-REPAIR: ' + str(source_id)
    active_jobs = list(db.scalars(
        select(Task).where(Task.product_id == product_id, Task.assigned_agent == AGENT,
                           Task.title.like(prefix + '%'),
                           Task.status.in_([TaskStatus.WAITING_APPROVAL, TaskStatus.QUEUED, TaskStatus.RUNNING]))
        .order_by(Task.created_at, Task.id).with_for_update()
        .execution_options(populate_existing=True)))
    # Lock candidate tasks before approval artifacts, in the same order as approve_job.
    # Upgrade legacy, unapproved visual repairs instead of reusing an old request
    # that imports the original pictures and shares the exhausted source budget.
    # Never supersede a queued/running or already approved paid job.
    existing = None
    superseded = False
    for candidate in active_jobs:
        if candidate.status == TaskStatus.WAITING_APPROVAL:
            _, pending_state, consent, pending_payload = records(db, candidate, lock=True)
            if consent.status == 'pending' and (not pending_payload.repair_regenerate_images
                    or pending_payload.models.image != chosen):
                consent.status = 'rejected'
                consent.decided_at = datetime.now(timezone.utc)
                consent.decided_by = ('policy:legacy_repair_upgrade' if not pending_payload.repair_regenerate_images
                                      else 'owner:repair_model_changed')
                candidate.status = TaskStatus.CANCELLED
                prior = json.loads(pending_state.content)
                prior.update(stage='cancelled', error='cancelled',
                             superseded_by_new_repair=True)
                pending_state.content = json.dumps(prior)
                superseded = True
                continue
        # Never return or modify an active paid job for a different model.
        _, _, _, current = records(db, candidate, lock=True)
        if current.models.image != chosen:
            raise ReadinessError('repair_model_busy')
        # Repeated clicks must be idempotent for the active replacement.
        existing = candidate
        break
    if existing:
        if superseded:
            db.commit()
        return existing
    # Prefer resuming paid, verified checkpoints instead of buying the same images again.
    failures = db.scalars(
        select(Task).where(Task.product_id == product_id, Task.assigned_agent == AGENT,
                           Task.title.like(prefix + '%'), Task.status == TaskStatus.FAILED)
        .order_by(Task.created_at.desc(), Task.id.desc()))
    for failed in failures:
        _, saved, consent, old_payload = records(db, failed)
        saved_state = json.loads(saved.content)
        if (consent.status == 'approved' and old_payload.repair_regenerate_images
                and old_payload.models.image == chosen
                and (can_resume(failed.status, saved_state, old_payload)
                     or can_top_up(failed.status, saved_state, old_payload))):
            if superseded:
                db.commit()
            return failed
    story = WhiteboardStoryboard.model_validate_json(store.load('storyboard', limit=64_000))
    audio_hashes = []
    for index, scene in enumerate(story.scenes):
        store.load(f'scene-{index:02}-image')
        for beat in range(len(scene.beats)):
            audio = store.load(f'scene-{index:02}-beat-{beat:02}-speech', limit=10_000_000)
            audio_hashes.append({'scene_index': index, 'beat_index': beat,
                                 'sha256': hashlib.sha256(audio).hexdigest()})
    # 1 new image + 1 new region plan + at most 1 revision per scene,
    # and 1 critic + 1 author decision. 3-scene jobs use the v1 minimum of 13.
    repair_limit = max(13, 3 * len(story.scenes) + 2)
    payload = JobRequest(
        request=original.request,
        models=original.models.model_copy(update={'image': chosen,
                                               'regions': CLAUDEHUB_VISION_MODEL}),
        max_api_calls=repair_limit, visual_review=True, visual_repair_from=UUID(str(source_id)),
        image_provider='claudehub', repair_regenerate_images=True, image_recovery=True,
        repair_source_digest=digest(source.content),
        repair_manifest_sha256=hashlib.sha256((store.directory / 'manifest.json').read_bytes()).hexdigest(),
        inherited_calls=value['calls'])
    content = payload.model_dump_json()
    task = Task(product_id=product_id, assigned_agent=AGENT,
                title=prefix + ':' + uuid4().hex[:12], priority=50,
                status=TaskStatus.WAITING_APPROVAL,
                description='Перерисовать изображения без повторной озвучки: ' + original.request.topic)
    db.add(task)
    db.flush()
    artifact = Artifact(product_id=product_id, task_id=task.id, kind='whiteboard_request',
                        title='Перерисовка с исходным аудио', content=content,
                        status=ArtifactStatus.AWAITING_APPROVAL)
    db.add(artifact)
    db.flush()
    db.add(Artifact(product_id=product_id, task_id=task.id, kind='whiteboard_job_state',
                    title='Ход перерисовки',
                    content=json.dumps({'calls': 0, 'attempts_started': 0,
                        'stage': 'waiting_approval', 'error': None,
                        'cost_usd': None, 'cost_known': False, 'request_digest': digest(content),
                        'audio_reused_from': str(source_id), 'audio_sha256': audio_hashes,
                        'source_calls': value['calls'], 'source_attempts_started': started_attempts(value),
                        'additional_speech_calls': 0})))
    db.add(Approval(product_id=product_id, artifact_id=artifact.id, action_type=ACTION,
                    risk_level='YELLOW', status='pending',
                    requested_by='owner', reason='sha256:' + digest(content)))
    db.commit()
    return task


async def reuse_verified_science(original_store, destination, *, request, storyboard,
                                 source_job_id, source_model_id):
    """Import verified review and public evidence without recontacting any providers.

    The source is a completed, immutable, owner-approved video (checked by
    source_store). The science review was part of its original paid approval.
    """
    if not requires_science_review(request):
        return
    original_sources = ScienceSources.model_validate_json(
        original_store.load('science-sources', limit=64_000))
    original_review = FactReview.model_validate_json(
        original_store.load('science-review', limit=64_000))
    if not original_sources.sources or not evidence_supported(original_review, request, storyboard):
        raise ReadinessError('repair_source_invalid')
    async def cached_sources():
        return original_sources
    async def cached_review():
        return original_review
    await destination.json_stage('science-sources', cached_sources, ScienceSources)
    destination.record_import('science-sources', source_job_id=source_job_id,
                              source_model_id='public-evidence')
    await destination.json_stage('science-review', cached_review, FactReview)
    destination.record_import('science-review', source_job_id=source_job_id,
                              source_model_id=source_model_id)


async def prime_visual_repair(session_factory, settings, product_id, payload, destination):
    with session_factory() as db:
        source, value, original, store = source_store(db, settings, product_id, payload.visual_repair_from)
        replacement = {'image': repair_image_model(original.models)}
        if payload.repair_regenerate_images:
            if payload.models.image not in CLAUDEHUB_IMAGE_MODELS:
                raise ReadinessError('repair_model_invalid')
            # The approved immutable repair request may deliberately use a different image model.
            replacement['image'] = payload.models.image
            replacement['regions'] = CLAUDEHUB_VISION_MODEL
        if (digest(source.content) != payload.repair_source_digest
                or hashlib.sha256((store.directory/'manifest.json').read_bytes()).hexdigest() != payload.repair_manifest_sha256
                or original.request != payload.request
                or original.models.model_copy(update=replacement) != payload.models
                or payload.image_provider != 'claudehub'
                or value['calls'] != payload.inherited_calls):
            raise ReadinessError('repair_source_invalid')
        story = WhiteboardStoryboard.model_validate_json(store.load('storyboard',limit=64_000))
        async def saved_story():return story
        await destination.json_stage('storyboard',saved_story,WhiteboardStoryboard)
        destination.record_import('storyboard',source_job_id=payload.visual_repair_from,source_model_id=original.models.storyboard)
        await reuse_verified_science(store, destination, request=payload.request, storyboard=story,
                                     source_job_id=payload.visual_repair_from,
                                     source_model_id=original.models.regions)
        for index,scene in enumerate(story.scenes):
            # A genuine repair must generate NEW images, not cache the old ones.
            # Legacy approved repairs keep their original import behavior.
            for name,limit in ([ ] if payload.repair_regenerate_images else [(f'scene-{index:02}-image',12_000_000)]) + [
                               (f'scene-{index:02}-beat-{beat:02}-speech',10_000_000) for beat in range(len(scene.beats))]:
                data = store.load(name,limit=limit)
                async def saved():return data
                await destination.bytes_stage(name,saved,limit=limit)
                destination.record_import(name,source_job_id=payload.visual_repair_from,
                    source_model_id=original.models.speech if name.endswith('-speech') else original.models.image)
