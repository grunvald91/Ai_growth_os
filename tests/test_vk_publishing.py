"""VK OAuth and approved-post publisher regression tests (no live provider calls)."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import select

from app.comments.models import CommentChannel
from app.core.config import Settings
from app.models.domain import Artifact, Product, Publication
from app.social_vk import (
    VKStore, begin_oauth, read_oauth_cookie, approved_posts,
    cancel, schedule, deliver_once, redirect_uri, connect_community, connected,
)


@contextmanager
def sessions(db):
    yield db


def business_and_post(db, *, slug="vk-test", approved=True):
    product = Product(name="Студия", slug=slug)
    db.add(product)
    db.flush()
    post = Artifact(product_id=product.id, kind="channel_text",
                    title="Тестовый пост", status="approved" if approved else "draft",
                    content=json.dumps({"channel": "vk", "body": "Проверка интеграции ServGo"}))
    db.add(post)
    db.commit()
    return product, post


def attach_vk(db, product_id, account_id="123456"):
    row = CommentChannel(product_id=product_id, platform="vk", account_id=account_id,
                         account_name="Наше сообщество", status="connected")
    db.add(row)
    db.commit()
    return row


def test_vk_oauth_pkce_and_company_binding(tmp_path):
    settings = Settings(products_root=tmp_path, social_vk_client_id="12345678")
    url, cookie = begin_oauth(settings, "3ecb2135-1077-428e-9a65-ea1d895a8432", "123456")
    assert url.startswith("https://id.vk.ru/authorize?")
    assert "code_challenge_method=s256" in url
    from urllib.parse import urlsplit, parse_qs
    params = parse_qs(urlsplit(url).query)
    assert params["app_id"] == ["12345678"]
    assert params["client_id"] == ["12345678"]
    assert "scope=groups+wall" in url
    assert "code_verifier" not in url
    assert redirect_uri(settings) == "https://servgo.ru/studio/social/vk/callback"
    from urllib.parse import urlsplit, parse_qs
    state = parse_qs(urlsplit(url).query)["state"][0]
    result = read_oauth_cookie(settings, cookie, state)
    assert result["account_id"] == "123456"
    assert result["product_id"] == "3ecb2135-1077-428e-9a65-ea1d895a8432"
    with pytest.raises(ValueError):
        read_oauth_cookie(settings, cookie, "malicious-state")
    with pytest.raises(ValueError):
        read_oauth_cookie(settings, cookie[:-8] + "tampered", state)
    assert "123456" not in cookie


def test_vk_oauth_rejects_bad_group_or_missing_app(tmp_path):
    settings = Settings(products_root=tmp_path, social_vk_client_id="12345678")
    for value in ("-1234", "club12", "0", "abc"):
        with pytest.raises(ValueError):
            begin_oauth(settings, "3ecb2135-1077-428e-9a65-ea1d895a8432", value)
    with pytest.raises(ValueError):
        begin_oauth(Settings(products_root=tmp_path, social_vk_client_id=None),
                    "3ecb2135-1077-428e-9a65-ea1d895a8432", "123456")


def test_vk_approval_and_company_guards(db):
    company, post = business_and_post(db)
    other = Product(name="Чужая", slug="other-vk")
    db.add(other)
    db.commit()
    ch = attach_vk(db, company.id)
    assert approved_posts(db, company.id) == [post]
    when = datetime.now(timezone.utc) + timedelta(minutes=30)
    with pytest.raises(ValueError):
        schedule(db, other.id, post.id, when)
    publication = schedule(db, company.id, post.id, when)
    assert publication.channel == "vk" and publication.status == "scheduled"
    assert publication.product_id == company.id
    assert db.scalar(select(Artifact).where(Artifact.kind == "vk_publish_intent",
                                         Artifact.product_id == company.id)) is not None
    with pytest.raises(ValueError):
        schedule(db, company.id, post.id, when)
    with pytest.raises(LookupError):
        cancel(db, other.id, publication.id)
    assert cancel(db, company.id, publication.id).status == "cancelled"
    assert ch.status == "connected"


def test_vk_draft_cannot_be_scheduled(db):
    company, post = business_and_post(db, slug="draft-vk", approved=False)
    attach_vk(db, company.id)
    with pytest.raises(ValueError):
        schedule(db, company.id, post.id, datetime.now(timezone.utc) + timedelta(days=1))


@pytest.mark.asyncio
async def test_vk_worker_success_and_no_duplicate_send(db, tmp_path):
    product, post = business_and_post(db, slug="success-vk")
    attach_vk(db, product.id)
    pub = schedule(db, product.id, post.id, datetime.now(timezone.utc) + timedelta(seconds=5))
    settings = Settings(products_root=tmp_path, content_vk_autopost_enabled=True)
    calls = []

    async def publish(group, text, guid):
        calls.append((group, text, guid))
        return 123

    assert await deliver_once(lambda: sessions(db), settings, sender=publish,
                              now=datetime.now(timezone.utc) + timedelta(seconds=10)) == 1
    db.refresh(pub)
    assert pub.status == "published" and pub.external_reference == "vk:-123456_123"
    assert len(calls) == 1 and calls[0][2] == str(pub.id)
    assert await deliver_once(lambda: sessions(db), settings, sender=publish,
                              now=datetime.now(timezone.utc) + timedelta(seconds=11)) == 0
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_vk_worker_unknown_delivery_no_retry(db, tmp_path):
    product, post = business_and_post(db, slug="failure-vk")
    attach_vk(db, product.id)
    pub = schedule(db, product.id, post.id, datetime.now(timezone.utc) + timedelta(seconds=5))
    settings = Settings(products_root=tmp_path, content_vk_autopost_enabled=True)
    calls = []

    async def fail(group, text, guid):
        calls.append(guid)
        raise TimeoutError("upstream timeout")

    assert await deliver_once(lambda: sessions(db), settings, sender=fail,
                              now=datetime.now(timezone.utc) + timedelta(seconds=10)) == 1
    db.refresh(pub)
    assert pub.status == "delivery_unknown"
    assert await deliver_once(lambda: sessions(db), settings, sender=fail,
                              now=datetime.now(timezone.utc) + timedelta(minutes=20)) == 0
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_vk_worker_disabled_and_changed_approved_material(db, tmp_path):
    product, post = business_and_post(db, slug="change-vk")
    attach_vk(db, product.id)
    pub = schedule(db, product.id, post.id, datetime.now(timezone.utc) + timedelta(seconds=5))
    calls = []

    async def publish(group, text, guid):
        calls.append(guid)
        return 12

    later = datetime.now(timezone.utc) + timedelta(seconds=10)
    assert await deliver_once(lambda: sessions(db),
                              Settings(products_root=tmp_path, content_vk_autopost_enabled=False),
                              sender=publish, now=later) == 0
    post.version += 1
    db.commit()
    assert await deliver_once(lambda: sessions(db),
                              Settings(products_root=tmp_path, content_vk_autopost_enabled=True),
                              sender=publish, now=later) == 1
    db.refresh(pub)
    assert pub.status == "failed_before_send"
    assert not calls


def test_vk_publisher_key_is_encrypted_and_isolated(db, tmp_path):
    product, post = business_and_post(db, slug="community-key")
    second = Product(name="Чужая компания", slug="community-key-other")
    db.add(second)
    db.commit()
    settings = Settings(products_root=tmp_path)
    private_key = "vk1.a.pretendSecretForTests"
    connection = connect_community(db, settings, product.id, "123456", private_key)
    assert connection.kind == "vk_publish_connection"
    assert connection.product_id == product.id
    assert connected(db, product.id).is_community_token is True
    assert connected(db, second.id) is None
    assert VKStore(tmp_path).load_token(connection.id) == private_key
    assert private_key.encode() not in VKStore(tmp_path).path(connection.id).read_bytes()
    with pytest.raises(ValueError):
        connect_community(db, settings, second.id, "-321", private_key)
    when = datetime.now(timezone.utc) + timedelta(minutes=25)
    pub = schedule(db, product.id, post.id, when)
    assert pub.product_id == product.id


def test_vk_publisher_key_rotation_invalidates_previous_scheduled_post(db, tmp_path):
    product, post = business_and_post(db, slug="community-rotate")
    settings = Settings(products_root=tmp_path)
    old = connect_community(db, settings, product.id, "123456", "first-test-token")
    pub = schedule(db, product.id, post.id, datetime.now(timezone.utc) + timedelta(minutes=25))
    new = connect_community(db, settings, product.id, "654321", "second-test-token")
    assert new.id != old.id
    assert connected(db, product.id).account_id == "654321"
    assert not VKStore(tmp_path).path(old.id).exists()
    assert pub.status == "scheduled"  # Worker must reject the old destination at claim.
