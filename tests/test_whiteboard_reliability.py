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
