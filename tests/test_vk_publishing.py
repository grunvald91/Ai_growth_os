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
    community_reference, resolve_community, verify_community_key,
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

    vanity_url, vanity_cookie = begin_oauth(settings, "3ecb2135-1077-428e-9a65-ea1d895a8432",
                                          "https://vk.com/my_studio")
    vanity_state = parse_qs(urlsplit(vanity_url).query)["state"][0]
    assert read_oauth_cookie(settings, vanity_cookie, vanity_state)["account_id"] == "my_studio"


def test_vk_oauth_rejects_bad_group_or_missing_app(tmp_path):
    settings = Settings(products_root=tmp_path, social_vk_client_id="12345678")
    for value in ("-1234", "id12", "0", "not-a-url", "a!"):
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
    with pytest.raises(ValueError, match="wall.post"):
        schedule(db, product.id, post.id, when)


def test_vk_publisher_key_rotation_invalidates_previous_scheduled_post(db, tmp_path):
    product, post = business_and_post(db, slug="community-rotate")
    settings = Settings(products_root=tmp_path)
    old = connect_community(db, settings, product.id, "123456", "first-test-token")
    new = connect_community(db, settings, product.id, "654321", "second-test-token")
    assert new.id != old.id
    assert connected(db, product.id).account_id == "654321"
    assert not VKStore(tmp_path).path(old.id).exists()
    with pytest.raises(ValueError, match="wall.post"):
        schedule(db, product.id, post.id, datetime.now(timezone.utc) + timedelta(minutes=25))


@pytest.mark.parametrize(("value", "expected"), [
    ("123456", "123456"),
    ("club123456", "123456"),
    ("https://vk.com/public123456", "123456"),
    ("https://m.vk.com/club123456?from=share", "123456"),
    ("https://vk.ru/my_company", "my_company"),
    ("vk.com/my_company", "my_company"),
])
def test_vk_community_reference_accepts_links(value, expected):
    assert community_reference(value) == expected


@pytest.mark.parametrize("value", [
    "https://evil.example/club123456", "http://vk.com/club123456",
    "https://vk.com@evil.example/club123456", "https://vk.com/wall-1_2",
    "https://vk.com/id123456", "https://vk.com/club123456/anything",
    "javascript:alert(1)", "", "vk.com/club0",
])
def test_vk_community_reference_rejects_invalid(value):
    with pytest.raises(ValueError):
        community_reference(value)


@pytest.mark.asyncio
async def test_vk_resolve_link_and_verify_key(monkeypatch):
    calls = []

    async def call(self, method, token, **params):
        assert token == "test-group-key"
        calls.append((method, params))
        if method == "groups.getById":
            return {"groups": [{"id": 123456, "name": "Моя компания"}]}
        return {"mask": 8192}

    monkeypatch.setattr("app.social_vk.VKAdapter.call", call)
    account_id, name = await resolve_community("test-group-key", "https://vk.com/my_company")
    assert (account_id, name) == ("123456", "Моя компания")
    await verify_community_key("test-group-key", account_id)
    assert [name for name, _ in calls] == [
        "groups.getById", "groups.getTokenPermissions", "groups.getCallbackSettings"]
    assert calls[-1][1] == {"group_id": "123456"}


@pytest.mark.asyncio
async def test_vk_resolve_rejects_different_numeric_id(monkeypatch):
    async def call(self, method, token, **params):
        return {"groups": [{"id": 654321, "name": "Чужое сообщество"}]}
    monkeypatch.setattr("app.social_vk.VKAdapter.call", call)
    with pytest.raises(ValueError, match="другое сообщество"):
        await resolve_community("test-group-key", "https://vk.com/club123456")


@pytest.mark.asyncio
async def test_vk_key_verification_rejects_missing_group_access(monkeypatch):
    async def call(self, method, token, **params):
        if method == "groups.getCallbackSettings":
            raise ValueError("VK rejected")
        return {}
    monkeypatch.setattr("app.social_vk.VKAdapter.call", call)
    with pytest.raises(ValueError, match="Ключ не даёт доступ"):
        await verify_community_key("test-group-key", "123456")


@pytest.mark.asyncio
async def test_vk_comment_group_key_cannot_pass_read_check(monkeypatch):
    from app.comments.vk import VKAdapter
    from app.comments.gateway import CommentGateway, PlatformError

    calls = []

    async def call(self, method, token, **params):
        calls.append((method, params))
        if method == "groups.getById":
            return {"groups": [{"id": 123456, "name": "VK Business"}]}
        if method == "wall.get":
            raise PlatformError("wall.get requires a user or service token")
        return {"mask": 8192}

    monkeypatch.setattr(VKAdapter, "call", call)
    adapter = VKAdapter(CommentGateway(), "vk")
    with pytest.raises(PlatformError, match="wall.get"):
        await adapter.check("123456", "test-group-key")
    wall = next(p for m, p in calls if m == "wall.get")
    assert wall["owner_id"] == -123456
    assert "domain" not in wall
    assert "groups.getTokenPermissions" in [m for m, _ in calls]
    assert "groups.getCallbackSettings" in [m for m, _ in calls]
