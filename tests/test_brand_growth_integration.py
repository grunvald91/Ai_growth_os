"""Brand Kit / Growth integration: pinned snapshots, retries, free renders and isolation."""
from __future__ import annotations

import io
import json
from uuid import UUID

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.content_studio_auth import require_studio_auth
from app.brand_kit.contracts import BrandStyle
from app.brand_kit.design_service import verified_render
from app.brand_kit.models import BrandActive, BrandVersion
from app.brand_kit.service import approve_version, save_draft, store_asset
from app.content.brand_story_pipeline import (
    brand_digest, compose_saved_stories, pin_brand_request, visible_images,
    verified_gallery_image,
)
from app.content.creative import CreativeRequest, queue_creative
from app.content.creative_images import generate_images, generated_images, GEMINI_MODEL
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Artifact, Product


def _image(color=(90, 70, 160)):
    out = io.BytesIO()
    Image.new("RGB", (500, 700), color).save(out, "PNG")
    return out.getvalue()


def _draft():
    from app.content.creative import CreativeDraft
    return CreativeDraft.model_validate({
        "format": "story", "platform": "telegram",
        "title": "Как выбрать букет", "hook": "Цветы для друзей",
        "cta": "Закажите букет онлайн", "cover_text": "Сюрприз для друзей",
        "visual_style": "Мягкий реализм и контрастная палитра на тёмном фоне",
        "caption": "Несколько практичных советов для выбора цветов без ошибок",
        "frames": [{
            "number": i + 1, "start_second": i * 5, "end_second": (i + 1) * 5,
            "purpose": "Польза для покупателя",
            "visual_prompt": "Мастерская с натуральными букетами, столом и живым освещением",
            "on_screen_text": f"Выбирайте сердцем · {i + 1}",
            "action_or_animation": "Медленный наезд камеры",
        } for i in range(3)],
    })


class FakeProvider:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    async def generate(self, prompt, *, model, ratio):
        self.calls.append((prompt, model, ratio))
        if self.fail and len(self.calls) == self.fail:
            raise ValueError("test generator failure")
        return _image()


@pytest.fixture
def brand_growth(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        a, b = Product(name="Цветочный салон", slug="grow-a"), Product(
            name="Другая компания", slug="grow-b")
        db.add_all([a, b]); db.commit()
        logo = store_asset(db, tmp_path, a.id, "original", _image((240, 200, 50)))
        style = BrandStyle.model_validate({
            "logos": {"original": str(logo.id)},
            "preferred_generator": "gemini",
            "palette": {"background": "#0D1B2A", "text": "#FAFAFA",
                        "primary": "#3366CC", "secondary": "#446688",
                        "accent": "#FFCC00"},
        })
        old = save_draft(db, tmp_path, a.id, style.model_dump(mode="json"))
        approve_version(db, tmp_path, a.id, old.id)
        def override_db(): yield db
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None, products_root=tmp_path, xai_api_key="unit-test",
            brand_stories_enabled=True, brand_story_canary_product_ids=str(a.id))
        with TestClient(app) as client:
            yield db, client, a, b, old, tmp_path, style
        app.dependency_overrides.clear()
    engine.dispose()


def _canary(company):
    return Settings(_env_file=None, brand_stories_enabled=True,
                    brand_story_canary_product_ids=str(company.id))


def _request(company, **kw):
    return CreativeRequest(mode="creative", product_id=company.id,
                           topic="Выбор букета", platform="telegram", format="story",
                           image_model="auto", brand_mode="auto", **kw)


@pytest.mark.asyncio
async def test_pin_checkpoint_and_reuse_paid_backgrounds_after_new_approval(brand_growth):
    db, client, company, other, old, root, style = brand_growth
    task = queue_creative(db, _request(company), settings=_canary(company))
    stored = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "creative_request"))
    pinned = CreativeRequest.model_validate_json(stored.content)
    assert pinned.brand_mode == "brand" and pinned.brand_version_id == old.id
    assert pinned.image_model == "gemini"
    assert pinned.brand_snapshot_sha256 == brand_digest(old.config)
    fake = FakeProvider()
    settings = Settings(_env_file=None, products_root=root, xai_api_key="unit-test")
    draft = _draft()
    assert await generate_images(db, settings, pinned, draft,
                                 task_id=task.id, provider=fake)
    assert len(fake.calls) == 3
    assert all("#3366CC" in call[0] for call in fake.calls)
    assert all("NO words" in call[0] or "NO words".lower() in call[0].lower()
               for call in fake.calls)
    assert compose_saved_stories(db, root, pinned, draft, task.id)
    assert len(visible_images(db, company.id, task.id)) == 3
    for record in visible_images(db, company.id, task.id):
        path = verified_gallery_image(db, root, company.id, task.id, record)
        with Image.open(path) as picture:
            assert picture.size == (900, 1600)
        metadata = json.loads(record.content)
        assert metadata["brand_version_id"] == str(old.id)
    changed = style.model_copy(deep=True)
    changed.palette.primary = "#FF0000"
    latest = save_draft(db, root, company.id, changed.model_dump(mode="json"))
    approve_version(db, root, company.id, latest.id)
    assert await generate_images(db, settings, pinned, draft,
                                 task_id=task.id, provider=fake)
    assert compose_saved_stories(db, root, pinned, draft, task.id)
    assert len(fake.calls) == 3
    assert len(visible_images(db, company.id, task.id)) == 3
    assert client.get(f"/studio/products/{other.id}/tasks/{task.id}/images.zip").status_code == 404


