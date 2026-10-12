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
    return WhiteboardRequest(product_id=uuid4(), topic="Зачем бизнесу онлайн-запись", **changes)


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
    output = await worker.produce(request(), tmp_path / "video.mp4")
    assert output.scene_count == 3 and output.duration_seconds == 30.0
    assert output.video_path.is_file()
    assert render.asset_count == 3 and len(speech.seen) == 6
    assert all(entry[1] == "ru" for entry in speech.seen)
    with pytest.raises(FileExistsError):
        await worker.produce(request(), tmp_path / "video.mp4")


@pytest.mark.asyncio
async def test_pipeline_rejects_secret_before_any_provider_call(tmp_path):
    planner = FakePlanner()
    worker = WhiteboardPipeline(
        storyboard=planner, images=FakeImages(), speech=FakeSpeech(),
        regions=FakeRegions(), renderer=FakeRenderer(), sanitizer=PrivacyGateway(),
    )
    with pytest.raises(ValueError, match="Sensitive text"):
        await worker.produce(request(topic="Пароль sk-hub-abcdefgh123456789 для демонстрации"), tmp_path / "x.mp4")
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
    assert recorded[1][2]["response_format"] == "mp3"


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
