"""Regression coverage for errors seen in the 32-failure production video.

All models/media are mocked, no paid API requests are made.
"""
import json

import httpx
import pytest
from sqlalchemy import select

from app.api.whiteboard import job_html
from app.content.whiteboard.jobs import (
    CallLedger, can_resume, can_top_up, owner_task, records, resume_job,
)
from app.content.whiteboard.review import VisualDecision
from app.content.whiteboard.runtime import ReadinessError
from app.content.whiteboard.worker import run_whiteboard_once
from app.models.domain import AgentRun, TaskStatus
from test_whiteboard_jobs import approve, environment, fake_factory, new_job, ready
from test_whiteboard_visual_review import issue, review


def test_author_normalizes_nested_grouped_and_direct_decisions_without_rewriting_content():
    report = review(issue(), issue(1, category='style'))
    text = json.dumps({'review': {
        'accepted': [{
            'issue_id': 's0-hook', 'scene_index': 0, 'beat_index': 0,
            'severity': 'major', 'category': 'mechanics', 'confidence': 'high',
            'reason': 'Исправление существенной механики рисунка обязательно.',
            'correction': 'Показать крюк под хвостом самолёта.',
        }],
        'rejected': [{
            'issue_id': 's1-hook', 'scene_index': 1, 'beat_index': 0,
            'reason': 'Стилистическую правку принимать не следует.',
        }],
    }}, ensure_ascii=False)
    value = VisualDecision.from_author_json('```json\n' + text + '\n```', report)
    assert [i.accept for i in value.decisions] == [True, False]
    assert value.accepted(report, 0) == (report.issues[0],)
    assert value.accepted(report, 1) == ()
    direct = {'decisions': [
        {'issue_id': 's0-hook', 'decision': 'reject',
         'reason': 'Недостаточно оснований изменять этот рисунок.'},
        {'issue_id': 's1-hook', 'decision': 'reject',
         'reason': 'Незначительное замечание не требует перерисовки.'},
    ]}
    assert not any(x.accept for x in VisualDecision.from_author_json(json.dumps(direct), report).decisions)
    groups = {'accepted': [], 'rejected': direct['decisions']}
    # Grouped format requires group membership alone, without duplicating
    # contradictory decision flags from another schema.
    for item in groups['rejected']:
        item.pop('decision')
    assert not any(x.accept for x in VisualDecision.from_author_json(json.dumps(groups), report).decisions)


@pytest.mark.parametrize('change', ['unknown_field', 'changed_scene', 'contradictory', 'accept_style', 'missing_issue'])
def test_author_normalization_rejects_ambiguous_or_scope_expanding_instructions(change):
    report = review(issue(), issue(1, category='style'))
    body = {'decisions': [
        {'issue_id': 's0-hook', 'accept': False, 'reason': 'Оставляем оригинал без новых рисунков.'},
        {'issue_id': 's1-hook', 'accept': False, 'reason': 'Стилистические изменения не обязательны.'},
    ]}
    if change == 'unknown_field':
        body['decisions'][0]['new_narration'] = 'Rewrite everything and expand scope'
    elif change == 'changed_scene':
        body['decisions'][0]['scene_index'] = 2
    elif change == 'contradictory':
        body['decisions'][0]['decision'] = 'accept'
    elif change == 'accept_style':
        body['decisions'][1]['accept'] = True
    elif change == 'missing_issue':
        body['decisions'].pop()
    with pytest.raises(ValueError):
        VisualDecision.from_author_json(json.dumps(body), report)


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [429, 500, 502, 503, 504])
async def test_region_service_error_finishes_video_with_local_layout_and_no_repeat(environment, status):
    factory, settings, product_id, _ = environment
    task_id = new_job(environment)
    approve(environment, task_id)
    calls = []

    def build(settings, payload, store):
        pipeline = fake_factory(settings, payload, store)
        class Regions:
            async def detect(self, *args):
                calls.append(1)
                request = httpx.Request('POST', 'https://provider.example/v1/chat/completions')
                raise httpx.HTTPStatusError('provider unavailable', request=request,
                    response=httpx.Response(status, request=request))
        pipeline.regions = Regions()
        return pipeline

    assert await run_whiteboard_once(factory, settings, factory=build, readiness=ready) == 1
    with factory() as db:
        task = owner_task(db, product_id, task_id)
        assert task.status == TaskStatus.COMPLETED
        state = json.loads(records(db, task)[1].content)
        assert state['attempts_started'] == state['calls'] == 13
        assert len(calls) == 3
        repairs = state['region_layout']
        assert len(repairs) == 3
        assert all(item['operation'] == 'offline_fallback_region_split'
                   and item['provider_http_status'] == status
                   and item['billing_status'] == 'unknown'
                   and item['attempted_provider_calls'] == 1 for item in repairs)
        assert not state.get('errors')
        failed_runs = list(db.scalars(select(AgentRun).where(
            AgentRun.task_id == task_id, AgentRun.status == 'failed')))
        assert len(failed_runs) == 3
        page = job_html({**state, 'status': task.status.value, 'task_id': str(task_id),
            'request': {'topic': 'Видео с резервной разметкой', 'scenes': 3},
            'max_api_calls': 14}, product_id)
        assert 'Проверьте порядок появления элементов' in page