@pytest.mark.asyncio
async def test_partial_paid_failure_renders_good_frames_without_rebilling(brand_growth):
    db, _, company, _, _, root, _ = brand_growth
    task = queue_creative(db, _request(company), settings=_canary(company))
    request = CreativeRequest.model_validate_json(db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "creative_request")).content)
    fake = FakeProvider(fail=2)
    settings = Settings(_env_file=None, products_root=root, xai_api_key="unit-test")
    draft = _draft()
    assert not await generate_images(db, settings, request, draft,
                                     task_id=task.id, provider=fake)
    assert not compose_saved_stories(db, root, request, draft, task.id)
    assert len(visible_images(db, company.id, task.id)) == 2
    another = FakeProvider()
    assert await generate_images(db, settings, request, draft,
                                 task_id=task.id, provider=another)
    assert len(another.calls) == 1
    assert compose_saved_stories(db, root, request, draft, task.id)
    assert len(visible_images(db, company.id, task.id)) == 3


def test_own_images_render_locally_and_legacy_requests_unchanged(brand_growth):
    db, _, a, b, old, root, style = brand_growth
    request = _request(a)
    request = request.model_copy(update={"image_model": "none"})
    task = queue_creative(db, request, settings=_canary(a))
    pinned = CreativeRequest.model_validate_json(db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "creative_request")).content)
    assert pinned.brand_mode == "brand" and pinned.image_model == "none"
    assert compose_saved_stories(db, root, pinned, _draft(), task.id)
    assert len(visible_images(db, a.id, task.id)) == 3
    legacy = queue_creative(db, CreativeRequest(
        mode="creative", product_id=b.id, format="story", platform="telegram",
        topic="Другая компания", image_model="none"))
    saved = db.scalar(select(Artifact).where(
        Artifact.task_id == legacy.id, Artifact.kind == "creative_request"))
    assert json.loads(saved.content)["brand_mode"] == "classic"


def test_task_pin_is_required_and_wrong_brand_cannot_be_rendered(brand_growth):
    from app.brand_kit.design_service import create_composition
    from app.content.brand_story_pipeline import story_frame
    db, _, a, b, old, root, _ = brand_growth
    task = queue_creative(db, _request(a), settings=_canary(a))
    assert db.get(BrandActive, a.id).version_id == old.id
    # No cross-tenant access even if a valid pinned task UUID is supplied.
    with pytest.raises((LookupError, ValueError)):
        create_composition(db, root, b.id, old.id, story_frame(
            _draft(), _draft().frames[0], count=3), frame_key="x-company",
            pinned_task_id=task.id)
    assert db.scalar(select(BrandVersion).where(BrandVersion.id == old.id)) is not None


def test_brand_copy_is_checked_before_paid_background_call():
    from app.content.brand_story_pipeline import validate_brand_story_copy
    draft = _draft()
    validate_brand_story_copy(draft)
    malformed = draft.model_copy(deep=True)
    malformed.frames[0].on_screen_text = "Очень длинный текст " * 12
    with pytest.raises(ValueError, match="текст кадра"):
        validate_brand_story_copy(malformed)
    malformed = draft.model_copy(deep=True)
    malformed.cta = "Невероятно длинная рекламная фраза для призыва " * 5
    with pytest.raises(ValueError, match="призыв"):
        validate_brand_story_copy(malformed)



def test_strict_canary_default_off_and_company_allowlist(brand_growth):
    from app.content.brand_story_pipeline import brand_story_canary_allowed
    db, _, company, other, _, _, _ = brand_growth
    assert not brand_story_canary_allowed(Settings(_env_file=None), company.id)
    assert not brand_story_canary_allowed(
        Settings(_env_file=None, brand_stories_enabled=True), company.id)
    assert brand_story_canary_allowed(_canary(company), company.id)
    assert not brand_story_canary_allowed(_canary(company), other.id)
    assert not brand_story_canary_allowed(
        Settings(_env_file=None, brand_stories_enabled=True,
                 brand_story_canary_product_ids="garbage"), company.id)
    job = queue_creative(db, _request(company), settings=Settings(_env_file=None))
    stored = db.scalar(select(Artifact).where(
        Artifact.task_id == job.id, Artifact.kind == "creative_request"))
    assert json.loads(stored.content)["brand_mode"] == "classic"
    with pytest.raises(ValueError, match="пока не включены"):
        queue_creative(db, _request(company).model_copy(update={"brand_mode": "brand"}),
                       settings=Settings(_env_file=None))
    db.rollback()


def test_real_text_layout_preflight_rejects_long_word_before_billing(brand_growth):
    from app.content.brand_story_pipeline import preflight_brand_story_layout
    db, _, company, _, _, root, _ = brand_growth
    task = queue_creative(db, _request(company), settings=_canary(company))
    request = CreativeRequest.model_validate_json(db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "creative_request")).content)
    spec = _draft()
    preflight_brand_story_layout(db, root, request, spec, task.id)
    long_word = spec.model_copy(deep=True)
    long_word.frames[1].on_screen_text = "Щ" * 95
    with pytest.raises(ValueError, match="помещ"):
        preflight_brand_story_layout(db, root, request, long_word, task.id)



@pytest.mark.asyncio
async def test_kill_switch_stops_queued_paid_job_before_model_call(brand_growth):
    from app.content.creative import run_creative_once
    db, _, company, _, _, root, _ = brand_growth
    task = queue_creative(db, _request(company), settings=_canary(company))
    command = CreativeRequest.model_validate_json(db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "creative_request")).content)
    calls = []
    class PaidMustNotRun:
        async def generate(self, *args, **kwargs):
            calls.append(args)
            raise AssertionError("Paid LLM must not execute when canary is disabled")
    disabled = Settings(
        _env_file=None, products_root=root, xai_api_key="unit-test",
        brand_stories_enabled=False,
        brand_story_canary_product_ids=str(company.id))
    assert not await run_creative_once(db, disabled, command, task_id=task.id,
                                       provider=PaidMustNotRun())
    assert calls == []
