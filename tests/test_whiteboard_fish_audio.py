import json

import httpx
import pytest

from app.core.config import Settings
from app.content.whiteboard.adapters import FishSpeechProvider
from app.content.whiteboard.runtime import (
    FISH_SPEECH_MODEL, ReadinessError, generation_profile, preflight,
    scoped_settings, safe_error,
)


def settings():
    return Settings(_env_file=None, fish_api_key='fish-test-key',
                    openrouter_api_key=None, xai_api_key='hub-test-key',
                    xai_base_url='https://api.claudehub.fun/v1',
                    whiteboard_storyboard_model='kimi-2.7')


def test_default_with_fish_key_and_explicit_grok():
    s = settings()
    models, voice = generation_profile(s, 'gemini_ru')
    assert models.speech == FISH_SPEECH_MODEL and voice == 'default'
    assert generation_profile(s, 'fish') == (models, voice)
    assert generation_profile(s, 'grok')[0].speech == 'x-ai/grok-voice-tts-1.0'


@pytest.mark.asyncio
async def test_fish_posts_once_and_never_uses_paid_default():
    calls = []
    def handler(req):
        calls.append(req)
        assert req.url.path == '/v1/tts'
        assert req.headers['authorization'] == 'Bearer fish-test-key'
        assert req.headers['model'] == 's2.1-pro-free'
        assert json.loads(req.content) == {'text': 'Привет!', 'format': 'mp3'}
        return httpx.Response(200, content=b'ID3' + b'x' * 200)
    async with httpx.AsyncClient(base_url='https://api.fish.audio/', transport=httpx.MockTransport(handler)) as c:
        provider = FishSpeechProvider(api_key='fish-test-key', model=FISH_SPEECH_MODEL, client=c)
        assert (await provider.synthesize('Привет!', language='ru', voice='default')).startswith(b'ID3')
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('status, code', [(401, 'fish_key_invalid'), (402, 'fish_request_rejected'), (429, 'fish_unavailable'), (503, 'fish_unavailable')])
async def test_fish_error_is_not_openrouter_error(status, code):
    async with httpx.AsyncClient(base_url='https://api.fish.audio/', transport=httpx.MockTransport(lambda req: httpx.Response(status))) as c:
        provider = FishSpeechProvider(api_key='fish-test-key', model=FISH_SPEECH_MODEL, client=c)
        with pytest.raises(httpx.HTTPStatusError) as err:
            await provider.synthesize('Привет!', language='ru', voice='default')
        assert safe_error(err.value) == code


@pytest.mark.asyncio
async def test_fish_preflight_does_not_contact_openrouter():
    s = settings()
    models, voice = generation_profile(s, 'fish')
    s = scoped_settings(s, models, voice)
    def hub(req):
        return httpx.Response(200, json={'data': [{'id': models.image}, {'id': models.regions}]})
    def fish(req):
        assert req.method == 'GET' and req.url.path == '/wallet/self/api-credit'
        assert req.headers['authorization'] == 'Bearer fish-test-key'
        return httpx.Response(200, json={'credit': 0})
    def forbidden(req):
        pytest.fail('OpenRouter must not be contacted')
    async with (
        httpx.AsyncClient(base_url='https://api.claudehub.fun/v1/', transport=httpx.MockTransport(hub)) as h,
        httpx.AsyncClient(base_url='https://api.fish.audio/', transport=httpx.MockTransport(fish)) as f,
        httpx.AsyncClient(base_url='https://openrouter.ai/api/v1/', transport=httpx.MockTransport(forbidden)) as o,
    ):
        assert (await preflight(s, models, voice, client=o, claudehub_client=h, fish_client=f))['ready']
        with pytest.raises(ReadinessError, match='configuration_missing'):
            await preflight(s.model_copy(update={'fish_api_key': None}), models, voice,
                            client=o, claudehub_client=h, fish_client=f)


from test_whiteboard_jobs import environment, client, approve


def test_owner_switch_preserves_original_approval_and_routes_ledger(environment):
    from uuid import UUID
    from app.content.whiteboard.jobs import (
        CallLedger, digest, records, owner_task, resume_with_fish, effective_speech,
    )
    from app.models.domain import AgentRun, TaskStatus
    factory, original, product, other = environment
    s = original.model_copy(update={'fish_api_key': 'test-key'})
    with client() as browser:
        response = browser.post(f'/products/{product}/whiteboard/jobs',
                                json={'topic': 'Продолжение видео с новой озвучкой'})
        assert response.status_code == 201
        task_id = UUID(response.json()['task_id'])
    approve(environment, task_id)
    with factory() as db:
        task = owner_task(db, product, task_id)
        source, state, approval, payload = records(db, task)
        old_content = source.content
        old_approval = approval.reason
        value = json.loads(state.content)
        value.update(stage='failed', error='openrouter_forbidden', failed_stage='speech')
        state.content = json.dumps(value)
        task.status = TaskStatus.FAILED
        db.commit()
        with pytest.raises(ReadinessError):
            resume_with_fish(db, product, task_id, confirmed=False, request_digest=digest(old_content),
                             decided_by='owner', settings=s)
        with pytest.raises(ReadinessError):
            resume_with_fish(db, product, task_id, confirmed=True, request_digest='0'*64,
                             decided_by='owner', settings=s)
        resume_with_fish(db, product, task_id, confirmed=True, request_digest=digest(old_content),
                         decided_by='owner', settings=s)
        task = owner_task(db, product, task_id)
        source, state, approval, payload = records(db, task)
        value = json.loads(state.content)
        assert source.content == old_content and approval.reason == old_approval
        assert effective_speech(db, task, source, value, payload) == (FISH_SPEECH_MODEL, 'default')
        assert value['resume_requested'] and task.status == TaskStatus.QUEUED
        task.status = TaskStatus.RUNNING
        db.commit()
    run_id = CallLedger(factory, product, task_id).begin('speech')
    with factory() as db:
        run = db.get(AgentRun, run_id)
        assert run.model == FISH_SPEECH_MODEL and run.provider == 'fish_audio'


@pytest.mark.asyncio
async def test_voice_override_preserves_checkpoint_request_voice():
    seen = []
    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, content=b'ID3' + b'x' * 200)
    async with httpx.AsyncClient(base_url='https://api.fish.audio/', transport=httpx.MockTransport(handler)) as c:
        provider = FishSpeechProvider(api_key='test', model=FISH_SPEECH_MODEL,
                                      client=c, voice_override='default')
        await provider.synthesize('Привет!', language='ru', voice='eve')
    assert seen == [{'text': 'Привет!', 'format': 'mp3'}]
