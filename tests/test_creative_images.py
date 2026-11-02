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

from app.content.creative import CreativeDraft, CreativeRequest, queue_creative
from app.content.creative_images import (
    GEMINI_MODEL, OPENAI_MODEL, ImageProviderUnavailable, generate_images,
    generated_images, image_folder, png_output, request_image, resolve_model,
)
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Artifact, Product


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
