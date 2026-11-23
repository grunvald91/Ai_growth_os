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
    community_reference, resolve_community, resolve_publisher_community, verify_community_key,
    begin_community_oauth, read_community_oauth_cookie, finish_community_oauth,
    community_oauth_config, community_oauth_redirect,
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
    pub = schedule(db, product.id, post.id, when)
    assert pub.channel == "vk" and pub.status == "scheduled"


def test_vk_publisher_key_rotation_invalidates_previous_scheduled_post(db, tmp_path):
    product, post = business_and_post(db, slug="community-rotate")
    settings = Settings(products_root=tmp_path)
    old = connect_community(db, settings, product.id, "123456", "first-test-token")
    new = connect_community(db, settings, product.id, "654321", "second-test-token")
    assert new.id != old.id
    assert connected(db, product.id).account_id == "654321"
    assert not VKStore(tmp_path).path(old.id).exists()
    pub = schedule(db, product.id, post.id, datetime.now(timezone.utc) + timedelta(minutes=25))
    assert pub.status == "scheduled" and connected(db, product.id).id == new.id


@pytest.mark.parametrize(("value", "expected"), [
    ("123456", "123456"),
    ("club123456", "123456"),
    ("https://vk.com/public123456", "123456"),
    ("https://m.vk.com/club123456?from=share", "123456"),
    ("https://vk.ru/my_company", "my_company"),
    ("https://vk.ru/club241947800", "241947800"),
    ("VK.RU/club241947800", "241947800"),
    ("WWW.VK.COM/public241947800", "241947800"),
    ("HTTPS://VK.RU/club241947800", "241947800"),
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
async def test_vkr_u_numeric_group_link_does_not_require_public_api_lookup(monkeypatch):
    """Regression for the real user group: the URL is valid independent of VK token."""
    from app.comments.vk import VKAdapter
    calls = []

    async def fake(self, method, token, **params):
        calls.append((method, params))
        if method == "groups.getById":
            raise AssertionError("Numeric group must not require groups.getById")
        if method == "groups.getTokenPermissions":
            return {"mask": 8192}
        if method == "groups.getCallbackServers":
            assert params["group_id"] == "241947800"
            return {"count": 0, "items": []}
        raise AssertionError("Unexpected request to " + method)

    monkeypatch.setattr(VKAdapter, "call", fake)
    account_id, label = await resolve_publisher_community(
        "test-group-key", "https://vk.ru/club241947800")
    assert account_id == "241947800"
    assert label
    assert calls == []
    await verify_community_key("test-group-key", account_id)
    assert [method for method, _ in calls] == [
        "groups.getTokenPermissions", "groups.getCallbackServers"]


@pytest.mark.asyncio
async def test_vkr_u_short_name_still_requires_api_lookup(monkeypatch):
    async def fake(token, reference):
        assert reference == "https://vk.ru/my_company"
        return "241947800", "Studio"

    monkeypatch.setattr("app.social_vk.resolve_community", fake)
    assert await resolve_publisher_community("key", "https://vk.ru/my_company") == (
        "241947800", "Studio")


@pytest.mark.asyncio
async def test_vk_resolve_link_and_verify_key(monkeypatch):
    calls = []

    async def call(self, method, token, **params):
        assert token == "test-group-key"
        calls.append((method, params))
        if method == "groups.getById":
            return {"groups": [{"id": 123456, "name": "Моя компания"}]}
        if method == "groups.getTokenPermissions":
            return {"mask": 8192}
        if method == "groups.getCallbackServers":
            return {"count": 0, "items": []}
        raise AssertionError("Unexpected method " + method)

    monkeypatch.setattr("app.social_vk.VKAdapter.call", call)
    account_id, name = await resolve_community("test-group-key", "https://vk.com/my_company")
    assert (account_id, name) == ("123456", "Моя компания")
    await verify_community_key("test-group-key", account_id)
    assert [name for name, _ in calls] == [
        "groups.getById", "groups.getTokenPermissions", "groups.getCallbackServers"]
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
    from app.social_vk import VKKeyVerificationError

    async def call(self, method, token, **params):
        if method == "groups.getTokenPermissions":
            return {"mask": 8192}
        if method in ("groups.getCallbackServers", "groups.getCallbackConfirmationCode"):
            raise ValueError("VK denied group binding")
        raise AssertionError("Unexpected API method " + method)

    monkeypatch.setattr("app.social_vk.VKAdapter.call", call)
    with pytest.raises(VKKeyVerificationError, match="указанному сообществу") as exc:
        await verify_community_key("test-group-key", "123456")
    assert exc.value.reason == "group_unverified"


@pytest.mark.asyncio
async def test_vk_key_group_proof_falls_back_without_callback_server(monkeypatch):
    """No pre-existing callback server is needed for approved text posting."""
    from app.comments.gateway import PlatformError
    from app.comments.vk import VKAdapter

    calls = []

    async def fake(self, method, token, **params):
        calls.append((method, params))
        if method == "groups.getTokenPermissions":
            return {"mask": 8192 | 262144}
        if method == "groups.getCallbackServers":
            raise PlatformError("callback listing unsupported")
        if method == "groups.getCallbackConfirmationCode":
            assert params == {"group_id": "241947800"}
            return {"code": "discard-this-secret"}
        raise AssertionError("Unsupported method " + method)

    monkeypatch.setattr(VKAdapter, "call", fake)
    assert await verify_community_key("group-key", "241947800") is None
    assert [method for method, _ in calls] == [
        "groups.getTokenPermissions", "groups.getCallbackServers",
        "groups.getCallbackConfirmationCode"]


@pytest.mark.asyncio
async def test_vk_key_rejects_unrecognized_group_proof_response(monkeypatch):
    from app.social_vk import VKKeyVerificationError
    from app.comments.vk import VKAdapter

    async def fake(self, method, token, **params):
        if method == "groups.getTokenPermissions":
            return {"mask": 8192}
        if method == "groups.getCallbackServers":
            return {"count": 0}  # Not a valid proof: missing items.
        if method == "groups.getCallbackConfirmationCode":
            return {"code": ""}  # Missing private group-scoped confirmation.
        raise AssertionError("Unexpected method")

    monkeypatch.setattr(VKAdapter, "call", fake)
    with pytest.raises(VKKeyVerificationError, match="указанному сообществу") as exc:
        await verify_community_key("wrong-group-key", "241947800")
    assert exc.value.reason == "group_unverified"


@pytest.mark.asyncio
async def test_vk_key_permissions_api_failure_is_not_misreported_as_missing_scope(monkeypatch):
    from app.social_vk import VKKeyVerificationError
    from app.comments.gateway import PlatformError
    from app.comments.vk import VKAdapter

    async def fake(self, method, token, **params):
        assert method == "groups.getTokenPermissions"
        raise PlatformError("VK endpoint temporarily unavailable")

    monkeypatch.setattr(VKAdapter, "call", fake)
    with pytest.raises(VKKeyVerificationError) as exc:
        await verify_community_key("group-key", "241947800")
    assert exc.value.reason == "permissions_unverified"


@pytest.mark.asyncio
async def test_vk_comment_group_key_cannot_pass_read_check(monkeypatch):
    from app.comments.vk import VKAdapter
    from app.comments.gateway import CommentGateway, PlatformError

    calls = []

    async def call(self, method, token, **params):
        calls.append((method, params))
        if method == "groups.getById":
            return {"groups": [{"id": 123456, "name": "VK Business"}]}
        if method == "groups.getCallbackServers":
            return {"count": 0, "items": []}
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
    assert "groups.getCallbackServers" in [m for m, _ in calls]


@pytest.mark.asyncio
async def test_vk_comment_token_refreshes_before_sync_and_send(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from app.comments.service import CommentService

    service = object.__new__(CommentService)
    service.settings = Settings(products_root=tmp_path)
    async def fresh(settings, channel):
        assert channel.platform == "vk"
        return "refreshed-vk-user-token"

    monkeypatch.setattr("app.social_vk.active_token", fresh)
    channel = SimpleNamespace(platform="vk", id="stub-id")
    assert await service.channel_token(channel) == "refreshed-vk-user-token"


@pytest.mark.asyncio
async def test_vk_group_key_rejects_missing_wall_permission(monkeypatch):
    async def call(self, method, token, **params):
        if method == "groups.getTokenPermissions":
            return {"mask": 262144}
        raise AssertionError("No other calls allowed without wall bit")
    monkeypatch.setattr("app.social_vk.VKAdapter.call", call)
    from app.social_vk import VKKeyVerificationError
    with pytest.raises(VKKeyVerificationError, match="разрешение") as exc:
        await verify_community_key("test-key", "123456")
    assert exc.value.reason == "wall_missing"


@pytest.mark.asyncio
async def test_vk_group_key_text_post_once_and_no_duplicate(db, tmp_path):
    product, post = business_and_post(db, slug="community-wall-post")
    settings = Settings(products_root=tmp_path, content_vk_autopost_enabled=True)
    conn = connect_community(db, settings, product.id, "123456", "group-token", name="Компания")
    pub = schedule(db, product.id, post.id, datetime.now(timezone.utc) + timedelta(seconds=4))
    calls = []
    async def sender(group_id, body, guid):
        calls.append((group_id, body, guid))
        return 777
    due = datetime.now(timezone.utc) + timedelta(seconds=8)
    assert await deliver_once(lambda: sessions(db), settings, sender=sender, now=due) == 1
    db.refresh(pub)
    assert pub.status == "published"
    assert pub.external_reference == "vk:-123456_777"
    assert calls == [("123456", "Проверка интеграции ServGo", str(pub.id))]
    assert await deliver_once(lambda: sessions(db), settings, sender=sender, now=due) == 0
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_vk_split_read_and_write_tokens_for_comments(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4
    from app.comments.vk import VKAdapter
    from app.comments.gateway import CommentGateway

    group_id = "123456"
    owner = -123456
    calls = []

    async def fake(self, method, token, **params):
        calls.append((method, token, params))
        if method == "groups.getById":
            return {"groups": [{"id": 123456, "name": "Тест", "is_admin": 0}]}
        if method == "groups.getTokenPermissions":
            return {"mask": 8192 + 262144}
        if method == "groups.getCallbackServers":
            return {"count": 0, "items": []}
        if method == "wall.get":
            return {"items": [{"id": 15, "owner_id": owner, "text": "Пост"}], "count": 1}
        if method == "wall.getComments":
            # The VK API schema makes comment.post_id optional. Read is scoped
            # to owner=-123456, post_id=15 in the request, not in each item.
            return {"items": [{"id": 91, "text": "Цена?",
                               "from_id": 77, "thread": {"count": 0}}], "count": 1}
        if method == "wall.createComment":
            return {"comment_id": 92}
        raise AssertionError("Unexpected VK method: " + method)

    monkeypatch.setattr(VKAdapter, "call", fake)
    adapter = VKAdapter(CommentGateway(vk_read_token="service-read-token"), "vk")
    assert await adapter.check(group_id, "community-write-token") == "Тест"
    comment = SimpleNamespace(id=uuid4(), external_id="-123456_15_91",
                              target={"owner_id": owner, "post_id": 15, "comment_id": 91})
    channel = SimpleNamespace(platform="vk", account_id=group_id)
    assert await adapter.send(channel, "community-write-token", comment, "Ответ") == "92"
    reading = {"wall.get", "wall.getComments"}
    writing = {"wall.createComment"}
    assert all(token == "service-read-token" for method, token, _ in calls if method in reading)
    assert all(token == "community-write-token" for method, token, _ in calls if method in writing)
    assert all(method not in {"wall.getById", "wall.getComment"} for method, _, _ in calls)
    post_check = [params for method, _, params in calls if method == "wall.getComments"][-1]
    assert post_check["owner_id"] == owner
    assert post_check["post_id"] == 15
    assert post_check["start_comment_id"] == 91


@pytest.mark.asyncio
async def test_vk_service_token_used_only_for_read(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from app.comments.service import CommentService
    service = object.__new__(CommentService)
    service.settings = Settings(products_root=tmp_path, social_vk_service_token="read-only-key")
    async def fake_active(settings, channel):
        assert channel.platform == "vk"
        return "community-write-key"
    monkeypatch.setattr("app.social_vk.active_token", fake_active)
    channel = SimpleNamespace(platform="vk", id="test-channel")
    assert await service.channel_token(channel, for_read=True) == "read-only-key"
    assert await service.channel_token(channel) == "community-write-key"


@pytest.mark.asyncio
async def test_vk_reply_rejects_conflicting_post_id_with_service_read_token(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4
    from app.comments.vk import VKAdapter
    from app.comments.gateway import CommentGateway, PlatformError

    methods = []

    async def call(self, method, token, **params):
        methods.append(method)
        if method == "groups.getById":
            return {"groups": [{"id": 123456, "name": "Команда"}]}
        if method == "groups.getTokenPermissions":
            return {"mask": 8192}
        if method == "groups.getCallbackServers":
            return {"count": 0, "items": []}
        if method == "wall.getComments":
            return {"items": [{"id": 91, "post_id": 999, "text": "Чужой комментарий",
                               "from_id": 77}], "count": 1}
        if method == "wall.createComment":
            raise AssertionError("Writing after a conflicting post ID is forbidden")
        raise AssertionError("Unexpected VK call " + method)

    monkeypatch.setattr(VKAdapter, "call", call)
    adapter = VKAdapter(CommentGateway(vk_read_token="read-only-service-key"), "vk")
    channel = SimpleNamespace(account_id="123456", platform="vk")
    comment = SimpleNamespace(id=uuid4(), external_id="-123456_15_91",
                              target={"owner_id": -123456, "post_id": 15, "comment_id": 91})
    with pytest.raises(PlatformError, match="удалён или недоступен"):
        await adapter.send(channel, "community-key", comment, "Ответ")
    assert "wall.createComment" not in methods


def test_vk_text_autopost_default_enabled_with_emergency_disable(tmp_path):
    settings = Settings(products_root=tmp_path)
    assert settings.content_vk_autopost_enabled is True
    disabled = Settings(products_root=tmp_path, content_vk_autopost_enabled=False)
    assert disabled.content_vk_autopost_enabled is False


@pytest.mark.asyncio
async def test_vk_worker_refuses_stale_approved_publications_on_reenable(db, tmp_path):
    product, post = business_and_post(db, slug="stale-vk-when-worker-starts")
    attach_vk(db, product.id)
    pub = schedule(db, product.id, post.id, datetime.now(timezone.utc) + timedelta(seconds=5))
    settings = Settings(products_root=tmp_path, content_vk_max_lateness_minutes=60)
    sent = []

    async def send(group, text, guid):
        sent.append((group, text, guid))
        return 777

    far_later = datetime.now(timezone.utc) + timedelta(hours=3)
    assert await deliver_once(lambda: sessions(db), settings, sender=send, now=far_later) == 1
    db.refresh(pub)
    assert pub.status == "failed_before_send"
    assert sent == []
    assert await deliver_once(lambda: sessions(db), settings, sender=send, now=far_later) == 0


def test_vk_platform_service_token_is_encrypted_and_can_be_removed(tmp_path):
    from app.vk_service import (platform_service_token, save_platform_service_token,
                                delete_platform_service_token, _SERVICE_KEY_ID)
    settings = Settings(products_root=tmp_path, social_vk_service_token=None, _env_file=None)
    secret = "vk-platform-readonly-token-test"
    assert platform_service_token(settings) is None
    save_platform_service_token(settings, secret)
    assert platform_service_token(settings) == secret
    from app.comments.secrets import CommentSecrets
    store = CommentSecrets(tmp_path)
    assert secret.encode() not in store.path(_SERVICE_KEY_ID).read_bytes()
    env_managed = Settings(products_root=tmp_path, social_vk_service_token="env-key", _env_file=None)
    assert platform_service_token(env_managed) == "env-key"
    with pytest.raises(ValueError, match="окружением"):
        save_platform_service_token(env_managed, "replacement")
    with pytest.raises(ValueError, match="окружении"):
        delete_platform_service_token(env_managed)
    delete_platform_service_token(settings)
    assert platform_service_token(settings) is None


@pytest.mark.asyncio
async def test_vk_platform_service_key_is_verified_with_read_only_calls(monkeypatch):
    from app.vk_service import verify_platform_service_token
    from app.comments.vk import VKAdapter

    calls = []

    async def fake(self, method, token, **params):
        assert token == "app-service-token"
        calls.append((method, params))
        if method == "groups.getById":
            return {"groups": [{"id": 123456, "name": "Проверка ServGo"}]}
        if method == "wall.get":
            return {"items": [{"id": 49, "owner_id": -123456}]}
        if method == "wall.getComments":
            return {"items": [], "count": 0}
        raise AssertionError("Unexpected VK call: " + method)

    monkeypatch.setattr(VKAdapter, "call", fake)
    result = await verify_platform_service_token("app-service-token", "https://vk.com/my_community")
    assert result == ("123456", "Проверка ServGo", True)
    assert [name for name, _ in calls] == ["groups.getById", "wall.get", "wall.getComments"]
    assert calls[-1][1]["owner_id"] == -123456
    assert calls[-1][1]["post_id"] == 49


@pytest.mark.asyncio
async def test_vk_platform_service_key_empty_wall_not_marked_comments_verified(monkeypatch):
    from app.vk_service import verify_platform_service_token
    from app.comments.vk import VKAdapter
    from app.comments.gateway import CommentGateway, PlatformError

    async def fake(self, method, token, **params):
        if method == "groups.getById":
            return {"groups": [{"id": 123456, "name": "Пустая группа"}]}
        if method == "wall.get":
            return {"items": [], "count": 0}
        raise AssertionError("Comments must not be probed without a post")
    monkeypatch.setattr(VKAdapter, "call", fake)
    assert await verify_platform_service_token("token", "vk.com/club123456") == (
        "123456", "Пустая группа", False)
    adapter = VKAdapter(CommentGateway(vk_read_token="token"), "vk")
    # A manager is still verified using the original group key.
    async def manager(self, account_id, token):
        return {"name": "Пустая группа"}
    monkeypatch.setattr(VKAdapter, "manager", manager)
    with pytest.raises(PlatformError, match="нет открытых записей"):
        await adapter.check("123456", "group-token")


def test_vk_one_click_group_oauth_requires_legacy_app_and_never_exposes_secret(tmp_path):
    from urllib.parse import parse_qs, urlsplit
    settings = Settings(products_root=tmp_path, social_vk_community_app_id="1234567",
                        social_vk_community_app_secret="very-secret-app-key", _env_file=None)
    assert community_oauth_config(settings)[0] == "1234567"
    assert "very-secret-app-key" not in repr(settings)
    assert community_oauth_redirect(settings) == "https://servgo.ru/studio/social/vk/group-callback"
    with pytest.raises(ValueError, match="не настроена"):
        community_oauth_config(Settings(products_root=tmp_path,
                                        social_vk_client_id="1234567",
                                        social_vk_community_app_id=None,
                                        _env_file=None))
    assert "very-secret-app-key" not in community_oauth_redirect(settings)


@pytest.mark.asyncio
async def test_vk_one_click_numeric_community_oauth_is_state_and_tenant_bound(tmp_path):
    from urllib.parse import parse_qs, urlsplit
    settings = Settings(products_root=tmp_path, social_vk_community_app_id="1234567",
                        social_vk_community_app_secret="very-secret-app-key", _env_file=None)
    product_id = "3ecb2135-1077-428e-9a65-ea1d895a8432"
    url, cookie = await begin_community_oauth(
        settings, product_id, "https://vk.com/club123456", owner_login="studio-owner")
    assert url.startswith("https://oauth.vk.com/authorize?")
    query = parse_qs(urlsplit(url).query)
    assert query["client_id"] == ["1234567"]
    assert query["group_ids"] == ["123456"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == [str(262144 | 8192)]
    assert query["redirect_uri"] == [community_oauth_redirect(settings)]
    assert "very-secret-app-key" not in url
    assert product_id not in cookie
    state = query["state"][0]
    decoded = read_community_oauth_cookie(settings, cookie, state)
    assert decoded["owner_login"] == "studio-owner"
    assert decoded["product_id"] == product_id
    assert decoded["group_id"] == "123456"
    with pytest.raises(ValueError):
        read_community_oauth_cookie(settings, cookie, "wrong-state")
    with pytest.raises(ValueError):
        read_community_oauth_cookie(settings, cookie + "broken", state)


@pytest.mark.asyncio
async def test_vk_one_click_short_group_resolves_with_platform_service_key(monkeypatch, tmp_path):
    from urllib.parse import parse_qs, urlsplit
    settings = Settings(products_root=tmp_path, social_vk_community_app_id="1234567",
                        social_vk_community_app_secret="very-secret-app-key", _env_file=None)
    calls = []
    async def legacy(settings, *, code=None, service=False):
        assert service is True
        return {"access_token": "safe-app-read-key"}
    async def resolve(token, reference):
        calls.append((token, reference))
        return "123456", "My Studio"
    monkeypatch.setattr("app.social_vk._legacy_oauth_token", legacy)
    monkeypatch.setattr("app.social_vk.resolve_community", resolve)
    url, _ = await begin_community_oauth(settings,
        "3ecb2135-1077-428e-9a65-ea1d895a8432", "https://vk.com/my_studio")
    assert parse_qs(urlsplit(url).query)["group_ids"] == ["123456"]
    assert calls == [("safe-app-read-key", "https://vk.com/my_studio")]


@pytest.mark.asyncio
async def test_vk_one_click_exchange_verifies_exact_group_and_encrypts_key(monkeypatch, db, tmp_path):
    settings = Settings(products_root=tmp_path, social_vk_community_app_id="1234567",
                        social_vk_community_app_secret="very-secret-app-key", _env_file=None)
    company = Product(name="My Studio", slug="one-click-vk-123")
    db.add(company)
    db.commit()
    captured = []
    async def exchange(settings, *, code=None, service=False):
        assert code == "confirmed-code-12345" and not service
        return {"access_token_123456": "new-group-secret-key", "expires_in": 3600}
    async def resolve(token, reference):
        captured.append(("resolve", token, reference))
        return "123456", "Our VK"
    async def verify(token, group):
        captured.append(("verify", token, group))
    monkeypatch.setattr("app.social_vk._legacy_oauth_token", exchange)
    monkeypatch.setattr("app.social_vk.resolve_community", resolve)
    monkeypatch.setattr("app.social_vk.verify_community_key", verify)
    payload = {"group_id": "123456", "product_id": str(company.id)}
    row = await finish_community_oauth(db, settings, payload, "confirmed-code-12345")
    ch = connected(db, company.id)
    from app.vk_service import group_app_verified
    assert not group_app_verified(settings)  # Consent alone does not prove wall.post.
    assert ch.id == row.id and ch.is_community_token
    assert ch.auth_type == "community_oauth"
    assert ch.expires_at is not None
    assert VKStore(tmp_path).load_token(row.id) == "new-group-secret-key"
    assert b"new-group-secret-key" not in VKStore(tmp_path).path(row.id).read_bytes()
    assert captured == [
        ("resolve", "new-group-secret-key", "123456"),
        ("verify", "new-group-secret-key", "123456"),
    ]
    post = Artifact(product_id=company.id, kind="channel_text",
                    title="Approved VK post", status="approved",
                    content=json.dumps({"channel": "vk", "body": "safe"}))
    db.add(post)
    db.commit()
    with pytest.raises(ValueError, match="истечёт"):
        schedule(db, company.id, post.id, datetime.now(timezone.utc) + timedelta(days=2))


@pytest.mark.asyncio
async def test_vk_one_click_rejects_wrong_group_token_without_mutating_company(monkeypatch, db, tmp_path):
    settings = Settings(products_root=tmp_path, social_vk_community_app_id="1234567",
                        social_vk_community_app_secret="very-secret-app-key", _env_file=None)
    company = Product(name="My Studio", slug="one-click-rejected-vk")
    db.add(company); db.commit()
    async def exchange(settings, *, code=None, service=False):
        return {"access_token_777777": "unrelated-community-token"}
    monkeypatch.setattr("app.social_vk._legacy_oauth_token", exchange)
    with pytest.raises(ValueError, match="не выдал ключ"):
        await finish_community_oauth(db, settings,
            {"group_id": "123456", "product_id": str(company.id)}, "confirmed-code-12345")
    assert connected(db, company.id) is None


def test_vk_group_pilot_gate_before_first_real_success(tmp_path):
    from types import SimpleNamespace
    from app.api.vk_publishing import _can_begin_community_oauth
    from app.vk_service import mark_group_app_verified
    settings = Settings(products_root=tmp_path, app_env="production",
                        studio_tune_usernames="owner",
                        social_vk_community_app_id="1234567",
                        social_vk_community_app_secret="secret-only-studio", _env_file=None)
    pilot = SimpleNamespace(state=SimpleNamespace(studio_login="owner", studio_product_ids=None))
    customer = SimpleNamespace(state=SimpleNamespace(studio_login="company",
                                                    studio_product_ids=["12345678-1234-1234-1234-123456789012"]))
    assert _can_begin_community_oauth(settings, pilot)
    assert not _can_begin_community_oauth(settings, customer)
    mark_group_app_verified(settings)
    assert _can_begin_community_oauth(settings, customer)


@pytest.mark.asyncio
async def test_vk_one_click_only_becomes_public_after_real_confirmed_post(monkeypatch, db, tmp_path):
    from app.comments.vk import VKAdapter
    from app.vk_service import group_app_verified, group_app_credentials
    product, post = business_and_post(db, slug="verified-vk-publishing")
    settings = Settings(products_root=tmp_path, social_vk_community_app_id="1234567",
                        social_vk_community_app_secret="secret-only-studio", _env_file=None)
    connect_community(db, settings, product.id, "123456", "community-oauth-test-token",
                      auth_type="community_oauth")
    assert not group_app_verified(settings)
    pub = schedule(db, product.id, post.id, datetime.now(timezone.utc) + timedelta(seconds=5))
    async def api_call(self, method, token, **params):
        assert method == "wall.post" and token == "community-oauth-test-token"
        assert params["owner_id"] == -123456 and params["message"] == "Проверка интеграции ServGo"
        return {"post_id": 888}
    monkeypatch.setattr(VKAdapter, "call", api_call)
    assert await deliver_once(lambda: sessions(db), settings, sender=None,
                              now=datetime.now(timezone.utc) + timedelta(seconds=6)) == 1
    db.refresh(pub)
    assert pub.status == "published"
    assert group_app_verified(settings)
    # Rotating the app credentials removes the confirmed global-access state.
    other = Settings(products_root=tmp_path, social_vk_community_app_id="1234567",
                     social_vk_community_app_secret="different-studio-secret", _env_file=None)
    assert not group_app_verified(other)


@pytest.mark.asyncio
async def test_vk_one_click_fake_sender_does_not_verify_app(monkeypatch, db, tmp_path):
    from app.vk_service import group_app_verified
    product, post = business_and_post(db, slug="not-verified-injected-vk")
    settings = Settings(products_root=tmp_path, social_vk_community_app_id="1234567",
                        social_vk_community_app_secret="secret-only-studio", _env_file=None)
    connect_community(db, settings, product.id, "123456", "community-oauth-test-token",
                      auth_type="community_oauth")
    pub = schedule(db, product.id, post.id, datetime.now(timezone.utc) + timedelta(seconds=5))
    async def sender(group, text, guid):
        return 888
    assert await deliver_once(lambda: sessions(db), settings, sender=sender,
                              now=datetime.now(timezone.utc) + timedelta(seconds=6)) == 1
    db.refresh(pub)
    assert pub.status == "published"
    assert not group_app_verified(settings)


def test_vk_connection_guides_match_2fa_and_current_menu():
    from app.comments.platforms import PLATFORMS, CONNECTION_GUIDES

    # Users reach VK from both publications and the comments workspace.
    for guide in (PLATFORMS["vk"], CONNECTION_GUIDES["vk"]):
        rendered = " ".join(guide["steps"]) + " " + guide.get("help", "")
        assert "Дополнительно" in rendered
        assert "Работа с API" in rendered
        assert "Ключи доступа" in rendered
        assert "2FA" in rendered


def test_vk_pilot_gate_uses_exact_operator_access_rules(tmp_path):
    from types import SimpleNamespace
    from app.api.vk_publishing import _can_begin_community_oauth, _is_tune_owner

    settings = Settings(products_root=tmp_path, app_env="production",
                        studio_tune_usernames="owner",
                        social_vk_community_app_id="1234567",
                        social_vk_community_app_secret="secret-only-studio", _env_file=None)
    permitted = SimpleNamespace(state=SimpleNamespace(studio_login="owner", studio_product_ids=None))
    wrong_case = SimpleNamespace(state=SimpleNamespace(studio_login="Owner", studio_product_ids=None))
    tenant_scoped = SimpleNamespace(state=SimpleNamespace(studio_login="owner", studio_product_ids=["example"]))
    assert _can_begin_community_oauth(settings, permitted) == _is_tune_owner(settings, permitted)
    assert not _can_begin_community_oauth(settings, wrong_case)
    assert not _can_begin_community_oauth(settings, tenant_scoped)


def test_vk_page_shows_contextual_settings_link_only_to_operator(db, tmp_path):
    from types import SimpleNamespace
    from app.api.vk_publishing import vk_page

    product, _ = business_and_post(db, slug="vk-settings-link")
    settings = Settings(
        products_root=tmp_path, app_env="production",
        studio_tune_usernames="owner", social_vk_community_app_id=None,
        social_vk_community_app_secret=None, social_vk_service_token=None,
        _env_file=None,
    )
    owner = SimpleNamespace(state=SimpleNamespace(
        studio_login="owner", studio_product_ids=None))
    customer = SimpleNamespace(state=SimpleNamespace(
        studio_login="customer", studio_product_ids=[str(product.id)]))

    page_response = vk_page(product.id, owner, db=db, settings=settings)
    # Browsers must be able to submit these same-origin POST forms.
    # A no-referrer policy previously yielded Origin: null and CSRF 403.
    assert page_response.headers["referrer-policy"] == "same-origin"
    assert page_response.headers["cache-control"] == "private, no-store"
    owner_html = page_response.body.decode()
    assert 'Авторизация одним нажатием пока не настроена.' in owner_html
    assert '<a href="/studio/tune/vk"' in owner_html
    assert 'href="https://vk.com/groups"' not in owner_html
    assert 'id="sg-vk-group-link"' in owner_html
    assert "vk.ru/club241947800" not in owner_html
    assert "vk.ru/club123456" in owner_html
    assert 'id="sg-vk-community-link"' in owner_html
    assert 'id="sg-vk-token-link"' in owner_html
    assert 'encodeURIComponent(name) + "?act=tokens"' in owner_html
    assert 'id="sg-vk-token-link" target="_blank" rel="noopener noreferrer" hidden' in owner_html
    assert 'id="sg-vk-token-copy" type="button"' in owner_html
    assert 'id="sg-vk-token-help" role="status" aria-live="polite"' in owner_html
    assert "двухфакторную аутентификацию (2FA)" in owner_html
    assert "«Дополнительно»" in owner_html
    assert "«Работа с API»" in owner_html
    assert "«Создать ключ»" in owner_html
    assert 'link.removeAttribute("href")' in owner_html
    assert 'id="sg-vk-oauth-community-link"' not in owner_html
    assert 'https://vk.ru/' in owner_html
    assert 'target="_blank" rel="noopener noreferrer"' in owner_html
    assert "Настроить подключение VK" in owner_html

    customer_response = vk_page(product.id, customer, db=db, settings=settings)
    assert customer_response.headers["referrer-policy"] == "same-origin"
    customer_html = customer_response.body.decode()
    assert "/studio/tune/vk" not in customer_html
    assert "Используйте ключ сообщества ниже" in customer_html




def test_vk_page_same_origin_form_passes_csrf_without_relaxing_security(db, tmp_path):
    """Regression for a browser posting back to the exact VK page.

    The VK GET must send a same-origin policy; CSRF must still reject a null
    or cross-site Origin even if the page carries the corrected policy.
    """
    from types import SimpleNamespace
    from starlette.requests import Request
    from app.api.content_studio_auth import StudioOriginError, require_studio_csrf
    from app.api.vk_publishing import vk_page

    product, _ = business_and_post(db, slug="vk-csrf-page")
    cfg = Settings(
        products_root=tmp_path, app_env="production",
        studio_public_origin="https://growth.servgo.ru",
        studio_servgo_gateway_enabled=True, _env_file=None,
    )
    browser = SimpleNamespace(state=SimpleNamespace(
        studio_login="customer", studio_product_ids=[str(product.id)]))
    response = vk_page(product.id, browser, db=db, settings=cfg)
    assert response.headers["Referrer-Policy"] == "same-origin"

    def post_request(*, origin=None, referer=None, fetch_site="same-origin"):
        headers = [(b"host", b"servgo.ru"),
                   (b"x-forwarded-proto", b"https"),
                   (b"sec-fetch-site", fetch_site.encode("ascii"))]
        if origin is not None:
            headers.append((b"origin", origin.encode("ascii")))
        if referer is not None:
            headers.append((b"referer", referer.encode("ascii")))
        return Request({
            "type": "http", "method": "POST", "scheme": "http",
            "server": ("servgo.ru", 443),
            "path": f"/studio/products/{product.id}/vk/community-key",
            "headers": headers, "query_string": b"",
        })

    assert require_studio_csrf(
        post_request(referer=f"https://servgo.ru/studio/products/{product.id}/vk"),
        cfg,
    ) is None
    assert require_studio_csrf(post_request(origin="https://servgo.ru"), cfg) is None
    for rejected in (
        post_request(origin="null"),
        post_request(origin="https://evil.example", fetch_site="cross-site"),
        post_request(),
    ):
        with pytest.raises(StudioOriginError) as error:
            require_studio_csrf(rejected, cfg)
        assert error.value.status_code == 403


def test_vk_page_shows_settings_near_pilot_and_missing_comments(db, tmp_path):
    from types import SimpleNamespace
    from app.api.vk_publishing import vk_page

    product, _ = business_and_post(db, slug="vk-pilot-settings-link")
    settings = Settings(
        products_root=tmp_path, app_env="production",
        studio_tune_usernames="owner", social_vk_community_app_id="1234567",
        social_vk_community_app_secret="platform-owner-secret",
        social_vk_service_token=None, _env_file=None,
    )
    owner = SimpleNamespace(state=SimpleNamespace(
        studio_login="owner", studio_product_ids=None))
    customer = SimpleNamespace(state=SimpleNamespace(
        studio_login="customer", studio_product_ids=[str(product.id)]))

    pilot_html = vk_page(product.id, owner, db=db, settings=settings).body.decode()
    assert "Проверить вход ВКонтакте" in pilot_html
    assert 'href="https://vk.com/groups"' not in pilot_html
    assert 'id="sg-vk-oauth-group-link"' in pilot_html
    assert 'id="sg-vk-oauth-community-link"' in pilot_html
    assert 'совместимость с вашим VK-приложением ещё не подтверждена.' in pilot_html
    assert '<a href="/studio/tune/vk"' in pilot_html
    assert "/studio/tune/vk" not in vk_page(
        product.id, customer, db=db, settings=settings).body.decode()

    connect_community(db, settings, product.id, "123456", "group-key",
                      name="Our test VK community")
    owner_connected = vk_page(product.id, owner, db=db, settings=settings).body.decode()
    assert "Комментарии: не подключены." in owner_connected
    assert owner_connected.count('<a href="/studio/tune/vk"') >= 2
    customer_connected = vk_page(product.id, customer, db=db, settings=settings).body.decode()
    assert "/studio/tune/vk" not in customer_connected


def test_vk_page_explains_separate_photo_access_and_keeps_user_key_private(db, tmp_path):
    from types import SimpleNamespace
    from app.api.vk_publishing import vk_page
    from app.social_vk import connect_community

    product, _ = business_and_post(db, slug="vk-photo-form")
    settings = Settings(_env_file=None, products_root=tmp_path)
    connect_community(db, settings, product.id, "123456", "community-token")
    request = SimpleNamespace(state=SimpleNamespace(
        studio_login="owner", studio_product_ids=None))
    markup = vk_page(product.id, request, db=db, settings=settings).body.decode()
    assert f'/studio/products/{product.id}/vk/photo-key' in markup
    assert 'name="access_token" type="password"' in markup
    assert "настроить доступ к загрузке" in markup
    assert "Ключ сообщества для публикации текста остаётся" in markup
    assert "community-token" not in markup
    assert "vk1.a." not in markup
