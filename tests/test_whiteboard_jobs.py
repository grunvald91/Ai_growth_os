"""Owner/API -> persistent worker -> real synthetic MP4; no live provider calls."""
import asyncio
import hashlib
import io
import json
import subprocess
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api import whiteboard as api
from app.content.whiteboard.contracts import WhiteboardRequest
from app.content.whiteboard.jobs import (CallLedger, approve_job, cancel_job, digest,
    job_directory, owner_task, queue_job, records)
from app.content.whiteboard.pipeline import WhiteboardPipeline
from app.content.whiteboard.render import MarkerSweepRenderer
from app.content.whiteboard.runtime import ReadinessError, SingleCallStoryboardLLM, configured_models, preflight
from app.content.whiteboard.worker import recover_interrupted_jobs, run_whiteboard_once
from app.content.whiteboard.checkpoints import _Lock
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.llm.schemas import LLMRequest, LLMMessage
from app.main import app
from app.models.domain import AgentRun, Approval, Artifact, ArtifactStatus, Product, Task, TaskStatus
from app.privacy import PrivacyGateway
from test_whiteboard import FakePlanner, FakeRegions, FakeSpeech, FakeRenderer


@pytest.fixture
def environment(tmp_path):
    engine = create_engine('sqlite+pysqlite:///' + str(tmp_path / 'jobs.db'), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(_env_file=None, products_root=tmp_path / 'products', app_env='production',
        studio_admin_username='owner', studio_admin_password='test-owner-password', studio_public_origin='https://studio.test',
        xai_api_key='text-test-key', openrouter_api_key='media-test-key',
        whiteboard_storyboard_model='kimi-2.7', whiteboard_image_model='google/image',
        whiteboard_speech_model='google/tts', whiteboard_regions_model='google/vision', whiteboard_voice='Kore')
    with factory() as db:
        product = Product(name='ServGo', slug='whiteboard-jobs')
        other = Product(name='Other company', slug='other-jobs')
        db.add_all([product, other]); db.commit()
    def dependency():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    app.dependency_overrides[get_settings] = lambda: settings
    yield factory, settings, product.id, other.id
    app.dependency_overrides.clear()
    engine.dispose()


def client():
    browser = TestClient(app, headers={'Origin': 'https://studio.test'})
    browser.auth = ('owner', 'test-owner-password')
    return browser


def new_job(environment, *, maximum=13):
    factory, settings, product_id, _ = environment
    with factory() as db:
        task = queue_job(db, WhiteboardRequest(product_id=product_id, topic='Как работает онлайн-запись', voice='Kore'),
                         configured_models(settings), max_api_calls=maximum)
        return task.id


def approve(environment, task_id):
    factory, _, product_id, _ = environment
    with factory() as db:
        task = owner_task(db, product_id, task_id)
        source, _, _, _ = records(db, task)
        approve_job(db, product_id, task_id, request_digest=digest(source.content), confirmed=True, decided_by='owner')


async def ready(*args):
    return {'ready': True}


def fake_factory(settings, payload, store):
    class ImageProvider:
        async def generate(self, *args):
            image = Image.new('RGB', (180, 320), 'white'); draw = ImageDraw.Draw(image)
            draw.rectangle((4, 8, 71, 127), fill='red'); draw.rectangle((92, 8, 161, 143), fill='blue')
            output = io.BytesIO(); image.save(output, format='PNG'); return output.getvalue()
    return WhiteboardPipeline(storyboard=FakePlanner(), images=ImageProvider(), speech=FakeSpeech(),
                              regions=FakeRegions(), renderer=FakeRenderer(), sanitizer=PrivacyGateway(), checkpoints=store)


def test_queue_immutable_review_dedupes_and_cannot_approve_stale_digest(environment):
    factory, settings, product_id, _ = environment
    first = new_job(environment); second = new_job(environment)
    assert first == second
    with factory() as db:
        task = owner_task(db, product_id, first)
        assert task.status == TaskStatus.WAITING_APPROVAL
        with pytest.raises(ReadinessError):
            approve_job(db, product_id, first, request_digest='0' * 64, confirmed=True, decided_by='owner')
        assert records(db, task)[2].status == 'pending'
        source = records(db, task)[0]; source.content = source.content.replace('онлайн-запись', 'онлайн-оплата')
        db.commit()
        with pytest.raises(ReadinessError):
            records(db, task)


@pytest.mark.asyncio
async def test_approved_job_models_are_scoped_and_do_not_change_shared_settings(environment):
    factory, settings, product_id, _ = environment
    original = configured_models(settings)
    chosen = original.model_copy(update={'image': 'black-forest-labs/flux.2-pro',
        'speech': 'x-ai/grok-voice-tts-1.0', 'regions': 'qwen/qwen3-vl-30b-a3b-instruct'})
    with factory() as db:
        task = queue_job(db, WhiteboardRequest(product_id=product_id, topic='Как работает онлайн-запись', voice='eve'), chosen)
        task_id = task.id
    approve(environment, task_id)
    async def check(scoped, models, voice):
        assert scoped is not settings and configured_models(scoped) == chosen == models
        assert scoped.whiteboard_voice == voice == 'eve'
        assert scoped.openrouter_api_key == settings.openrouter_api_key
        return {'ready': True}
    assert await run_whiteboard_once(factory, settings, factory=fake_factory, readiness=check) == 1
    assert configured_models(settings) == original and settings.whiteboard_voice == 'Kore'
    with factory() as db:
        assert db.get(Task, task_id).status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_worker_never_picks_unapproved_request(environment):
    factory, settings, product_id, _ = environment
    task_id = new_job(environment)
    assert await run_whiteboard_once(factory, settings, factory=fake_factory, readiness=ready) == 0
    with factory() as db:
        assert owner_task(db, product_id, task_id).status == TaskStatus.WAITING_APPROVAL
        assert list(db.scalars(select(AgentRun))) == []


@pytest.mark.asyncio
async def test_worker_checks_persisted_approval_even_if_task_was_queued(environment):
    factory, settings, product_id, _ = environment
    task_id = new_job(environment)
    with factory() as db:
        db.get(Task, task_id).status = TaskStatus.QUEUED; db.commit()
    await run_whiteboard_once(factory, settings, factory=fake_factory, readiness=ready)
    with factory() as db:
        task = owner_task(db, product_id, task_id)
        assert task.status == TaskStatus.FAILED
        assert json.loads(records(db, task)[1].content)['calls'] == 0


@pytest.mark.asyncio
async def test_security_policy_preflight_blocks_before_any_model(environment):
    factory, settings, product_id, _ = environment
    task_id = new_job(environment); approve(environment, task_id)
    async def denied(*args): raise ReadinessError('openrouter_security_policy')
    assert await run_whiteboard_once(factory, settings, factory=fake_factory, readiness=denied) == 1
    with factory() as db:
        task = owner_task(db, product_id, task_id)
        state = json.loads(records(db, task)[1].content)
        assert task.status == TaskStatus.FAILED and state['calls'] == 0
        assert state['error'] == 'openrouter_security_policy'
        assert list(db.scalars(select(AgentRun))) == []


@pytest.mark.asyncio
async def test_full_owner_interface_worker_and_real_mp4(environment, tmp_path, monkeypatch):
    factory, settings, product_id, other_id = environment
    monkeypatch.setattr(api, 'preflight', ready)
    audio = tmp_path / 'tone.mp3'
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
                    '-codec:a', 'libmp3lame', str(audio)], check=True)
    def real_factory(settings, payload, store):
        pipeline = fake_factory(settings, payload, store)
        class Speech:
            async def synthesize(self, *args, **kwargs): return audio.read_bytes()
        pipeline.speech = Speech()
        pipeline.renderer = MarkerSweepRenderer(width=180, height=320, fps=8)
        return pipeline
    with client() as browser:
        index = browser.get(f'/studio/products/{product_id}/whiteboard')
        assert index.status_code == 200 and 'Сохранить и перейти' in index.text
        response = browser.post(f'/studio/products/{product_id}/whiteboard',
                                data={'topic': 'Как работает онлайн-запись', 'max_api_calls': '13'}, follow_redirects=False)
        assert response.status_code == 303
        url = response.headers['location']; task_id = UUID(url.split('/')[-1])
        review = browser.get(url)
        assert 'Подтвердить и сгенерировать' in review.text
        with factory() as db:
            source = records(db, owner_task(db, product_id, task_id))[0]
            fingerprint = digest(source.content)
        for _ in range(2):
            response = browser.post(url + '/approve', data={'confirmed': 'yes', 'request_digest': fingerprint}, follow_redirects=False)
            assert response.status_code == 303
        assert await run_whiteboard_once(factory, settings, factory=real_factory, readiness=ready) == 1
        assert await run_whiteboard_once(factory, settings, factory=real_factory, readiness=ready) == 0
        page = browser.get(url)
        assert page.status_code == 200 and '<video controls' in page.text
        assert '13 из 13' in page.text and 'Скачать MP4' in page.text
        assert 'Завершено этапов: 14 из 14' in page.text
        assert '/files/video.vtt' in page.text
        download = browser.get(url + '/files/video.mp4')
        assert download.status_code == 200 and b'ftyp' in download.content[:32]
        attachment = browser.get(url + '/files/video.mp4?download=true')
        assert attachment.content == download.content
        assert attachment.headers['content-disposition'] == 'attachment; filename="video.mp4"'
        seek = browser.get(url + '/files/video.mp4', headers={'Range': 'bytes=0-31'})
        assert seek.status_code == 206 and seek.content == download.content[:32]
        subtitles = browser.get(url + '/files/video.vtt')
        assert subtitles.status_code == 200 and subtitles.text.startswith('WEBVTT\n\n')
        assert '00:00:00.000 --> ' in subtitles.text and 'text/vtt' in subtitles.headers['content-type']
        assert browser.get(f'/studio/products/{other_id}/whiteboard/{task_id}/files/video.vtt').status_code == 404
        index = browser.get(f'/studio/products/{product_id}/whiteboard')
        assert 'Видео готово' in index.text and 'Посмотреть видео' in index.text
        assert '/files/video.mp4?download=true' in index.text
        assert browser.get(url + '/files/video.srt').status_code == 200
        assert browser.get(url + '/files/stages').status_code == 404
        assert browser.get(f'/studio/products/{other_id}/whiteboard/{task_id}/files/video.mp4').status_code == 404
        meta = browser.get(url + '/files/video.json').json()
        assert abs(meta['duration_seconds'] - 30) < .1
        assert meta['video_sha256'] == hashlib.sha256(download.content).hexdigest()
        redirect = browser.get(f'/studio/products/{product_id}/tasks/{task_id}', follow_redirects=False)
        assert redirect.status_code == 303 and redirect.headers['location'] == url
    with factory() as db:
        assert len(list(db.scalars(select(AgentRun)))) == 13
        assert db.get(Task, task_id).status == TaskStatus.COMPLETED
        assert len(list(db.scalars(select(Artifact).where(Artifact.kind == 'whiteboard_video')))) == 1


