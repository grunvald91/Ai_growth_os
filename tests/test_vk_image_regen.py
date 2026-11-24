"""VK picture redraw: one user-authorized paid call, no post text rerun."""
from __future__ import annotations

import hashlib
import io
import json

import pytest
from PIL import Image
from sqlalchemy import select

from app.content.creative_images import image_folder
from app.content.revisions import source_fingerprint
from app.content.vk_image_critic import (
    VKImageIssue, VKImageReview, _post_digest, review_vk_post_image,
    verified_vk_image_approval,
)
from app.content.vk_image_regen import (
    queue_vk_image_redraw, restore_vk_redraw_failure, run_vk_image_redraw,
)
from app.core.config import Settings
from app.models.domain import Artifact, Task, TaskStatus
from app.models.domain import Product
from app.services.artifacts import ArtifactService


def _setup(db, tmp_path):
    product = Product(name="Тестовая мастерская", slug="vk-redraw-test")
    db.add(product)
    db.flush()
    task = Task(product_id=product.id, assigned_agent="content_factory",
                title="CONTENT: post:vk:redraw", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    picture = _png()
    folder = image_folder(tmp_path, product.id, task.id)
    folder.mkdir(parents=True)
    (folder / "frame-01.png").write_bytes(picture)
    digest = hashlib.sha256(picture).hexdigest()
    image = ArtifactService(db).create(
        product_id=product.id, task_id=task.id, kind="generated_image",
        title="VK cover", content=json.dumps({
            "filename": "frame-01.png", "frame_number": 1,
            "ratio": "4:5", "model": "gemini-3.1-flash-image", "sha256": digest,
        }),
    )
    post = ArtifactService(db).create(
        product_id=product.id, task_id=task.id, kind="channel_text",
        title="Как вернуть клиентов", content=json.dumps({
            "channel": "vk", "title": "Как вернуть клиентов",
            "body": "Как мастерская отвечает на пропущенные обращения",
            "image_model": "gemini", "image_ratio": "4:5",
            "image_state": "awaiting_review", "image_review_state": "pending",
            "image_artifact_id": str(image.id), "image_sha256": digest,
        }), status="awaiting_approval",
    )
    db.commit()
    return product, task, post, image


class FakeReviewer:
    def __init__(self, verdict):
        self.verdict = verdict

    async def review(self, image, *, company_name, title, body):
        return self.verdict


def issue(*, severity, category):
    return VKImageIssue(category=category, severity=severity, confidence="high",
                        evidence="Нарисован предмет, который противоречит смыслу поста")


def report(*issues):
    return VKImageReview(summary="Картинка проверена", issues=list(issues))



def _png(rgb=(20, 80, 130)):
    out = io.BytesIO()
    Image.new("RGB", (1080, 1350), rgb).save(out, format="PNG")
    return out.getvalue()


async def _blocked(db, tmp_path):
    product, task, post, image = _setup(db, tmp_path)
    settings = Settings(_env_file=None, products_root=tmp_path, xai_api_key="fake")
    reviewer = FakeReviewer(report(issue(severity="critical", category="contradiction")))
    assert await review_vk_post_image(
        db, settings, product=product, task=task, post=post, reviewer=reviewer,
    ) == "blocked"
    return product, task, post, image, settings


@pytest.mark.asyncio
async def test_explicit_redraw_replaces_picture_but_not_text_and_requires_new_critic(
    db, tmp_path, monkeypatch,
):
    product, origin, post, previous, settings = await _blocked(db, tmp_path)
    before = json.loads(post.content)
    assert not verified_vk_image_approval(db, post, previous)
    job = queue_vk_image_redraw(
        db, product.id, post.id, source_version=post.version,
        source_digest=source_fingerprint(post), feedback="Исправьте изображение телефона",
    )
    assert job.status == TaskStatus.QUEUED
    assert json.loads(post.content)["image_state"] == "regenerating"
    with pytest.raises(ValueError, match="уже переделывается"):
        queue_vk_image_redraw(
            db, product.id, post.id, source_version=post.version,
            source_digest=source_fingerprint(post))
    calls = []
    async def image_call(*args, **kwargs):
        calls.append("image")
        return _png((130, 80, 20))
    async def review_call(self, image_bytes, *, company_name, title, body):
        calls.append("critic")
        assert hashlib.sha256(image_bytes).hexdigest() != before["image_sha256"]
        return VKImageReview(summary="Картинка без серьёзных недочётов.")
    monkeypatch.setattr("app.content.vk_image_regen.request_image", image_call)
    monkeypatch.setattr(
        "app.content.vk_image_critic.VKImageVisionReviewer.review", review_call)
    job.status = TaskStatus.RUNNING
    db.commit()
    new_image = await run_vk_image_redraw(db, settings, task_id=job.id)
    db.refresh(post)
    after = json.loads(post.content)
    assert after["title"] == before["title"]
    assert after["body"] == before["body"]
    assert _post_digest(after) == _post_digest(before)
    assert after["image_artifact_id"] == str(new_image.id)
    assert after["image_sha256"] != before["image_sha256"]
    assert after["image_review_state"] == "approved"
    assert after["image_state"] == "ready"
    assert verified_vk_image_approval(db, post, new_image)
    assert calls == ["image", "critic"]
    assert len(db.scalars(select(Artifact).where(
        Artifact.task_id == origin.id, Artifact.kind == "vk_image_critique")).all()) == 2
    assert len(db.scalars(select(Artifact).where(
        Artifact.task_id == origin.id, Artifact.kind == "generated_image")).all()) == 2
    folder = image_folder(tmp_path, product.id, origin.id)
    assert (folder / "frame-01.png").is_file()
    assert (folder / "frame-02.png").is_file()
    assert json.loads(previous.content)["sha256"] == before["image_sha256"]
    assert len(db.scalars(select(Artifact).where(
        Artifact.task_id == job.id, Artifact.kind == "vk_image_redraw_intent")).all()) == 1

    # An old browser form for the rejected image must not approve the new image.
    from app.content.review import ReviewError, approve_material
    with pytest.raises(ReviewError, match="Изображение не согласовано"):
        approve_material(
            db, tmp_path, product.id, post.id, image_confirmed=True,
            expected_image_sha256=before["image_sha256"],
            expected_post_sha256=_post_digest(before),
        )
    db.refresh(post)
    assert json.loads(post.content)["image_state"] == "ready"


@pytest.mark.asyncio
async def test_redraw_never_bills_again_after_started_intent(db, tmp_path, monkeypatch):
    product, origin, post, previous, settings = await _blocked(db, tmp_path)
    job = queue_vk_image_redraw(
        db, product.id, post.id, source_version=post.version,
        source_digest=source_fingerprint(post))
    async def failing_provider(*args, **kwargs):
        raise TimeoutError("upstream unknown")
    monkeypatch.setattr("app.content.vk_image_regen.request_image", failing_provider)
    job.status = TaskStatus.RUNNING
    db.commit()
    with pytest.raises(TimeoutError):
        await run_vk_image_redraw(db, settings, task_id=job.id)
    with pytest.raises(ValueError, match="повтор запрещён"):
        await run_vk_image_redraw(db, settings, task_id=job.id)
    db.rollback()
    restore_vk_redraw_failure(db, job.id, "TimeoutError")
    current = json.loads(post.content)
    assert current["image_state"] == "blocked"
    assert current["image_artifact_id"] == str(previous.id)
    assert current["image_regeneration_error_code"] == "TimeoutError"
    assert "image_redraw_task_id" not in current


@pytest.mark.asyncio
async def test_failed_critic_keeps_new_image_but_never_reuses_old_veto(db, tmp_path, monkeypatch):
    product, origin, post, previous, settings = await _blocked(db, tmp_path)
    job = queue_vk_image_redraw(
        db, product.id, post.id, source_version=post.version,
        source_digest=source_fingerprint(post))
    monkeypatch.setattr(
        "app.content.vk_image_regen.request_image",
        lambda *args, **kwargs: _async_image(_png((80, 100, 150))),
    )
    async def broken_critic(*args, **kwargs):
        raise RuntimeError("vision unavailable")
    monkeypatch.setattr("app.content.vk_image_regen.review_vk_post_image", broken_critic)
    job.status = TaskStatus.RUNNING
    db.commit()
    with pytest.raises(RuntimeError):
        await run_vk_image_redraw(db, settings, task_id=job.id)
    db.rollback()
    restore_vk_redraw_failure(db, job.id, "RuntimeError")
    db.refresh(post)
    current = json.loads(post.content)
    assert current["image_artifact_id"] != str(previous.id)
    assert current["image_review_state"] == "unavailable"
    assert current["image_state"] == "review_unavailable"
    assert not verified_vk_image_approval(db, post, previous)


async def _async_image(data):
    return data


@pytest.mark.asyncio
async def test_redraw_stale_form_and_approved_post_are_rejected(db, tmp_path):
    product, origin, post, previous, settings = await _blocked(db, tmp_path)
    with pytest.raises(ValueError, match="изменился"):
        queue_vk_image_redraw(
            db, product.id, post.id, source_version=post.version,
            source_digest="outdated")
    from app.models.domain import ArtifactStatus
    post.status = ArtifactStatus.APPROVED
    db.commit()
    with pytest.raises(ValueError, match="до утверждения"):
        queue_vk_image_redraw(
            db, product.id, post.id, source_version=post.version,
            source_digest=source_fingerprint(post))


@pytest.mark.asyncio
async def test_redraw_is_consumed_by_real_content_worker_without_rewriting_post(
    db, tmp_path, monkeypatch,
):
    from contextlib import contextmanager
    from app.content.worker import run_content_once
    from app.api.content_studio import material_detail_page

    product, origin, post, old_image, settings = await _blocked(db, tmp_path)
    page = material_detail_page(db, product, post, root=tmp_path)
    assert "Переделать картинку" in page
    assert 'name="paid_confirmed"' in page
    old_text = json.loads(post.content)["body"]
    job = queue_vk_image_redraw(
        db, product.id, post.id, source_version=post.version,
        source_digest=source_fingerprint(post), feedback="Другая композиция",
    )
    async def fake_image(*args, **kwargs):
        return _png((110, 80, 100))
    async def fake_critic(self, picture, *, company_name, title, body):
        return VKImageReview(summary="Готова новая картинка.")
    monkeypatch.setattr("app.content.vk_image_regen.request_image", fake_image)
    monkeypatch.setattr(
        "app.content.vk_image_critic.VKImageVisionReviewer.review", fake_critic)

    @contextmanager
    def factory():
        yield db

    assert await run_content_once(factory, settings) == 1
    db.refresh(job)
    db.refresh(post)
    assert job.status == TaskStatus.COMPLETED
    content = json.loads(post.content)
    assert content["body"] == old_text
    assert content["image_state"] == "ready"
    assert "image_redraw_task_id" not in content
    page = material_detail_page(db, product, post, root=tmp_path)
    assert "Утвердить материал" in page
    assert "Переделать картинку" in page


def test_material_preview_displays_saved_image_even_without_valid_critic(db, tmp_path):
    from app.api.content_studio import material_detail_page
    product, task, post, image = _setup(db, tmp_path)
    value = json.loads(post.content)
    value["image_state"] = "ready"
    value["image_review_state"] = "approved"
    post.content = json.dumps(value)
    db.commit()
    page = material_detail_page(db, product, post, root=tmp_path)
    uri = f"/studio/products/{product.id}/tasks/{task.id}/images/{image.id}"
    assert f'<img src="{uri}"' in page
    assert page.index(uri) < page.index('id="material-copy"')
    assert "согласование этой версии не подтверждено" in page
    assert "Утвердить материал</button>" not in page
    assert "Утвердить картинку и пост" not in page


def test_material_without_picture_has_visible_generation_action_before_text(db, tmp_path):
    from app.api.content_studio import material_detail_page
    product, task, post, image = _setup(db, tmp_path)
    value = json.loads(post.content)
    value.pop("image_artifact_id")
    value.pop("image_sha256")
    value["image_state"] = "failed"
    post.content = json.dumps(value)
    db.commit()
    page = material_detail_page(db, product, post, root=tmp_path)
    assert "Изображение не создано. Текст сохранён." in page
    assert page.index("Создать картинку") < page.index('id="material-copy"')
    assert 'name="paid_confirmed"' in page
    assert "Утвердить материал</button>" not in page
    assert "Утвердить картинку и пост" not in page
