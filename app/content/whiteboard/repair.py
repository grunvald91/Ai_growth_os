"""Owner-approved visual-only repair: immutable story and cached MP3s, never TTS."""
import hashlib
import json
from uuid import UUID

from sqlalchemy import select

from app.models.domain import Task, TaskStatus, Product, Artifact, ArtifactStatus, Approval
from .checkpoints import CheckpointStore
from .contracts import WhiteboardStoryboard
from .jobs import AGENT, ACTION, JobRequest, digest, records, owner_task, job_directory
from .runtime import ReadinessError


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


def queue_visual_repair(db, settings, product_id, source_id):
    db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    source, value, original, store = source_store(db, settings, product_id, source_id)
    title = 'WHITEBOARD-VISUAL-REPAIR: ' + str(source_id)
    # One repair per original, including closed/failed jobs; no concealed paid restart.
    existing = db.scalar(select(Task).where(Task.product_id == product_id, Task.assigned_agent == AGENT, Task.title == title))
    if existing:
        return existing
    if original.max_api_calls - value['calls'] < 8:
        raise ReadinessError('repair_budget')
    story = WhiteboardStoryboard.model_validate_json(store.load('storyboard', limit=64_000))
    audio_hashes = []
    for index, scene in enumerate(story.scenes):
        store.load(f'scene-{index:02}-image')
        for beat in range(len(scene.beats)):
            audio = store.load(f'scene-{index:02}-beat-{beat:02}-speech', limit=10_000_000)
            audio_hashes.append({'scene_index': index, 'beat_index': beat, 'sha256': hashlib.sha256(audio).hexdigest()})
    payload = JobRequest(request=original.request, models=original.models.model_copy(update={'image':'gemini-3.1-flash-image'}),
        max_api_calls=original.max_api_calls, visual_review=True, visual_repair_from=UUID(str(source_id)),
        image_provider='claudehub',
        repair_source_digest=digest(source.content), repair_manifest_sha256=hashlib.sha256((store.directory/'manifest.json').read_bytes()).hexdigest(),
        inherited_calls=value['calls'])
    content = payload.model_dump_json()
    task = Task(product_id=product_id, assigned_agent=AGENT, title=title, priority=50,
        status=TaskStatus.WAITING_APPROVAL, description='Переделать рисунки, сохранив исходную озвучку: '+original.request.topic)
    db.add(task);db.flush()
    artifact = Artifact(product_id=product_id, task_id=task.id, kind='whiteboard_request',
        title='Визуальная правка без повторной озвучки', content=content, status=ArtifactStatus.AWAITING_APPROVAL)
    db.add(artifact);db.flush()
    db.add(Artifact(product_id=product_id, task_id=task.id, kind='whiteboard_job_state', title='Ход визуальной правки',
        content=json.dumps({'calls':value['calls'], 'stage':'waiting_approval', 'error':None,
            'cost_usd':None, 'cost_known':False, 'request_digest':digest(content),
            'audio_reused_from':str(source_id), 'audio_sha256':audio_hashes, 'additional_speech_calls':0})))
    db.add(Approval(product_id=product_id, artifact_id=artifact.id, action_type=ACTION,
        risk_level='YELLOW', status='pending', requested_by='owner', reason='sha256:'+digest(content)))
    db.commit()
    return task


async def prime_visual_repair(session_factory, settings, product_id, payload, destination):
    with session_factory() as db:
        source, value, original, store = source_store(db, settings, product_id, payload.visual_repair_from)
        if (digest(source.content) != payload.repair_source_digest
                or hashlib.sha256((store.directory/'manifest.json').read_bytes()).hexdigest() != payload.repair_manifest_sha256
                or original.request != payload.request
                or original.models.model_copy(update={'image':'gemini-3.1-flash-image'}) != payload.models
                or payload.image_provider != 'claudehub'
                or value['calls'] != payload.inherited_calls):
            raise ReadinessError('repair_source_invalid')
        story = WhiteboardStoryboard.model_validate_json(store.load('storyboard',limit=64_000))
        async def saved_story():return story
        await destination.json_stage('storyboard',saved_story,WhiteboardStoryboard)
        destination.record_import('storyboard',source_job_id=payload.visual_repair_from,source_model_id=original.models.storyboard)
        for index,scene in enumerate(story.scenes):
            for name,limit in [(f'scene-{index:02}-image',12_000_000),
                               *[(f'scene-{index:02}-beat-{beat:02}-speech',10_000_000) for beat in range(len(scene.beats))]]:
                data = store.load(name,limit=limit)
                async def saved():return data
                await destination.bytes_stage(name,saved,limit=limit)
                destination.record_import(name,source_job_id=payload.visual_repair_from,
                    source_model_id=original.models.speech if name.endswith('-speech') else original.models.image)