@pytest.mark.asyncio
async def test_failed_paid_stage_is_not_retried_by_worker(environment):
    factory, settings, product_id, _ = environment
    task_id = new_job(environment); approve(environment, task_id)
    calls = []
    def broken_factory(settings, payload, store):
        pipeline = fake_factory(settings, payload, store)
        class Speech:
            async def synthesize(self, *args, **kwargs):
                calls.append(1); raise TimeoutError('private provider error must not be stored')
        pipeline.speech = Speech(); return pipeline
    await run_whiteboard_once(factory, settings, factory=broken_factory, readiness=ready)
    assert await run_whiteboard_once(factory, settings, factory=broken_factory, readiness=ready) == 0
    assert len(calls) == 1
    with factory() as db:
        task = owner_task(db, product_id, task_id); state = json.loads(records(db, task)[1].content)
        assert task.status == TaskStatus.FAILED and state['calls'] == 4
        assert 'private provider error' not in json.dumps(state)
    manifest = json.loads((job_directory(settings.products_root, product_id, task_id) / 'stages/manifest.json').read_text())
    assert manifest['stages']['scene-00-beat-00-speech']['status'] == 'interrupted_or_failed'


def test_call_ledger_caps_requests_and_cancellation_blocks_next_call(environment):
    factory, settings, product_id, _ = environment
    task_id = new_job(environment); approve(environment, task_id)
    with factory() as db:
        db.get(Task, task_id).status = TaskStatus.RUNNING; db.commit()
    ledger = CallLedger(factory, product_id, task_id)
    for _ in range(13): ledger.finish(ledger.begin('speech'))
    with pytest.raises(ReadinessError, match='call_limit'): ledger.begin('speech')
    with factory() as db: cancel_job(db, product_id, task_id)
    with pytest.raises(ReadinessError, match='cancelled'): ledger.begin('image')


