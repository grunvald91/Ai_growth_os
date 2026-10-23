"""Everything except speech goes through ClaudeHub; OpenRouter is kept for the voice only."""
import json

import httpx
import pytest

from app.content.whiteboard.adapters import (ClaudeHubRegionProvider, OpenRouterRegionProvider,
                                              OpenRouterSpeechProvider, json_object_text)
from app.content.whiteboard.checkpoints import CheckpointStore
from app.content.whiteboard.contracts import WhiteboardRegion, WhiteboardRegionPlan
from app.content.whiteboard.jobs import owner_task, records
from app.content.whiteboard.review import ClaudeHubVisualCritic, OpenRouterVisualCritic
from app.content.whiteboard.runtime import (CLAUDEHUB_VISION_MODEL, ReadinessError, generation_profile, preflight,
                                             safe_error, scoped_settings)
from app.content.whiteboard.worker import build_pipeline
from test_whiteboard import PNG, scene
from test_whiteboard_jobs import client, environment

HUB = 'https://api.claudehub.fun/v1'


def plan_json():
    return WhiteboardRegionPlan(regions=[WhiteboardRegion(beat_index=i, box=(0, 100 * i, 1000, 100 * i + 90))
                                         for i in range(len(scene().beats))]).model_dump_json()


@pytest.mark.parametrize('profile', ['configured', 'gemini_ru'])
def test_both_profiles_use_claudehub_for_regions_and_openrouter_only_for_speech(environment, profile):
    _, settings, _, _ = environment
    models, _ = generation_profile(settings, profile)
    assert models.regions == CLAUDEHUB_VISION_MODEL and '/' not in models.regions
    assert models.image == 'gemini-3.1-flash-image'
    if profile == 'gemini_ru':
        assert models.speech == 'x-ai/grok-voice-tts-1.0'


def test_new_jobs_default_to_the_working_profile_without_google_models_on_openrouter(environment):
    factory, settings, product, _ = environment
    with client() as browser:
        state = browser.post(f'/products/{product}/whiteboard/jobs', json={'topic': 'Запуск из диспетчера без профиля'}).json()
    assert state['models']['speech'] == 'x-ai/grok-voice-tts-1.0'
    assert state['models']['regions'] == CLAUDEHUB_VISION_MODEL


def test_pipeline_sends_regions_and_review_to_claudehub_but_speech_to_openrouter(environment, tmp_path):
    factory, settings, product, _ = environment
    with client() as browser:
        state = browser.post(f'/products/{product}/whiteboard/jobs', json={'topic': 'Проверка маршрутов', 'visual_review': True}).json()
    from uuid import UUID
    with factory() as db:
        _, _, _, payload = records(db, owner_task(db, product, UUID(state['task_id'])))
    store = CheckpointStore(tmp_path / 'stages', payload.request, models=payload.models.model_dump(), visual_review=True, image_provider='claudehub')
    pipeline = build_pipeline(scoped_settings(settings, payload.models, payload.request.voice), payload, store)
    assert isinstance(pipeline.regions, ClaudeHubRegionProvider) and isinstance(pipeline.critic, ClaudeHubVisualCritic)
    assert isinstance(pipeline.speech, OpenRouterSpeechProvider)


def test_jobs_approved_before_the_switch_keep_their_openrouter_vision_model(environment, tmp_path):
    factory, settings, product, _ = environment
    with client() as browser:
        state = browser.post(f'/products/{product}/whiteboard/jobs', json={'topic': 'Старое задание', 'visual_review': True}).json()
    from uuid import UUID
    with factory() as db:
        _, _, _, payload = records(db, owner_task(db, product, UUID(state['task_id'])))
    old = payload.model_copy(update={'models': payload.models.model_copy(update={'regions': 'qwen/qwen3-vl-30b-a3b-instruct'})})
    store = CheckpointStore(tmp_path / 'stages', old.request, models=old.models.model_dump(), visual_review=True, image_provider='claudehub')
    pipeline = build_pipeline(scoped_settings(settings, old.models, old.request.voice), old, store)
    assert type(pipeline.regions) is OpenRouterRegionProvider and type(pipeline.critic) is OpenRouterVisualCritic


@pytest.mark.asyncio
async def test_region_request_goes_to_claudehub_only_with_the_vision_model_and_accepts_fenced_json():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={'choices': [{'message': {'content': '```json\n' + plan_json() + '\n```'}}]})

    async with httpx.AsyncClient(base_url=HUB + '/', transport=httpx.MockTransport(handler)) as http:
        plan = await ClaudeHubRegionProvider(api_key='hub-key', base_url=HUB, model=CLAUDEHUB_VISION_MODEL, client=http).detect(scene(), PNG)
    plan.check_for(scene())
    assert len(seen) == 1 and seen[0].url.host == 'api.claudehub.fun' and seen[0].url.path == '/v1/chat/completions'
    assert seen[0].headers['authorization'] == 'Bearer hub-key'
    assert json.loads(seen[0].content)['model'] == CLAUDEHUB_VISION_MODEL


def test_claudehub_chat_refuses_untrusted_hosts():
    for url in ('https://openrouter.ai/api/v1', 'http://api.claudehub.fun/v1', 'https://evil.example/v1'):
        with pytest.raises(ValueError):
            ClaudeHubRegionProvider(api_key='k', base_url=url, model=CLAUDEHUB_VISION_MODEL)


def test_fence_helper_leaves_plain_json_alone():
    assert json_object_text('{"a": 1}') == '{"a": 1}' and json_object_text('```\n{"a": 1}\n```') == '{"a": 1}'


@pytest.mark.asyncio
async def test_preflight_names_a_missing_claudehub_vision_model_before_any_paid_call(environment):
    _, settings, _, _ = environment
    models, voice = generation_profile(settings, 'gemini_ru')
    hub = httpx.MockTransport(lambda req: httpx.Response(200, json={'data': [{'id': 'gemini-3.1-flash-image'}]}))
    router = httpx.MockTransport(lambda req: httpx.Response(200, json={'data': [{'id': models.speech, 'supported_voices': ['eve']}]}))
    async with (httpx.AsyncClient(base_url=HUB + '/', transport=hub) as h,
                httpx.AsyncClient(base_url='https://openrouter.ai/api/v1/', transport=router) as r):
        with pytest.raises(ReadinessError) as error:
            await preflight(scoped_settings(settings, models, voice), models, voice, client=r, claudehub_client=h)
    assert error.value.code == 'claudehub_vision_unavailable'


@pytest.mark.parametrize('host,code', [('api.claudehub.fun', 'claudehub_rejected'), ('openrouter.ai', 'openrouter_forbidden')])
def test_403_is_attributed_to_the_provider_that_refused(host, code):
    request = httpx.Request('POST', f'https://{host}/v1/chat/completions')
    response = httpx.Response(403, text='denied', request=request)
    assert safe_error(httpx.HTTPStatusError('x', request=request, response=response)) == code
