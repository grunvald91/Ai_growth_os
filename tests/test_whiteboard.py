"""Offline tests for the new whiteboard scaffolding. No provider calls or ffmpeg."""
import base64
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.content.whiteboard.adapters import (
    LLMStoryboardProvider, OpenRouterImageProvider,
    OpenRouterRegionProvider, OpenRouterSpeechProvider,
)
from app.content.whiteboard.contracts import (
    WhiteboardBeat, WhiteboardRegion, WhiteboardRegionPlan, WhiteboardRequest,
    WhiteboardScene, WhiteboardStoryboard,
)
from app.content.whiteboard.pipeline import WhiteboardPipeline
from app.content.whiteboard.queue import queue_whiteboard
from app.llm.schemas import LLMResponse
from app.llm.service import LLMService
from app.models.domain import Approval, Artifact, Product, TaskStatus
from app.privacy import PrivacyGateway

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-fixture"


def request(**changes):
    values = {"product_id": uuid4(), "topic": "Зачем бизнесу онлайн-запись"}
    values.update(changes)
    return WhiteboardRequest(**values)


def scene():
    return WhiteboardScene(
        title="Время клиента",
        beats=[
            WhiteboardBeat(label="Запись", narration="Клиент выбирает свободное время.", visual="Нарисованный календарь"),
            WhiteboardBeat(label="Владелец", narration="Владелец видит расписание.", visual="Нарисованный телефон"),
        ],
    )


def storyboard():
    return WhiteboardStoryboard(scenes=[scene(), scene(), scene()])


class FakePlanner:
    def __init__(self):
        self.calls = 0

    async def plan(self, req):
        self.calls += 1
        return storyboard()


class FakeImages:
    async def generate(self, s, req):
        return PNG


class FakeSpeech:
    def __init__(self):
        self.seen = []

    async def synthesize(self, text, *, language, voice):
        self.seen.append((text, language, voice))
        return b"ID3" + text.encode("utf-8")


class FakeRegions:
    async def detect(self, scene, image_png):
        return WhiteboardRegionPlan(regions=[
            WhiteboardRegion(beat_index=0, box=(10, 20, 400, 400)),
            WhiteboardRegion(beat_index=1, box=(510, 20, 900, 450)),
        ])


class FakeRenderer:
    def __init__(self):
        self.asset_count = 0

    async def render(self, scenes, destination, request):
        self.asset_count = len(scenes)
        assert all(len(s.narration_chunks) == len(s.scene.beats) for s in scenes)
        destination.write_bytes(b"fake-mp4-in-offline-test")
        return 30.0


def test_whiteboard_request_rejects_unsupported_formats():
    with pytest.raises(ValidationError):
        request(aspect_ratio="16:9")
    with pytest.raises(ValidationError):
        request(scenes=20)
    with pytest.raises(ValidationError):
        request(topic="hi")


def test_region_plan_enforces_coverage_and_bounds():
    with pytest.raises(ValidationError):
        WhiteboardRegion(beat_index=0, box=(400, 0, 100, 500))
    with pytest.raises(ValueError):
        WhiteboardRegionPlan(regions=[WhiteboardRegion(beat_index=0, box=(1, 1, 100, 100))]).check_for(scene())


@pytest.mark.asyncio
async def test_pipeline_stages_create_one_output_and_one_audio_per_beat(tmp_path):
    speech, render, planner = FakeSpeech(), FakeRenderer(), FakePlanner()
    worker = WhiteboardPipeline(
        storyboard=planner, images=FakeImages(), speech=speech,
        regions=FakeRegions(), renderer=render, sanitizer=PrivacyGateway(),
    )
    output = await worker.produce(request(request_generation=True), tmp_path / "video.mp4", approval_verified=True)
    assert output.scene_count == 3 and output.duration_seconds == 30.0
    assert output.video_path.is_file()
    assert render.asset_count == 3 and len(speech.seen) == 6
    assert all(entry[1] == "ru" for entry in speech.seen)
    with pytest.raises(FileExistsError):
        await worker.produce(request(request_generation=True), tmp_path / "video.mp4", approval_verified=True)


@pytest.mark.asyncio
async def test_pipeline_rejects_secret_before_any_provider_call(tmp_path):
    planner = FakePlanner()
    worker = WhiteboardPipeline(
        storyboard=planner, images=FakeImages(), speech=FakeSpeech(),
        regions=FakeRegions(), renderer=FakeRenderer(), sanitizer=PrivacyGateway(),
    )
    with pytest.raises(ValueError, match="Sensitive text"):
        await worker.produce(request(topic="Пароль sk-hub-abcdefgh123456789 для демонстрации", request_generation=True), tmp_path / "x.mp4", approval_verified=True)
    assert planner.calls == 0


def test_queue_is_approval_gated_and_idempotent(db):
    product = Product(name="ServGo", slug="servgo-whiteboard")
    db.add(product)
    db.commit()
    req = WhiteboardRequest(product_id=product.id, topic="Как работает онлайн-запись")
    a = queue_whiteboard(db, req)
    b = queue_whiteboard(db, req)
    assert a.id == b.id and a.status == TaskStatus.WAITING_APPROVAL
    artifacts = list(db.scalars(select(Artifact).where(Artifact.task_id == a.id)))
    approvals = list(db.scalars(select(Approval).where(Approval.product_id == product.id)))
    assert len(artifacts) == len(approvals) == 1
    assert artifacts[0].kind == "whiteboard_request"
    assert approvals[0].artifact_id == artifacts[0].id
    assert approvals[0].status == "pending"


