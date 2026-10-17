"""Crash/billing uncertainty, integrity, run isolation and offline CLI checks."""
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.content.whiteboard.checkpoints import CheckpointError, CheckpointStore
from app.content.whiteboard.contracts import WhiteboardRequest, WhiteboardStoryboard
from app.content.whiteboard.__main__ import doctor, render_checkpoint
from app.content.whiteboard.pipeline import WhiteboardPipeline
from app.content.whiteboard.render import MarkerSweepRenderer
from app.privacy import PrivacyGateway
from test_whiteboard_render import assets, tone

MODELS = {'storyboard': 'test-text', 'image': 'test-image', 'speech': 'test-speech', 'regions': 'test-vision'}


def request():
    return WhiteboardRequest(product_id=uuid4(), topic='Проверка сохранения этапов видео', request_generation=True)


@pytest.mark.asyncio
async def test_completed_stage_is_reused_without_second_call(tmp_path):
    store = CheckpointStore(tmp_path / 'run', request(), models=MODELS)
    calls = 0
    async def callback():
        nonlocal calls
        calls += 1
        return b'completed-audio'
    assert await store.bytes_stage('speech', callback) == b'completed-audio'
    assert await store.bytes_stage('speech', callback) == b'completed-audio'
    assert calls == 1
    manifest = json.loads((store.directory / 'manifest.json').read_text())
    assert manifest['stages']['speech']['billing_status'] == 'unknown'
    assert manifest['stages']['speech']['cost_usd'] is None


@pytest.mark.asyncio
async def test_failed_call_is_not_repeated(tmp_path):
    store = CheckpointStore(tmp_path / 'run', request(), models=MODELS)
    calls = 0
    async def callback():
        nonlocal calls
        calls += 1
        raise RuntimeError('network outcome unknown')
    with pytest.raises(RuntimeError):
        await store.bytes_stage('speech', callback)
    with pytest.raises(CheckpointError, match='Automatic retry blocked'):
        await store.bytes_stage('speech', callback)
    assert calls == 1


@pytest.mark.asyncio
async def test_running_stage_after_crash_is_not_repeated(tmp_path):
    store = CheckpointStore(tmp_path / 'run', request(), models=MODELS)
    manifest = json.loads((store.directory / 'manifest.json').read_text())
    manifest['stages']['image'] = {'status': 'running'}
    store._write_manifest(manifest)
    async def must_not_run():
        pytest.fail('Interrupted call must not be repeated')
    with pytest.raises(CheckpointError, match='billing is unknown'):
        await store.bytes_stage('image', must_not_run)


@pytest.mark.asyncio
async def test_corrupt_cache_and_symlink_do_not_trigger_model(tmp_path):
    store = CheckpointStore(tmp_path / 'run', request(), models=MODELS)
    async def value():
        return b'original'
    await store.bytes_stage('image', value)
    path = store.directory / 'image.data'
    path.write_bytes(b'tampered')
    async def must_not_run():
        pytest.fail('Corrupt cache must not trigger a charge')
    with pytest.raises(CheckpointError, match='integrity'):
        await store.bytes_stage('image', must_not_run)
    path.unlink()
    path.symlink_to(tmp_path / 'other')
    with pytest.raises(CheckpointError, match='Symlink'):
        store.load('image')


def test_request_and_model_changes_reject_existing_run(tmp_path):
    req = request()
    CheckpointStore(tmp_path / 'run', req, models=MODELS)
    with pytest.raises(CheckpointError, match='request/models changed'):
        CheckpointStore(tmp_path / 'run', request(), models=MODELS)
    with pytest.raises(CheckpointError, match='request/models changed'):
        CheckpointStore(tmp_path / 'run', req, models={**MODELS, 'speech': 'different-model'})
    with pytest.raises(ValueError):
        CheckpointStore(tmp_path / 'key', req, models={**MODELS, 'speech': 'sk-hub-never-store-credentials'})


@pytest.mark.asyncio
async def test_concurrent_call_is_rejected_without_second_execution(tmp_path):
    req = request()
    first = CheckpointStore(tmp_path / 'run', req, models=MODELS)
    second = CheckpointStore(tmp_path / 'run', req, models=MODELS)
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed():
        entered.set()
        await release.wait()
        return b'completed'
    task = asyncio.create_task(first.bytes_stage('image', delayed))
    await entered.wait()
    try:
        with pytest.raises(CheckpointError, match='already executing'):
            await second.bytes_stage('image', delayed)
    finally:
        release.set()
        assert await task == b'completed'


def test_doctor_does_not_print_keys_or_assume_valid_access(monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setenv('XAI_API_KEY', 'private-xai-test-credential')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'private-router-test-credential')
    monkeypatch.setenv('WHITEBOARD_IMAGE_MODEL', 'sk-hub-abcd1234567890')
    get_settings.cache_clear()
    try:
        info = doctor()
        encoded = json.dumps(info)
        assert 'private-xai' not in encoded and 'private-router' not in encoded
        assert 'sk-hub' not in encoded
        assert info['claudehub_compatible_key_present'] and info['openrouter_key_present']
        assert info['whiteboard_models']['image'] is None
        assert not info['paid_generation_enabled_by_cli']
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_complete_run_can_be_rendered_again_without_provider_calls(tmp_path, tone):
    item = assets(tone)
    req = request()
    store = CheckpointStore(tmp_path / 'run', req, models=MODELS)
    calls = []
    class Planner:
        async def plan(self, request):
            calls.append('storyboard')
            return WhiteboardStoryboard(scenes=[item.scene] * 3)
    class Images:
        async def generate(self, scene, request):
            calls.append('image')
            return item.image_png
    class Speech:
        async def synthesize(self, text, **kwargs):
            calls.append('speech')
            return tone
    class Regions:
        async def detect(self, scene, image_png):
            calls.append('regions')
            return item.regions
    pipeline = WhiteboardPipeline(storyboard=Planner(), images=Images(), speech=Speech(),
        regions=Regions(), renderer=MarkerSweepRenderer(width=180, height=320, fps=5),
        sanitizer=PrivacyGateway(), checkpoints=store)
    await pipeline.produce(req, tmp_path / 'first.mp4', approval_verified=True)
    seen = list(calls)
    await pipeline.produce(req, tmp_path / 'second.mp4', approval_verified=True)
    assert calls == seen
    assert len(seen) == 13  # 1 storyboard, 3 images, 3 region plans, 6 speech clips
    local = render_checkpoint(tmp_path / 'run', tmp_path / 'third.mp4', width=180, height=320, fps=5)
    assert calls == seen and local['provider_calls'] == 0
    assert abs(local['duration_seconds'] - 30) < 0.1
    assert Path(local['video']).is_file()
