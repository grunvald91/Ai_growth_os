"""No paid calls: real PNG artifact, provider contract and owner-scoped ZIP/UI tests."""
from __future__ import annotations

import base64
import io
import json
import zipfile
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.content.creative import CreativeDraft, CreativeRequest, queue_creative, run_creative_once
from app.content.creative_images import (
    GEMINI_MODEL, OPENAI_MODEL, ImageProviderUnavailable, generate_images,
    generated_images, image_folder, png_output, request_image, resolve_model, image_aspect_ratio,
)
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Artifact, Product, TaskStatus, Task
from app.llm.schemas import LLMResponse, LLMUsage


def source_picture() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (480, 720), (80, 130, 190)).save(buffer, "PNG")
    return buffer.getvalue()


def draft_story(number: int = 3) -> CreativeDraft:
    return CreativeDraft.model_validate({
        "format": "story", "platform": "telegram",
        "title": "Три совета по выбору цветов",
        "hook": "Что учесть перед покупкой",
        "cta": "Спросите о предпочтениях человека",
        "caption": "Три практичных совета, чтобы выбрать букет без лишних хлопот.",
        "cover_text": "Как выбрать букет",
        "visual_style": "Тёплый реализм с чистыми цветами и простой композицией",
        "frames": [{
            "number": i + 1, "start_second": i * 5, "end_second": (i+1) * 5,
            "purpose": "Практическая подсказка зрителю",
            "visual_prompt": ("Цветочная мастерская с вазами и бумажной упаковкой, "
                              "естественное освещение, место под короткую подпись"),
            "on_screen_text": "Выбирайте с учётом получателя",
            "action_or_animation": "Плавная смена крупного плана",
        } for i in range(number)],
    })


def test_model_selection_and_png_size():
    assert resolve_model(Settings(_env_file=None, xai_api_key="yes", openai_api_key="yes"), "auto") == GEMINI_MODEL
    assert resolve_model(Settings(_env_file=None, xai_api_key=None, openai_api_key="yes"), "auto") == OPENAI_MODEL
    with pytest.raises(ImageProviderUnavailable):
        resolve_model(Settings(_env_file=None, xai_api_key=None, openai_api_key=None), "openai")
    with Image.open(io.BytesIO(png_output(source_picture(), ratio="9:16"))) as picture:
        assert picture.size == (900, 1600)
    with Image.open(io.BytesIO(png_output(source_picture(), ratio="1:1"))) as picture:
        assert picture.size == (1024, 1024)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_image_api_uses_only_requested_model_and_base64(provider):
    calls = []
    def handler(request: httpx.Request):
        calls.append((request.url.path, request.headers["Authorization"],
                      json.loads(request.content)))
        return httpx.Response(200, json={
            "data": [{"b64_json": base64.b64encode(source_picture()).decode("ascii")}],
        })
    base = "https://api.claudehub.fun/v1/" if provider == "gemini" else "https://api.openai.com/v1/"
    async with httpx.AsyncClient(base_url=base, transport=httpx.MockTransport(handler)) as client:
        settings = Settings(_env_file=None, xai_api_key="gemini-test", openai_api_key="openai-test")
        model = GEMINI_MODEL if provider == "gemini" else OPENAI_MODEL
        answer = await request_image(settings, model, "Детальный рисунок", "9:16", client=client)
    assert answer.startswith(b"\x89PNG")
    assert len(calls) == 1
    assert calls[0][0] == "/v1/images/generations"
    assert calls[0][1] == "Bearer " + ("gemini-test" if provider == "gemini" else "openai-test")
    assert calls[0][2]["model"] == model
    assert calls[0][2]["n"] == 1 if provider == "openai" else True


