"""Job choices, measured stage progress and recoverable owner forms; no APIs."""
import json
from uuid import UUID

import pytest
from sqlalchemy import select

from app.api import whiteboard as api
from app.content.whiteboard.jobs import owner_task, records
from app.content.whiteboard.runtime import configured_models
from app.content.whiteboard.worker import run_whiteboard_once
from app.models.domain import AgentRun, Task
from test_whiteboard_jobs import environment, client, ready, fake_factory, new_job, approve


def test_ui_profile_is_bound_to_review_and_approval_without_changing_global_settings(environment, monkeypatch):
    factory, settings, product_id, _ = environment
    original = settings.model_dump()
    inspected = []
    async def inspect(scoped, models, voice):
        inspected.append(models)
        assert scoped is not settings
        assert configured_models(scoped) == models
        assert models.image == 'gemini-3.1-flash-image'
        assert models.speech == 'x-ai/grok-voice-tts-1.0' and voice == scoped.whiteboard_voice == 'eve'
        assert scoped.openrouter_api_key == settings.openrouter_api_key
        return {'ready': True}
    monkeypatch.setattr(api, 'preflight', inspect)
    with client() as browser:
        created = browser.post(f'/products/{product_id}/whiteboard/jobs',
            json={'topic': 'Почему самолёт садится на авианосец', 'profile': 'gemini_ru'})
        assert created.status_code == 201
        state = created.json()
        assert state['calls'] == 0 and state['approval_status'] == 'pending'
        task_id = UUID(state['task_id'])
        page = browser.get(f'/studio/products/{product_id}/whiteboard/{task_id}')
        assert 'gemini-3.1-flash-image' in page.text and 'eve' in page.text
        assert 'video controls' not in page.text
        approval = browser.post(f'/products/{product_id}/whiteboard/jobs/{task_id}/approve',
            json={'request_digest': state['request_digest'], 'confirmed': True})
        assert approval.status_code == 200 and approval.json()['status'] == 'queued'
        assert len(inspected) == 1 and settings.model_dump() == original
        bad = browser.post(f'/products/{product_id}/whiteboard/jobs',
            json={'topic': 'Другая тема анимации', 'profile': 'arbitrary-provider'})
        assert bad.status_code == 422
    with factory() as db:
        assert len(list(db.scalars(select(Task)))) == 1
        assert list(db.scalars(select(AgentRun))) == []


@pytest.mark.asyncio
async def test_worker_reports_local_rendering_and_does_not_spend_an_extra_call(environment):
    factory, settings, product_id, _ = environment
    task_id = new_job(environment)
    approve(environment, task_id)
    observed = []
    def build(scoped, payload, store):
        pipeline = fake_factory(scoped, payload, store)
        original = pipeline.renderer
        class InspectRendering:
            async def render(self, scenes, destination, request):
                with factory() as db:
                    state = json.loads(records(db, owner_task(db, product_id, task_id))[1].content)
                    assert state['stage'] == 'rendering'
                    assert state['progress_done'] == 13 and state['progress_total'] == 14
                    assert state['calls'] == 13
                    assert state['scene_index'] is None and state['beat_index'] is None
                    observed.append(state)
                return await original.render(scenes, destination, request)
        pipeline.renderer = InspectRendering()
        return pipeline
    assert await run_whiteboard_once(factory, settings, factory=build, readiness=ready) == 1
    assert len(observed) == 1
    with factory() as db:
        state = api.job_state(db, product_id, task_id)
        assert state['status'] == 'completed' and state['progress_done'] == state['progress_total'] == 14
        assert state['calls'] == 13


def test_invalid_owner_form_retains_escaped_topic_and_selected_settings(environment):
    factory, _, product_id, _ = environment
    topic = '</textarea><script>alert(1)</script>'
    with client() as browser:
        response = browser.post(f'/studio/products/{product_id}/whiteboard',
            data={'topic': topic, 'max_api_calls': '12', 'profile': 'gemini_ru'})
        assert response.status_code == 422 and 'text/html' in response.headers['content-type']
        assert '&lt;/textarea&gt;&lt;script&gt;alert(1)&lt;/script&gt;' in response.text
        assert topic not in response.text and 'value="gemini_ru" selected' in response.text
    with factory() as db:
        assert list(db.scalars(select(Task))) == []


def test_index_exposes_return_path_and_russian_job_status(environment, monkeypatch):
    _, _, product_id, _ = environment
    task_id = new_job(environment)
    async def should_not_contact_provider(*args):
        raise AssertionError('Listing saved animations must not contact model providers')
    monkeypatch.setattr(api, 'preflight', should_not_contact_provider)
    with client() as browser:
        response = browser.get(f'/studio/products/{product_id}/whiteboard')
        assert response.status_code == 200 and 'Ваши анимации' in response.text
        assert 'Ожидает подтверждения' in response.text and f'/whiteboard/{task_id}' in response.text
        assert 'value="gemini_ru" selected' in response.text
        assert 'Скачать MP4</a>' not in response.text
        assert 'Refresh' not in response.headers


def test_failure_identifies_stage_without_downloads_or_automatic_paid_retry(environment):
    _, _, product_id, _ = environment
    body = api.job_html({'task_id': str(product_id), 'request': {'topic': 'Тестовый ролик'},
        'status': 'failed', 'stage': 'failed', 'failed_stage': 'rendering',
        'error': 'generation_failed', 'calls': 13, 'max_api_calls': 13,
        'progress_done': 13, 'progress_total': 14}, product_id)
    assert 'Ошибка на этапе: Сборка MP4' in body
    assert 'Автоматического повтора нет' in body
    assert 'Скачать MP4' not in body and '<video' not in body