@pytest.mark.asyncio
async def test_auth_error_in_region_provider_is_not_hidden_as_local_success(environment):
    factory, settings, product_id, _ = environment
    task_id = new_job(environment)
    approve(environment, task_id)

    def build(settings, payload, store):
        pipeline = fake_factory(settings, payload, store)
        class Regions:
            async def detect(self, *args):
                request = httpx.Request('POST', 'https://provider.example/v1/chat/completions')
                raise httpx.HTTPStatusError('access forbidden', request=request,
                    response=httpx.Response(403, request=request))
        pipeline.regions = Regions()
        return pipeline

    assert await run_whiteboard_once(factory, settings, factory=build, readiness=ready) == 1
    with factory() as db:
        task = owner_task(db, product_id, task_id)
        assert task.status == TaskStatus.FAILED
        state = json.loads(records(db, task)[1].content)
        assert state['attempts_started'] == 3
        assert state['failed_stage'] == 'regions'
        assert not state.get('region_layout')


def test_raw_attempt_ceiling_survives_502_refund_and_requires_owner_topup(environment):
    factory, _, product_id, _ = environment
    task_id = new_job(environment, maximum=14)
    approve(environment, task_id)
    with factory() as db:
        task = owner_task(db, product_id, task_id)
        task.status = TaskStatus.RUNNING
        db.commit()

    ledger = CallLedger(factory, product_id, task_id)
    for _ in range(14):
        ledger.finish(ledger.begin('speech'))
    with factory() as db:
        task = owner_task(db, product_id, task_id)
        _, state, _, payload = records(db, task)
        value = json.loads(state.content)
        assert value['attempts_started'] == value['calls'] == 14
        value['calls'] = 10  # Simulates 4 historical HTTP 502 refunds.
        state.content = json.dumps(value)
        db.commit()
    with pytest.raises(ReadinessError, match='call_limit'):
        ledger.begin('speech')
    with factory() as db:
        task = owner_task(db, product_id, task_id)
        _, state, _, payload = records(db, task)
        task.status = TaskStatus.FAILED
        value = json.loads(state.content)
        value.update(stage='failed', error='call_limit', failed_stage='speech')
        state.content = json.dumps(value)
        assert not can_resume(task.status, value, payload)
        assert can_top_up(task.status, value, payload)
        db.commit()
    with factory() as db:
        resume_job(db, product_id, task_id, confirmed=True,
                   decided_by='owner', extra_calls=3)
        task = owner_task(db, product_id, task_id)
        _, state, _, _ = records(db, task)
        value = json.loads(state.content)
        assert value['attempts_started'] == 14 and value['extra_calls'] == 3
        task.status = TaskStatus.RUNNING
        db.commit()
    for _ in range(3):
        ledger.finish(ledger.begin('speech'))
    with pytest.raises(ReadinessError, match='call_limit'):
        ledger.begin('speech')


def test_whiteboard_worker_disables_hidden_text_provider_retries(environment, tmp_path):
    """Each storyboard/author ledger call must contain at most one HTTP POST."""
    from app.content.whiteboard.checkpoints import CheckpointStore
    from app.content.whiteboard.jobs import JobRequest
    from app.content.whiteboard.runtime import configured_models
    from app.content.whiteboard.worker import build_pipeline
    from test_whiteboard import request

    _, settings, product_id, _ = environment
    req = request(product_id=product_id)
    chosen = configured_models(settings).model_copy(
        update={'image': 'gemini-3.1-flash-image'})
    payload = JobRequest(request=req, models=chosen, max_api_calls=25,
                         image_provider='claudehub', visual_review=True)
    store = CheckpointStore(tmp_path / 'protected', req,
                            models=chosen.model_dump(), visual_review=True,
                            image_provider='claudehub')
    pipeline = build_pipeline(settings, payload, store)
    provider = pipeline.storyboard.service._provider
    assert provider.allow_fallback is False
    assert provider.allow_ceiling_retry is False
    assert pipeline.visual_author.service._provider is provider