@pytest.fixture
def company_db(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        company = Product(name="Цветы", slug="flowers-images")
        another = Product(name="Другой", slug="other-images")
        db.add_all([company, another])
        db.commit()
        yield db, company, another, tmp_path
    engine.dispose()


class FakeImages:
    def __init__(self, *, fail=None):
        self.calls = []
        self.fail = fail

    async def generate(self, prompt, *, model, ratio):
        self.calls.append((model, ratio, prompt))
        if self.fail and len(self.calls) == self.fail:
            raise ValueError("Simulated provider error")
        return source_picture()


@pytest.mark.asyncio
async def test_images_are_persisted_individually_and_resume_without_double_calls(company_db):
    db, company, _, root = company_db
    request = CreativeRequest(mode="creative", product_id=company.id,
                              topic="Как выбрать букет", format="story",
                              platform="telegram", image_model="gemini")
    task = queue_creative(db, request)
    image_service = FakeImages()
    settings = Settings(_env_file=None, products_root=root, xai_api_key="test")
    assert await generate_images(db, settings, request, draft_story(), task_id=task.id,
                                 provider=image_service)
    assert len(image_service.calls) == 3
    assert len(generated_images(db, company.id, task.id)) == 3
    folder = image_folder(root, company.id, task.id)
    assert sorted(x.name for x in folder.iterdir()) == ["frame-01.png", "frame-02.png", "frame-03.png"]
    assert await generate_images(db, settings, request, draft_story(), task_id=task.id,
                                 provider=image_service)
    assert len(image_service.calls) == 3  # persisted frames are not charged again


@pytest.mark.asyncio
async def test_one_failed_frame_keeps_remaining_images(company_db):
    db, company, _, root = company_db
    request = CreativeRequest(mode="creative", product_id=company.id,
                              topic="Как выбрать букет", format="story",
                              platform="telegram", image_model="gemini")
    task = queue_creative(db, request)
    assert not await generate_images(
        db, Settings(_env_file=None, products_root=root, xai_api_key="test"),
        request, draft_story(), task_id=task.id, provider=FakeImages(fail=2))
    assert len(generated_images(db, company.id, task.id)) == 2
    failure = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "creative_image_error"))
    assert json.loads(failure.content)["frame_number"] == 2


@pytest.mark.asyncio
async def test_resolved_frame_error_disappears_from_gallery(company_db):
    from app.api.creative_image_gallery import gallery_html

    db, company, _, root = company_db
    request = CreativeRequest(mode="creative", product_id=company.id,
                              topic="Как выбрать букет", format="story",
                              platform="telegram", image_model="gemini")
    task = queue_creative(db, request)
    settings = Settings(_env_file=None, products_root=root, xai_api_key="test")
    draft = draft_story()
    assert not await generate_images(db, settings, request, draft, task_id=task.id,
                                     provider=FakeImages(fail=2))
    assert await generate_images(db, settings, request, draft, task_id=task.id,
                                 provider=FakeImages())
    errors = list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "creative_image_error")))
    assert errors  # A past failure is still available in the audit trail.
    rendered = gallery_html(company.id, task.id, generated_images(db, company.id, task.id), errors=errors)
    assert "Скачать все · 3 PNG" in rendered
    assert "не созданы" not in rendered


@pytest.mark.asyncio
async def test_owner_pages_show_gallery_and_download_png_zip(company_db):
    db, company, other, root = company_db
    request = CreativeRequest(mode="creative", product_id=company.id,
                              topic="Как выбрать букет", format="story",
                              platform="telegram", image_model="gemini")
    task = queue_creative(db, request)
    settings = Settings(_env_file=None, app_env="development",
                        products_root=root, xai_api_key="test")
    await generate_images(db, settings, request, draft_story(), task_id=task.id,
                          provider=FakeImages())
    image = generated_images(db, company.id, task.id)[0]
    def override_db():
        yield db
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            base = f"/studio/products/{company.id}/tasks/{task.id}"
            page = client.get(base)
            assert page.status_code == 200 and "Готовые изображения" in page.text
            assert "Скачать все" in page.text
            response = client.get(f"{base}/images/{image.id}?download=true")
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("image/png")
            assert "attachment" in response.headers["content-disposition"]
            assert response.content.startswith(b"\x89PNG")
            bundle = client.get(base + "/images.zip")
            assert bundle.status_code == 200
            with zipfile.ZipFile(io.BytesIO(bundle.content)) as zip_file:
                assert sorted(zip_file.namelist()) == [
                    "frame-01.png", "frame-02.png", "frame-03.png", "metadata.json"
                ]
                assert len(json.loads(zip_file.read("metadata.json"))) == 3
            forbidden = f"/studio/products/{other.id}/tasks/{task.id}"
            assert client.get(f"{forbidden}/images/{image.id}").status_code == 404
            assert client.get(forbidden + "/images.zip").status_code == 404
            assert client.get(f"{base}/images/{uuid4()}").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_creative_request_rejects_images_for_video_scripts():
    with pytest.raises(ValueError):
        CreativeRequest(mode="creative", product_id=uuid4(), topic="Видео про услуги",
                        format="short_video", platform="youtube_shorts", image_model="openai")