def test_crashed_running_job_is_failed_without_requeue(environment):
    factory, settings, product_id, _ = environment
    task_id = new_job(environment); approve(environment, task_id)
    with factory() as db:
        task = db.get(Task, task_id); task.status = TaskStatus.RUNNING
        task.updated_at = datetime.now(timezone.utc) - timedelta(minutes=3); db.commit()
    recover_interrupted_jobs(factory, settings)
    with factory() as db:
        task = owner_task(db, product_id, task_id)
        assert task.status == TaskStatus.FAILED
        assert json.loads(records(db, task)[1].content)['error'] == 'interrupted'


def test_authenticated_api_cannot_cross_company_or_cross_origin(environment, monkeypatch):
    factory, settings, product_id, other_id = environment
    monkeypatch.setattr(api, 'preflight', ready)
    path = f'/products/{product_id}/whiteboard/jobs'
    with client() as browser:
        assert browser.post(path, json={'topic': 'Тема обучающего ролика'}, headers={'Origin': 'https://evil.test'}).status_code == 403
        created = browser.post(path, json={'topic': 'Тема обучающего ролика'})
        assert created.status_code == 201 and created.json()['status'] == 'waiting_approval'
        task_id = created.json()['task_id']
        assert browser.get(f'/products/{other_id}/whiteboard/jobs/{task_id}').status_code == 404
        bad = browser.post(path + '/' + task_id + '/approve', json={'confirmed': True, 'request_digest': '0' * 64})
        assert bad.status_code == 409
    with TestClient(app) as stranger:
        assert stranger.get(path).status_code == 401
        assert stranger.post(path, json={'topic': 'Тема обучающего ролика'}).status_code == 401