@pytest.mark.asyncio
async def test_resumed_offline_region_audit_reaches_final_video_artifact(environment):
    """A cached region plan must retain its fallback warning in the final result."""
    from app.content.whiteboard.jobs import resume_job
    from app.models.domain import Artifact
    from test_whiteboard_visual_review import pipeline_factory, review, reviewed_job

    factory, original_settings, product_id, _ = environment
    settings = original_settings.model_copy(update={'whiteboard_auto_resume_attempts': 0})
    task_id = reviewed_job(environment, maximum=19)
    approve(environment, task_id)
    failed_speech_once = True
    region_attempts = 0

    def build(settings, payload, store):
        nonlocal failed_speech_once, region_attempts
        pipeline = pipeline_factory([], review())(settings, payload, store)
        original_regions = pipeline.regions
        original_speech = pipeline.speech

        class Regions:
            async def detect(self, scene, image):
                nonlocal region_attempts
                region_attempts += 1
                if region_attempts == 1:
                    request = httpx.Request('POST', 'https://provider.example/v1/chat/completions')
                    raise httpx.HTTPStatusError('temporary HTTP 502', request=request,
                                                response=httpx.Response(502, request=request))
                return await original_regions.detect(scene, image)

        class Speech:
            async def synthesize(self, *args, **kwargs):
                nonlocal failed_speech_once
                if failed_speech_once:
                    failed_speech_once = False
                    raise RuntimeError('synthetic interrupted speech')
                return await original_speech.synthesize(*args, **kwargs)

        pipeline.regions = Regions()
        pipeline.speech = Speech()
        return pipeline

    assert await run_whiteboard_once(factory, settings, factory=build, readiness=ready) == 1
    with factory() as db:
        task = owner_task(db, product_id, task_id)
        assert task.status == TaskStatus.FAILED
        value = json.loads(records(db, task)[1].content)
        assert value['visual_review_result']['region_layout'][0]['provider_http_status'] == 502
        resume_job(db, product_id, task_id, confirmed=True, decided_by='owner')

    assert await run_whiteboard_once(factory, settings, factory=build, readiness=ready) == 1
    with factory() as db:
        task = owner_task(db, product_id, task_id)
        assert task.status == TaskStatus.COMPLETED
        state = json.loads(records(db, task)[1].content)
        video = db.scalar(select(Artifact).where(
            Artifact.task_id == task_id, Artifact.kind == 'whiteboard_video'))
        assert video is not None
        artifact_audit = json.loads(video.content)['visual_review']['region_layout']
        assert artifact_audit == state['visual_review_result']['region_layout']
        assert len(artifact_audit) == 1
        assert artifact_audit[0]['scene_index'] == 0
        assert artifact_audit[0]['provider_http_status'] == 502
        assert artifact_audit[0]['operation'] == 'offline_fallback_region_split'
        assert region_attempts == 3  # Cached scene 0 region is not billed twice.


def test_region_ledger_records_claudehub_instead_of_openrouter(environment):
    from app.content.whiteboard.contracts import WhiteboardRequest
    from app.content.whiteboard.jobs import queue_job
    from app.content.whiteboard.runtime import configured_models

    factory, settings, product_id, _ = environment
    models = configured_models(settings).model_copy(update={
        'image': 'gemini-3.1-flash-image', 'regions': 'gemini-flash-3.8'})
    with factory() as db:
        task_id = queue_job(db, WhiteboardRequest(
            product_id=product_id, topic='Проверяем журнал разметки', voice='Kore'),
            models, max_api_calls=14, image_provider='claudehub').id
    approve(environment, task_id)
    with factory() as db:
        owner_task(db, product_id, task_id).status = TaskStatus.RUNNING
        db.commit()

    ledger = CallLedger(factory, product_id, task_id)
    run_id = ledger.begin('regions')
    with factory() as db:
        run = db.get(AgentRun, run_id)
        assert run.provider == 'claudehub'
        assert run.model == 'gemini-flash-3.8'
    ledger.finish(run_id)