class FakeDirectorLLM:
    def __init__(self):
        self.calls = []

    async def generate(self, request):
        self.calls.append(request)
        if len(self.calls) == 1:
            result = {
                "audience_pain": "Клиенты не знают, с чего начать выбор цветов",
                "audience_insight": "Простой совет помогает избежать ненужной покупки",
                "hook": "Три совета для покупателя цветов",
                "core_takeaway": "Уточнить повод, предпочтения и время доставки",
                "cta": "Спросите о предпочтениях получателя",
                "evidence_ids": [],
            }
        else:
            result = draft_story().model_dump(mode="json")
        return LLMResponse(content=json.dumps(result, ensure_ascii=False),
                           model="fake", usage=LLMUsage(input_tokens=100, output_tokens=400))


@pytest.mark.asyncio
async def test_end_to_end_story_task_generates_three_real_images(monkeypatch, company_db):
    db, company, _, root = company_db
    request = CreativeRequest(mode="creative", product_id=company.id,
                              topic="Как выбрать букет", format="story", platform="telegram",
                              image_model="gemini")
    task = queue_creative(db, request)
    provider = FakeDirectorLLM()

    async def fake_image(settings, model, prompt, ratio):
        assert model == GEMINI_MODEL and ratio == "9:16"
        assert "EXACT visible words" in prompt
        return source_picture()

    monkeypatch.setattr("app.content.creative_images.request_image", fake_image)
    done = await run_creative_once(
        db, Settings(_env_file=None, products_root=root, xai_api_key="test",
                     agent_critic_enabled=False),
        request, task_id=task.id, provider=provider)
    assert done is True
    assert len(provider.calls) == 2
    assert len(generated_images(db, company.id, task.id)) == 3
    db.refresh(task)
    assert task.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_missing_image_key_fails_before_strategy_llm_cost(company_db):
    db, company, _, root = company_db
    request = CreativeRequest(mode="creative", product_id=company.id,
                              topic="Как выбрать букет", format="story", platform="telegram",
                              image_model="openai")
    task = queue_creative(db, request)
    fake = FakeDirectorLLM()
    done = await run_creative_once(
        db, Settings(_env_file=None, products_root=root, openai_api_key=None,
                     agent_critic_enabled=False), request, task_id=task.id, provider=fake)
    assert done is False
    assert fake.calls == []
    db.refresh(task)
    assert task.status == TaskStatus.FAILED


@pytest.mark.parametrize(("fmt", "platform", "requested", "ratio"), [
    ("story", "telegram", None, "9:16"),
    ("story", "vk", "9:16", "9:16"),
    ("static_image", "instagram_reels", None, "4:5"),
    ("static_image", "vk", None, "1:1"),
    ("static_image", "telegram", None, "1:1"),
    ("static_image", "dzen", "16:9", "16:9"),
    ("static_image", "instagram_reels", "1:1", "1:1"),
])
def test_platform_image_ratio(fmt, platform, requested, ratio):
    assert image_aspect_ratio(fmt, platform, requested=requested) == ratio