@pytest.mark.asyncio
async def test_documented_openrouter_image_and_speech_routes_use_bearer():
    recorded = []

    def handler(req: httpx.Request):
        recorded.append((req.url.path, req.headers.get("Authorization"), json.loads(req.content)))
        if req.url.path.endswith("/images"):
            return httpx.Response(200, json={"data": [{
                "b64_json": base64.b64encode(PNG).decode(), "media_type": "image/png",
            }]})
        return httpx.Response(200, content=b"ID3" + b"x" * 200)

    async with httpx.AsyncClient(
        base_url="https://openrouter.ai/api/v1/", transport=httpx.MockTransport(handler),
    ) as client:
        img = await OpenRouterImageProvider(api_key="test-key", model="image-model", client=client).generate(scene(), request())
        wav = await OpenRouterSpeechProvider(api_key="test-key", model="speech-model", client=client).synthesize(
            "Привет, мир!", language="ru", voice="test-voice",
        )
    assert img == PNG and wav.startswith(b"ID3")
    assert [row[0] for row in recorded] == ["/api/v1/images", "/api/v1/audio/speech"]
    assert all(row[1] == "Bearer test-key" for row in recorded)
    assert recorded[0][2]["aspect_ratio"] == "9:16"
    assert recorded[0][2]["output_format"] == "png"
    assert recorded[1][2]["response_format"] == "mp3"


@pytest.mark.asyncio
async def test_grok_speech_explicitly_selects_russian_without_retries():
    seen = []
    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, content=b'ID3' + b'x' * 200)
    async with httpx.AsyncClient(base_url="https://openrouter.ai/api/v1/", transport=httpx.MockTransport(handler)) as client:
        await OpenRouterSpeechProvider(api_key='test', model='x-ai/grok-voice-tts-1.0', client=client).synthesize('Русская речь.', language='ru', voice='eve')
    assert len(seen) == 1 and seen[0]['provider']['options']['xai']['language'] == 'ru'
    assert seen[0]['input'] == 'Русская речь.' and seen[0]['voice'] == 'eve'


@pytest.mark.asyncio
async def test_flux_pro_uses_only_its_supported_image_parameters():
    seen = []
    def handler(req):
        assert req.url.path == '/api/v1/images'
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode(), "media_type": "image/png"}]})
    async with httpx.AsyncClient(base_url="https://openrouter.ai/api/v1/", transport=httpx.MockTransport(handler)) as client:
        assert await OpenRouterImageProvider(api_key="test", model="black-forest-labs/flux.2-pro", client=client).generate(scene(), request()) == PNG
    assert len(seen) == 1 and 'resolution' not in seen[0]
    assert seen[0]['aspect_ratio'] == '9:16' and seen[0]['output_format'] == 'png' and seen[0]['n'] == 1


@pytest.mark.asyncio
async def test_gemini_image_uses_one_chat_request_and_keeps_png_validation():
    seen = []
    def handler(req):
        assert req.url.path == '/api/v1/chat/completions'
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"images": [{
            "image_url": {"url": "data:image/png;base64," + base64.b64encode(PNG).decode()},
        }]}}]})
    async with httpx.AsyncClient(base_url="https://openrouter.ai/api/v1/", transport=httpx.MockTransport(handler)) as client:
        result = await OpenRouterImageProvider(api_key="test", model="google/gemini-3.1-flash-image", client=client).generate(scene(), request())
    assert result == PNG
    assert len(seen) == 1 and "output_format" not in seen[0]
    assert seen[0]["model"] == "google/gemini-3.1-flash-image"
    assert seen[0]["image_config"] == {"aspect_ratio": "9:16", "image_size": "1K"}
    assert seen[0]["modalities"] == ["image", "text"]


@pytest.mark.asyncio
@pytest.mark.parametrize('url', ['https://example.com/image.png', 'data:image/jpeg;base64,eA==', 'data:image/png;base64,eA=='])
async def test_gemini_rejects_non_png_without_another_request(url):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"choices": [{"message": {"images": [{"image_url": {"url": url}}]}}]})
    async with httpx.AsyncClient(base_url="https://openrouter.ai/api/v1/", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await OpenRouterImageProvider(api_key="test", model="google/gemini-3.1-flash-image", client=client).generate(scene(), request())
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_openrouter_vision_regions_are_typed():
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "regions": [{"beat_index": 0, "box": [0, 0, 400, 400]},
                        {"beat_index": 1, "box": [500, 0, 950, 450]}],
        })}}]})

    async with httpx.AsyncClient(
        base_url="https://openrouter.ai/api/v1/", transport=httpx.MockTransport(handler),
    ) as client:
        plan = await OpenRouterRegionProvider(api_key="test-key", model="vision-model", client=client).detect(scene(), PNG)
    plan.check_for(scene())
    assert seen[0]["messages"][0]["content"][1]["type"] == "image_url"


@pytest.mark.asyncio
async def test_storyboard_adapter_reuses_privacy_service():
    class Provider:
        async def generate(self, req):
            assert req.model == "test-model"
            return LLMResponse(model="test-model", content=storyboard().model_dump_json())

    service = LLMService(privacy_gateway=PrivacyGateway(), provider=Provider())
    got = await LLMStoryboardProvider(service, model="test-model").plan(request())
    assert len(got.scenes) == 3


@pytest.mark.asyncio
async def test_pipeline_disallows_unapproved_paid_execution(tmp_path):
    planner = FakePlanner()
    pipeline = WhiteboardPipeline(
        storyboard=planner, images=FakeImages(), speech=FakeSpeech(),
        regions=FakeRegions(), renderer=FakeRenderer(), sanitizer=PrivacyGateway(),
    )
    with pytest.raises(PermissionError):
        await pipeline.produce(request(request_generation=True), tmp_path / "no.mp4")
    assert planner.calls == 0