def test_security_policy_visible_in_interface_and_keeps_review_pending(environment, monkeypatch):
    factory, settings, product_id, _ = environment
    async def blocked(*args): raise ReadinessError('openrouter_security_policy')
    monkeypatch.setattr(api, 'preflight', blocked)
    with client() as browser:
        created = browser.post(f'/products/{product_id}/whiteboard/jobs', json={'topic': 'Тема обучающего ролика'}).json()
        url = f'/studio/products/{product_id}/whiteboard/{created["task_id"]}'
        response = browser.post(url + '/approve', data={'confirmed': 'yes', 'request_digest': created['request_digest']})
        assert response.status_code == 503 and 'Access denied by security policy' in response.text
        state = browser.get(f'/products/{product_id}/whiteboard/jobs/{created["task_id"]}').json()
        assert state['calls'] == 0 and state['approval_status'] == 'pending'
        assert settings.openrouter_api_key not in response.text and settings.xai_api_key not in response.text


@pytest.mark.asyncio
async def test_preflight_uses_documented_catalogue_and_rejects_unavailable_voice(environment):
    _, settings, _, _ = environment
    seen = []
    def handler(request):
        seen.append(str(request.url))
        if request.url.path.endswith('/key'): return httpx.Response(200, json={'data': {}})
        return httpx.Response(200, json={'data': [
            {'id': 'google/image'}, {'id': 'google/tts', 'supported_voices': ['Kore']}, {'id': 'google/vision'}]})
    async with httpx.AsyncClient(base_url='https://openrouter.ai/api/v1/', transport=httpx.MockTransport(handler)) as connection:
        assert (await preflight(settings, configured_models(settings), 'Kore', client=connection))['ready']
    assert any('output_modalities=speech' in url for url in seen)
    assert len(seen) == 4


@pytest.mark.asyncio
async def test_preflight_security_policy_sends_only_one_get(environment):
    _, settings, _, _ = environment
    calls = []
    def handler(request):
        calls.append(request); return httpx.Response(403, json={'success': False, 'error': 'Access denied by security policy.'})
    async with httpx.AsyncClient(base_url='https://openrouter.ai/api/v1/', transport=httpx.MockTransport(handler)) as connection:
        with pytest.raises(ReadinessError, match='openrouter_security_policy'):
            await preflight(settings, configured_models(settings), 'Kore', client=connection)
    assert len(calls) == 1 and calls[0].method == 'GET'


