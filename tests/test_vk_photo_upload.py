"""VK photo publication: real upload protocol mocked at external network edge."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.comments.vk import VKAdapter
from app.comments.models import CommentChannel
from app.content.creative_images import image_folder
from app.content.vk_image_critic import REVIEW_POLICY, _post_digest
from app.content.vk_photo_upload import (
    VKPhotoUploadError, _upload_destination, prepared_image, upload_wall_photo,
)
from app.core.config import Settings
from app.models.domain import Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus
from app.social_vk import VKStore, connect_community, deliver_once, schedule


@contextmanager
def sessions(db):
    yield db


def image_post(db, root, *, slug="vk-image-test", verdict="approved"):
    company = Product(name="Мастерская", slug=slug)
    db.add(company)
    db.flush()
    task = Task(product_id=company.id, title="CONTENT: post:vk", status=TaskStatus.COMPLETED)
    db.add(task)
    db.flush()
    raw = io.BytesIO()
    Image.new("RGB", (1080, 1350), (12, 40, 80)).save(raw, format="PNG")
    picture = raw.getvalue()
    digest = hashlib.sha256(picture).hexdigest()
    folder = image_folder(root, company.id, task.id)
    folder.mkdir(mode=0o700, parents=True)
    (folder / "frame-01.png").write_bytes(picture)
    image = Artifact(
        product_id=company.id, task_id=task.id,
        kind="generated_image", title="Рисунок",
        content=json.dumps({
            "filename": "frame-01.png", "sha256": digest,
            "model": "gemini-3.1-flash-image",
        }), status=ArtifactStatus.DRAFT,
    )
    db.add(image)
    db.flush()
    value = {
        "channel": "vk", "title": "Пропущенные звонки",
        "body": "Когда мастер занят, клиент может не дождаться ответа.",
        "image_model": "gemini", "image_artifact_id": str(image.id),
        "image_sha256": digest, "image_state": "ready" if verdict == "approved" else "blocked",
        "image_review_state": verdict,
    }
    review = Artifact(
        product_id=company.id, task_id=task.id,
        kind="vk_image_critique", title="Заключение",
        content=json.dumps({
            "policy": REVIEW_POLICY, "image_sha256": digest,
            "post_sha256": _post_digest(value),
            "decision": verdict,
            "review": {
                "summary": "Картинка проверена по смыслу.",
                "issues": ([] if verdict == "approved" else [{
                    "category": "contradiction", "severity": "critical",
                    "confidence": "high",
                    "evidence": "Картинка противоречит основной мысли материала.",
                }]),
            },
        }), status=ArtifactStatus.DRAFT,
    )
    db.add(review)
    db.flush()
    value["image_review_artifact_id"] = str(review.id)
    post = Artifact(
        product_id=company.id, task_id=task.id, kind="channel_text",
        title=value["title"], content=json.dumps(value, ensure_ascii=False),
        status=ArtifactStatus.APPROVED,
    )
    db.add(post)
    db.commit()
    return company, post, image, folder / "frame-01.png"


@pytest.mark.parametrize("url", [
    "http://pu.vk.com/upload.php",
    "https://pu.vk.com.evil.test/upload.php",
    "https://127.0.0.1/upload.php",
    "https://pu.vk.com:8080/upload.php",
    "https://operator:secret@pu.vk.com/upload.php",
    "https://pu.vk.com/upload.php#fragment",
    "https://pu.vk.com.evil.test@evil.test/upload.php",
])
def test_only_strict_vk_upload_destinations(url):
    with pytest.raises(VKPhotoUploadError):
        _upload_destination(url)


def test_valid_vk_upload_hosts():
    assert _upload_destination("https://pu.vk.ru/c1/upload.php?act=add") == (
        "https://pu.vk.ru/c1/upload.php?act=add"
    )
    assert _upload_destination("https://pu.vk.com/upload.php") == "https://pu.vk.com/upload.php"


@pytest.mark.asyncio
async def test_photo_upload_protocol_returns_real_attachment_with_owner_id(tmp_path):
    png = io.BytesIO()
    Image.new("RGB", (10, 12)).save(png, format="PNG")
    path = tmp_path / "image.png"
    path.write_bytes(png.getvalue())
    calls = []

    class Adapter:
        async def call(self, method, token, **params):
            calls.append((method, token, params))
            if method == "photos.getWallUploadServer":
                return {"upload_url": "https://pu.vk.ru/c123/upload.php?token=signed"}
            if method == "photos.saveWallPhoto":
                return [{"owner_id": 923, "id": 174}]
            raise AssertionError("Unexpected VK method")

    def handler(request):
        assert request.url.host == "pu.vk.ru"
        assert request.headers.get("authorization") is None
        assert b"secret-vk-token" not in request.content
        assert b"image/png" in request.content
        assert png.getvalue() in request.content
        assert request.method == "POST"
        return httpx.Response(200, json={"server": 777, "photo": "encoded", "hash": "signed_hash"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        attachment = await upload_wall_photo(Adapter(), "secret-vk-token", "123456", path, client=client)

    assert attachment == "photo923_174"
    assert [call[0] for call in calls] == [
        "photos.getWallUploadServer", "photos.saveWallPhoto",
    ]
    assert calls[0] == ("photos.getWallUploadServer", "secret-vk-token", {"group_id": 123456})
    assert calls[1][2] == {
        "group_id": 123456, "server": 777, "photo": "encoded", "hash": "signed_hash",
    }


@pytest.mark.asyncio
async def test_community_token_upload_error_fails_before_post(tmp_path):
    from app.comments.gateway import PlatformError

    class BlockedAdapter:
        async def call(self, method, token, **params):
            raise PlatformError("VK error 27")

    with pytest.raises(VKPhotoUploadError, match="ключом"):
        await upload_wall_photo(BlockedAdapter(), "community-token", "123456", tmp_path / "not-used")


def test_approved_image_is_required_and_pinned_at_schedule_time(db, tmp_path):
    company, post, image, path = image_post(db, tmp_path)
    connect_community(db, Settings(_env_file=None, products_root=tmp_path),
                      company.id, "123456", "community-token")
    when = datetime.now(timezone.utc) + timedelta(minutes=15)
    with pytest.raises(ValueError, match="проверенная версия"):
        schedule(db, company.id, post.id, when)
    pub = schedule(db, company.id, post.id, when, products_root=tmp_path)
    assert pub.status == "scheduled"
    intent = db.scalar(select(Artifact).where(
        Artifact.product_id == company.id, Artifact.kind == "vk_publish_intent",
    ))
    value = json.loads(intent.content)
    assert value["image_artifact_id"] == str(image.id)
    assert value["image_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="уже назначена"):
        schedule(db, company.id, post.id, when, products_root=tmp_path)


@pytest.mark.asyncio
async def test_approved_post_image_reaches_wall_post_once_and_not_text_only(db, tmp_path, monkeypatch):
    company, post, image, path = image_post(db, tmp_path, slug="vk-wall-image")
    settings = Settings(_env_file=None, products_root=tmp_path, content_vk_autopost_enabled=True)
    connect_community(db, settings, company.id, "123456", "community-token")
    pub = schedule(db, company.id, post.id, datetime.now(timezone.utc) + timedelta(seconds=2),
                   products_root=tmp_path)
    uploads, posts = [], []

    async def upload(adapter, token, group, picture, *, expected_sha256=None):
        assert expected_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert token == "community-token"
        assert group == "123456"
        assert picture == path
        uploads.append(picture)
        return "photo923_174"

    async def vk(self, method, token, **params):
        assert method == "wall.post"
        posts.append((token, params))
        return {"post_id": 884}

    monkeypatch.setattr("app.social_vk.upload_wall_photo", upload)
    monkeypatch.setattr(VKAdapter, "call", vk)
    due = datetime.now(timezone.utc) + timedelta(seconds=5)
    assert await deliver_once(lambda: sessions(db), settings, now=due) == 1
    db.refresh(pub)
    assert pub.status == "published"
    assert pub.external_reference == "vk:-123456_884"
    assert len(uploads) == len(posts) == 1
    assert posts[0][1] == {
        "owner_id": -123456, "from_group": 1,
        "message": "Когда мастер занят, клиент может не дождаться ответа.",
        "guid": str(pub.id), "attachments": "photo923_174",
    }
    assert await deliver_once(lambda: sessions(db), settings, now=due) == 0


@pytest.mark.asyncio
async def test_vk_rejects_missing_or_changed_approved_image_without_call(db, tmp_path, monkeypatch):
    company, post, image, path = image_post(db, tmp_path, slug="changed-photo")
    settings = Settings(_env_file=None, products_root=tmp_path, content_vk_autopost_enabled=True)
    connect_community(db, settings, company.id, "123456", "community-token")
    pub = schedule(db, company.id, post.id, datetime.now(timezone.utc) + timedelta(seconds=1),
                   products_root=tmp_path)
    path.write_bytes(b"modified")
    async def forbidden(self, method, token, **params):
        pytest.fail("VK must not be called after modifying the approved image")
    monkeypatch.setattr(VKAdapter, "call", forbidden)
    assert await deliver_once(lambda: sessions(db), settings,
                              now=datetime.now(timezone.utc) + timedelta(seconds=3)) == 1
    db.refresh(pub)
    assert pub.status == "failed_before_send"


@pytest.mark.asyncio
async def test_vk_upload_failure_never_posts_text_and_reports_reason(db, tmp_path, monkeypatch):
    company, post, image, path = image_post(db, tmp_path, slug="blocked-upload")
    settings = Settings(_env_file=None, products_root=tmp_path, content_vk_autopost_enabled=True)
    connect_community(db, settings, company.id, "123456", "community-token")
    pub = schedule(db, company.id, post.id, datetime.now(timezone.utc) + timedelta(seconds=1),
                   products_root=tmp_path)
    async def broken_upload(adapter, token, group, picture, *, expected_sha256=None):
        raise VKPhotoUploadError("ВК не разрешил загрузку фото этим ключом")
    async def forbidden(self, method, token, **params):
        pytest.fail("No text fallback allowed after image upload failed")
    monkeypatch.setattr("app.social_vk.upload_wall_photo", broken_upload)
    monkeypatch.setattr(VKAdapter, "call", forbidden)
    due = datetime.now(timezone.utc) + timedelta(seconds=3)
    assert await deliver_once(lambda: sessions(db), settings, now=due) == 1
    db.refresh(pub)
    assert pub.status == "failed_before_send"
    issue = db.scalar(select(Artifact).where(
        Artifact.product_id == company.id, Artifact.kind == "vk_publish_issue",
    ))
    assert "загрузку фото" in json.loads(issue.content)["reason"]
    assert await deliver_once(lambda: sessions(db), settings, now=due) == 0


@pytest.mark.asyncio
async def test_vk_wall_post_unknown_result_prevents_retry_with_image(db, tmp_path, monkeypatch):
    company, post, image, path = image_post(db, tmp_path, slug="wall-timeout")
    settings = Settings(_env_file=None, products_root=tmp_path, content_vk_autopost_enabled=True)
    connect_community(db, settings, company.id, "123456", "community-token")
    pub = schedule(db, company.id, post.id, datetime.now(timezone.utc) + timedelta(seconds=1),
                   products_root=tmp_path)
    calls = []
    async def upload(adapter, token, group, picture, *, expected_sha256=None):
        return "photo123_456"
    async def timeout(self, method, token, **params):
        calls.append(method)
        raise httpx.TimeoutException("VK wall.post response lost")
    monkeypatch.setattr("app.social_vk.upload_wall_photo", upload)
    monkeypatch.setattr(VKAdapter, "call", timeout)
    due = datetime.now(timezone.utc) + timedelta(seconds=3)
    assert await deliver_once(lambda: sessions(db), settings, now=due) == 1
    db.refresh(pub)
    assert pub.status == "delivery_unknown"
    assert calls == ["wall.post"]
    assert await deliver_once(lambda: sessions(db), settings, now=due) == 0


def test_blocked_image_cannot_be_scheduled_even_if_post_status_is_approved(db, tmp_path):
    company, post, image, path = image_post(db, tmp_path, slug="image-rejected",
                                            verdict="blocked")
    connect_community(db, Settings(_env_file=None, products_root=tmp_path),
                      company.id, "123456", "community-token")
    with pytest.raises(ValueError, match="Критик"):
        schedule(db, company.id, post.id, datetime.now(timezone.utc) + timedelta(minutes=10),
                 products_root=tmp_path)
    assert db.scalar(select(Publication).where(Publication.product_id == company.id)) is None


@pytest.mark.asyncio
async def test_separately_connected_legacy_user_token_uploads_photo_but_group_key_posts(
    db, tmp_path, monkeypatch,
):
    company, post, image, path = image_post(db, tmp_path, slug="dual-credentials")
    settings = Settings(_env_file=None, products_root=tmp_path, content_vk_autopost_enabled=True)
    connect_community(db, settings, company.id, "123456", "community-token")
    user = CommentChannel(product_id=company.id, platform="vk",
                          account_id="123456", account_name="Старая авторизация",
                          status="connected")
    db.add(user)
    db.commit()
    VKStore(settings.products_root).save_token(user.id, "vk1.a.classic-user-key")
    pub = schedule(db, company.id, post.id, datetime.now(timezone.utc) + timedelta(seconds=1),
                   products_root=tmp_path)
    calls = []

    async def manager(self, account, token):
        assert account == "123456" and token == "vk1.a.classic-user-key"
        calls.append("manager")
        return {"id": 123456}

    async def upload(adapter, token, group, picture, *, expected_sha256=None):
        assert token == "vk1.a.classic-user-key"
        assert expected_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        calls.append("image")
        return "photo923_174"

    async def post(self, method, token, **params):
        assert method == "wall.post"
        assert token == "community-token"
        assert params["attachments"] == "photo923_174"
        calls.append("post")
        return {"post_id": 333}

    monkeypatch.setattr(VKAdapter, "manager", manager)
    monkeypatch.setattr(VKAdapter, "call", post)
    monkeypatch.setattr("app.social_vk.upload_wall_photo", upload)
    assert await deliver_once(lambda: sessions(db), settings,
                              now=datetime.now(timezone.utc) + timedelta(seconds=3)) == 1
    db.refresh(pub)
    assert pub.status == "published"
    assert calls == ["manager", "image", "post"]


@pytest.mark.asyncio
async def test_photo_upload_rechecks_digest_to_reject_file_swap(tmp_path):
    data = io.BytesIO()
    Image.new("RGB", (12, 15)).save(data, format="PNG")
    path = tmp_path / "photo.png"
    path.write_bytes(data.getvalue())
    known_good = hashlib.sha256(path.read_bytes()).hexdigest()
    # The original approved file was replaced after the worker's preflight.
    Image.new("RGB", (12, 15), (45, 100, 200)).save(path, format="PNG")

    class Adapter:
        async def call(self, method, token, **params):
            assert method == "photos.getWallUploadServer"
            return {"upload_url": "https://pu.vk.ru/upload.php"}

    with pytest.raises(VKPhotoUploadError, match="изменилось"):
        await upload_wall_photo(Adapter(), "secret", "123456", path, expected_sha256=known_good)