def test_story_cannot_be_horizontal_or_square():
    with pytest.raises(ValueError):
        image_aspect_ratio("story", "vk", requested="1:1")
    with pytest.raises(ValueError):
        CreativeRequest(mode="creative", product_id=uuid4(), topic="Четыре истории",
                        format="story", platform="telegram", aspect_ratio="4:5")
    with pytest.raises(ValueError):
        CreativeRequest(mode="creative", product_id=uuid4(), topic="Текст ролика",
                        format="short_video", platform="vk_clips", aspect_ratio="9:16")


@pytest.mark.parametrize(("source_format", "ratio", "size"), [
    ("PNG", "9:16", (900, 1600)),
    ("JPEG", "4:5", (960, 1200)),
    ("WEBP", "1:1", (1024, 1024)),
    ("GIF", "16:9", (1600, 900)),
])
def test_image_decoder_accepts_multiple_input_formats_and_sizes(source_format, ratio, size):
    source = Image.new("RGB", (731, 1099), (30, 100, 170))
    buf = io.BytesIO()
    source.save(buf, format=source_format)
    data = png_output(buf.getvalue(), ratio=ratio)
    with Image.open(io.BytesIO(data)) as result:
        assert result.format == "PNG"
        assert result.size == size


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider", "input_format", "kind", "ratio", "expected_size"), [
    ("gemini", "JPEG", "b64_json", "9:16", "1024x1792"),
    ("gemini", "WEBP", "data_uri", "1:1", "1024x1024"),
    ("openai", "WEBP", "b64_json", "4:5", "1024x1280"),
    ("openai", "JPEG", "data_uri", "16:9", "1792x1008"),
])
async def test_provider_correct_size_and_multiple_image_transport_forms(
    provider, input_format, kind, ratio, expected_size,
):
    image_data = io.BytesIO()
    Image.new("RGB", (840, 1280), (90, 150, 110)).save(image_data, input_format)
    encoded = base64.b64encode(image_data.getvalue()).decode("ascii")
    api_data = ({"b64_json": encoded} if kind == "b64_json" else
                {"image_url": {"url": f"data:image/{input_format.lower()};base64,{encoded}"}})
    calls = []

    def respond(request):
        data = json.loads(request.content)
        calls.append(data)
        return httpx.Response(200, json={"data": [api_data]})

    base = "https://api.claudehub.fun/v1/" if provider == "gemini" else "https://api.openai.com/v1/"
    async with httpx.AsyncClient(base_url=base, transport=httpx.MockTransport(respond)) as http:
        settings = Settings(_env_file=None, xai_api_key="gemini-test", openai_api_key="openai-test")
        model = GEMINI_MODEL if provider == "gemini" else OPENAI_MODEL
        answer = await request_image(settings, model, "Russian visual poster", ratio, client=http)
    assert len(calls) == 1
    assert calls[0]["size"] == expected_size
    with Image.open(io.BytesIO(answer)) as final:
        assert final.format == "PNG"
        assert final.size == {"9:16": (900, 1600), "4:5": (960, 1200),
                              "1:1": (1024, 1024), "16:9": (1600, 900)}[ratio]


@pytest.mark.asyncio
async def test_instagram_post_image_defaults_to_four_by_five(company_db):
    db, company, _, root = company_db
    request = CreativeRequest(mode="creative", product_id=company.id,
                              topic="Как выбрать букет", format="static_image",
                              platform="instagram_reels", image_model="gemini")
    task = queue_creative(db, request)
    original = draft_story().model_dump(mode="json")
    original["format"] = "static_image"
    original["platform"] = "instagram_reels"
    frame = original["frames"][0]
    frame["start_second"] = frame["end_second"] = 0
    original["frames"] = [frame]
    rendered = await generate_images(db, Settings(
        _env_file=None, products_root=root, xai_api_key="gemini-test",
    ), request, CreativeDraft.model_validate(original),
        task_id=task.id, provider=FakeImages())
    assert rendered is True
    record = generated_images(db, company.id, task.id)[0]
    assert json.loads(record.content)["ratio"] == "4:5"
    image_path = image_folder(root, company.id, task.id) / "frame-01.png"
    with Image.open(image_path) as picture:
        assert picture.size == (960, 1200)