@pytest.mark.asyncio
async def test_storyboard_provider_never_retries_failed_post(environment):
    _, settings, _, _ = environment
    calls = []
    def handler(request):
        calls.append(request); return httpx.Response(500, json={'error': 'temporary'})
    async with httpx.AsyncClient(base_url='https://api.claudehub.fun/v1/', transport=httpx.MockTransport(handler)) as connection:
        provider = SingleCallStoryboardLLM(settings, client=connection)
        with pytest.raises(httpx.HTTPStatusError):
            await provider.generate(LLMRequest(model='kimi-2.7', messages=[LLMMessage(role='user', content='test')], response_schema={}))
    assert len(calls) == 1


def test_customer_account_is_limited_to_its_company(environment, tmp_path):
    _, settings, product_id, other_id = environment
    salt = bytes.fromhex('1f' * 16)
    value = hashlib.pbkdf2_hmac('sha256', b'customer-password', salt, 600000).hex()
    accounts = tmp_path / 'accounts.json'
    accounts.write_text(json.dumps({'customer': {'password_hash': 'pbkdf2_sha256$600000$' + salt.hex() + '$' + value,
                                               'product_ids': [str(product_id)]}}))
    settings.studio_users_file = accounts
    with TestClient(app, headers={'Origin': 'https://studio.test'}) as browser:
        browser.auth = ('customer', 'customer-password')
        assert browser.get(f'/products/{product_id}/whiteboard/jobs').status_code == 200
        assert browser.get(f'/products/{other_id}/whiteboard/jobs').status_code == 404
        assert browser.post(f'/products/{other_id}/whiteboard/jobs', json={'topic': 'Объясняющее видео'}).status_code == 404


def test_recovery_does_not_interrupt_a_live_worker_lock(environment):
    factory, settings, product_id, _ = environment
    task_id = new_job(environment); approve(environment, task_id)
    with factory() as db:
        task = db.get(Task, task_id); task.status = TaskStatus.RUNNING
        task.updated_at = datetime.now(timezone.utc) - timedelta(minutes=3); db.commit()
    directory = job_directory(settings.products_root, product_id, task_id)
    directory.mkdir(parents=True)
    with _Lock(directory / 'job.lock'):
        recover_interrupted_jobs(factory, settings)
    with factory() as db:
        assert db.get(Task, task_id).status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_cancel_during_one_call_prevents_all_following_calls(environment):
    factory, settings, product_id, _ = environment
    task_id = new_job(environment); approve(environment, task_id)
    def cancel_during_image(settings, payload, store):
        pipeline = fake_factory(settings, payload, store)
        original = pipeline.images
        class Images:
            async def generate(self, *args):
                with factory() as db: cancel_job(db, product_id, task_id)
                return await original.generate(*args)
        pipeline.images = Images(); return pipeline
    await run_whiteboard_once(factory, settings, factory=cancel_during_image, readiness=ready)
    with factory() as db:
        task = owner_task(db, product_id, task_id)
        assert task.status == TaskStatus.CANCELLED
        state = json.loads(records(db, task)[1].content)
        assert state['calls'] == 2 and state['error'] == 'cancelled'


@pytest.mark.asyncio
async def test_readiness_and_media_use_the_explicit_proxy_with_tls_validation(environment, monkeypatch):
    from app.content.whiteboard.adapters import OpenRouterSpeechProvider
    _, settings, _, _ = environment
    settings.whiteboard_proxy_url = 'http://trusted-proxy.test:8891'
    client_options = []
    actual_client = httpx.AsyncClient
    def handler(request):
        if request.method == 'POST': return httpx.Response(200, content=b'ID3' + b'x' * 200)
        if request.url.path.endswith('/key'): return httpx.Response(200, json={'data': {}})
        return httpx.Response(200, json={'data': [
            {'id': 'google/image'}, {'id': 'google/tts', 'supported_voices': ['Kore']}, {'id': 'google/vision'}]})
    def connection(**kwargs):
        client_options.append(kwargs)
        assert kwargs.pop('proxy') == settings.whiteboard_proxy_url
        assert kwargs.get('verify', True) is True
        return actual_client(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, 'AsyncClient', connection)
    await preflight(settings, configured_models(settings), 'Kore')
    audio = await OpenRouterSpeechProvider(api_key=settings.openrouter_api_key, model='google/tts',
        proxy_url=settings.whiteboard_proxy_url).synthesize('Пример короткой фразы', language='ru', voice='Kore')
    assert audio.startswith(b'ID3') and len(client_options) == 2
    assert all(options['trust_env'] is False and options['follow_redirects'] is False for options in client_options)