@pytest.mark.asyncio
async def test_provider_rejects_remote_image_url():
    def handler(request):
        return httpx.Response(200, json={"data": [{"url": "https://untrusted.example/secret.png"}]})
    async with httpx.AsyncClient(
        base_url="https://api.openai.com/v1/", transport=httpx.MockTransport(handler),
    ) as http:
        with pytest.raises(ValueError, match="inline image"):
            await request_image(
                Settings(_env_file=None, openai_api_key="test"),
                OPENAI_MODEL, "A picture", "1:1", client=http,
            )


@pytest.mark.asyncio
async def test_orphaned_valid_frame_is_adopted_without_another_paid_image_call(company_db):
    db, company, _, root = company_db
    request = CreativeRequest(mode="creative", product_id=company.id,
                              topic="Как выбрать букет", format="story",
                              platform="telegram", image_model="gemini")
    task = queue_creative(db, request)
    folder = image_folder(root, company.id, task.id)
    folder.mkdir(parents=True)
    (folder / "frame-01.png").write_bytes(png_output(source_picture(), ratio="9:16"))
    image_service = FakeImages()
    config = Settings(_env_file=None, products_root=root, xai_api_key="test")
    assert await generate_images(db, config, request, draft_story(), task_id=task.id,
                                 provider=image_service)
    assert len(image_service.calls) == 2
    assert len(generated_images(db, company.id, task.id)) == 3


@pytest.mark.asyncio
async def test_missing_saved_frame_blocks_billed_regeneration(company_db):
    db, company, _, root = company_db
    request = CreativeRequest(mode="creative", product_id=company.id,
                              topic="Как выбрать букет", format="story",
                              platform="telegram", image_model="gemini")
    task = queue_creative(db, request)
    config = Settings(_env_file=None, products_root=root, xai_api_key="test")
    assert await generate_images(db, config, request, draft_story(), task_id=task.id,
                                 provider=FakeImages())
    (image_folder(root, company.id, task.id) / "frame-02.png").unlink()
    image_service = FakeImages()
    with pytest.raises(LookupError, match="Image not found"):
        await generate_images(db, config, request, draft_story(), task_id=task.id,
                              provider=image_service)
    assert len(image_service.calls) == 0


@pytest.mark.asyncio
async def test_failed_frame_resumes_with_one_missing_image_and_no_new_text_calls(monkeypatch, company_db):
    db, company, _, root = company_db
    request = CreativeRequest(mode="creative", product_id=company.id,
                              topic="Как выбрать букет", format="story",
                              platform="telegram", image_model="gemini", frame_count=3)
    task = queue_creative(db, request)
    settings = Settings(_env_file=None, products_root=root,
                        xai_api_key="test", agent_critic_enabled=False)
    text_calls = []
    image_calls = []

    class Provider:
        async def generate(self, call):
            text_calls.append(call)
            from test_content_creative import strategy_payload
            output = strategy_payload() if len(text_calls) == 1 else draft_story().model_dump(mode="json")
            return LLMResponse(content=json.dumps(output, ensure_ascii=False),
                               model="fake", usage=LLMUsage(input_tokens=10, output_tokens=20))

    async def image_provider(settings, model, prompt, ratio):
        image_calls.append(prompt)
        if len(image_calls) == 2:
            raise ValueError("image unavailable once")
        return png_output(source_picture(), ratio=ratio)

    monkeypatch.setattr("app.content.creative_images.request_image", image_provider)
    assert not await run_creative_once(db, settings, request, task_id=task.id, provider=Provider())
    assert db.get(Task, task.id).status == TaskStatus.FAILED
    assert len(text_calls) == 2 and len(image_calls) == 3
    assert len(generated_images(db, company.id, task.id)) == 2
    task.status = TaskStatus.QUEUED
    db.commit()
    assert await run_creative_once(db, settings, request, task_id=task.id, provider=Provider())
    assert len(text_calls) == 2 and len(image_calls) == 4
    assert len(generated_images(db, company.id, task.id)) == 3
    assert len(list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "creative_brief")))) == 1
    assert db.get(Task, task.id).status == TaskStatus.COMPLETED
