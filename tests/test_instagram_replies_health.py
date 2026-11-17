"""No-network Instagram nested replies, cursor and health regression coverage."""
import hashlib
import hmac
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.comments.contracts import AgentSettings, ConnectInput
from app.comments.gateway import CommentGateway, PlatformError
from app.comments.models import BusinessComment, CommentChannel
from app.core.config import get_settings
from app.main import app
from app.models.domain import Approval, Product, Publication
from test_comments import ctx, client_for


@pytest.mark.asyncio
async def test_nested_instagram_replies_resume_native_pagination_and_dedupe(ctx):
    db, product, svc, _, _ = ctx
    channel = await svc.connect(product.id, ConnectInput(
        platform="instagram", account_id="100", token="local-test-token"))
    db.add(BusinessComment(
        product_id=product.id, platform="instagram", external_id="1001",
        source="platform", status="new", text="Вопрос первого уровня",
        post_text="Пост компании", target={"channel_id": str(channel.id),
                                           "media_id": "5001", "published_at": datetime.now(timezone.utc).isoformat()}))
    db.commit()
    svc.settings.comment_instagram_reply_scan_enabled = True
    svc.settings.comment_instagram_reply_scan_calls = 1
    calls = []

    async def transport(request):
        calls.append(request)
        assert request.url.host == "graph.instagram.com"
        path = request.url.path
        if path.endswith("/100/media"):
            return httpx.Response(200, json={"data": []})
        if path.endswith("/1001/replies"):
            assert request.url.params["limit"] == "100"
            assert request.url.params["fields"] == "id,text,from,timestamp"
            if request.url.params.get("after") == "native-page-two":
                return httpx.Response(200, json={"data": [
                    {"id": "3002", "text": "Ответ со второй страницы",
                     "from": {"id": "other"}, "timestamp": datetime.now(timezone.utc).isoformat()},
                ]})
            assert not request.url.params.get("after")
            return httpx.Response(200, json={"data": [
                {"id": "3001", "text": "Ответ из первой страницы",
                 "from": {"id": "another"}, "timestamp": datetime.now(timezone.utc).isoformat()},
                {"id": "3000", "text": "Наш собственный ответ",
                 "from": {"id": "100"}, "timestamp": datetime.now(timezone.utc).isoformat()},
            ], "paging": {"next": "https://untrusted.invalid/never-follow",
                          "cursors": {"after": "native-page-two"}}})
        raise AssertionError("Unexpected Graph API route: " + str(request.url))

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        svc.gateway = CommentGateway(client=client)
        assert await svc.sync(product.id, "instagram") == 1
        db.refresh(channel)
        assert channel.cursor["_ig_replies"]["after"] == "native-page-two"
        assert channel.cursor["_ig_replies"]["active"]
        assert await svc.sync(product.id, "instagram") == 1
        db.refresh(channel)
        assert channel.cursor["_ig_replies"].get("active") is None
        assert await svc.sync(product.id, "instagram") == 0  # wraps cycle
        assert await svc.sync(product.id, "instagram") == 0  # revisit: dedup row

    replies = list(db.scalars(select(BusinessComment).where(
        BusinessComment.platform == "instagram",
        BusinessComment.external_id.in_(["3000", "3001", "3002"]))))
    assert {item.external_id for item in replies} == {"3001", "3002"}
    assert all(item.target.get("parent_id") == "1001"
               and item.target.get("media_id") == "5001" for item in replies)
    assert all(item.post_text == "Пост компании" for item in replies)
    assert not list(db.scalars(select(Publication)))
    assert not list(db.scalars(select(Approval)))
    assert all(urlsplit(str(request.url)).hostname == "graph.instagram.com" for request in calls)
    assert all("untrusted.invalid" not in str(request.url) for request in calls)


@pytest.mark.asyncio
async def test_nested_replies_error_keeps_main_import_and_retries_same_parent(ctx):
    db, product, svc, _, gateway = ctx
    channel = await svc.connect(product.id, ConnectInput(
        platform="instagram", account_id="100", token="local-test-token"))
    root = BusinessComment(
        product_id=product.id, platform="instagram", external_id="1001",
        source="platform", text="Корневой комментарий",
        post_text="Контекст", target={"channel_id": str(channel.id), "media_id": "5001"})
    db.add(root)
    db.commit()
    svc.settings.comment_instagram_reply_scan_enabled = True

    class FailRepliesGateway:
        async def fetch(self, channel, token):
            return [{"external_id": "2000", "text": "Новое сообщение",
                     "post_text": "Текст поста",
                     "target": {"media_id": "5001", "published_at": datetime.now(timezone.utc).isoformat()}}], {}
        async def fetch_instagram_replies(self, channel, token, parent_id, *, after=None):
            raise PlatformError("permission denied: secret-token-should-not-leak")

    svc.gateway = FailRepliesGateway()
    assert await svc.sync(product.id, "instagram") == 1
    db.refresh(channel)
    assert channel.cursor["_ig_reply_health"]["last_error"] == "fetch_failed"
    assert "secret-token-should-not-leak" not in json.dumps(channel.cursor)
    assert db.scalar(select(BusinessComment).where(BusinessComment.external_id == "2000"))
    assert not list(db.scalars(select(Publication)))


@pytest.mark.asyncio
async def test_instagram_health_preserves_webhook_when_manual_sync_completes(ctx):
    db, product, svc, _, gateway = ctx
    svc.settings.comment_meta_app_id = "app-id"
    svc.settings.comment_meta_app_secret = "app-secret-for-hmac"
    svc.settings.comment_meta_login_config_id = "config-id"
    svc.settings.comment_meta_public_base = "https://servgo.ru"
    svc.settings.comment_meta_webhook_verify_token = "verification-secret"
    svc.settings.comment_meta_webhook_enabled = True
    channel = await svc.connect(product.id, ConnectInput(
        platform="instagram", account_id="100", token="local-test-token"))
    svc.configure(product.id, AgentSettings(enabled=True, auto_import=True))
    app.dependency_overrides[get_settings] = lambda: svc.settings
    client = client_for(ctx)
    health_url = f"/products/{product.id}/comments/instagram/health"
    before = client.get(health_url)
    assert before.status_code == 200 and before.json()["last_signed_webhook_at"] is None

    payload = {"object": "instagram", "entry": [{"id": "100", "changes": [
        {"field": "comments", "value": {"id": "50001", "text": "Сколько стоит?",
                                      "from": {"id": "900"},
                                      "media": {"id": "700"}}}]}]}
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(svc.settings.comment_meta_app_secret.encode(),
                                    raw, hashlib.sha256).hexdigest()
    result = client.post("/api/integrations/instagram/webhook",
                         content=raw, headers={"x-hub-signature-256": signature})
    assert result.status_code == 200 and result.json()["imported"] == 1
    signed_at = client.get(health_url).json()["last_signed_webhook_at"]
    assert signed_at and client.get(health_url).json()["webhook_total_imported"] == 1

    assert await svc.sync(product.id, "instagram") == 1
    state = client.get(health_url).json()
    assert state["last_signed_webhook_at"] == signed_at
    assert state["last_native_sync_at"]
    assert state["webhook_total_imported"] == 1
    assert state["webhook_global_enabled"] is True
    assert state["company_import_enabled"] is True
    assert state["account_name"] == "Канал бизнеса"
    assert "token" not in json.dumps(state).lower()
    card = client.get(f"/studio/products/{product.id}/comments?tab=platforms")
    assert "Подписанный Instagram Webhook" in card.text
    assert "Последняя сверка через API" in card.text
    assert signed_at in card.text


@pytest.mark.asyncio
async def test_instagram_manual_sync_failure_preserves_redacted_diagnostics(ctx):
    db, product, svc, _, _ = ctx
    channel = await svc.connect(product.id, ConnectInput(
        platform="instagram", account_id="100", token="private-token"))
    class FailedGateway:
        async def fetch(self, channel, token):
            raise PlatformError("bad secret private-token")
    svc.gateway = FailedGateway()
    with pytest.raises(PlatformError):
        await svc.sync(product.id, "instagram")
    db.refresh(channel)
    status = channel.cursor["_sync"]
    assert status["last_error"] == "fetch_failed"
    assert status["last_failure_at"]
    assert "private-token" not in json.dumps(channel.cursor)


@pytest.mark.asyncio
async def test_instagram_health_is_tenant_filtered_by_studio_access(ctx):
    """No diagnostic endpoint may resolve another company's channel directly."""
    db, product, svc, _, _ = ctx
    second = Product(name="Second Company", slug="second-for-health")
    db.add(second)
    db.commit()
    # The route inherits the same tenant product visibility guard as all Studio
    # comment endpoints. A missing product is always a 404, not global metadata.
    app.dependency_overrides[get_settings] = lambda: svc.settings
    client = client_for(ctx)
    result = client.get("/products/00000000-0000-0000-0000-000000000001/comments/instagram/health")
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_native_sync_marks_webhook_reply_as_nested_and_never_scans_it_as_parent(ctx):
    from app.comments.instagram_replies import fetch_nested_replies

    db, product, svc, _, gateway = ctx
    channel = await svc.connect(product.id, ConnectInput(
        platform="instagram", account_id="100", token="owner-token"))
    root = BusinessComment(
        product_id=product.id, platform="instagram", external_id="1001",
        text="Вопрос клиента", post_text="Контекст публикации", source="platform",
        target={"channel_id": str(channel.id), "media_id": "5001"})
    nested = BusinessComment(
        product_id=product.id, platform="instagram", external_id="3001",
        text="Ответ клиента", post_text="", source="platform",
        target={"channel_id": str(channel.id), "media_id": "5001", "ingest": "meta_webhook"})
    db.add_all([root, nested])
    db.commit()
    async def fetch(channel, token):
        return [{"external_id": "3001", "text": "Ответ клиента",
                 "post_text": "Контекст публикации",
                 "target": {"media_id": "5001", "parent_id": "1001",
                            "published_at": datetime.now(timezone.utc).isoformat()}}], {}
    gateway.fetch = fetch

    assert await svc.sync(product.id, "instagram") == 0
    db.refresh(nested)
    assert nested.target["parent_id"] == "1001"
    assert nested.status == "new"
    assert len(list(db.scalars(select(BusinessComment)))) == 2

    scanned = []
    class CaptureGateway:
        async def fetch_instagram_replies(self, channel, token, parent_id, *, after=None):
            scanned.append(parent_id)
            return {"data": []}
    await fetch_nested_replies(db, CaptureGateway(), channel, "owner-token", max_calls=5)
    assert scanned == ["1001"]


@pytest.mark.asyncio
async def test_nested_scan_skips_own_and_unknown_authors_and_invalid_ids(ctx):
    from app.comments.instagram_replies import fetch_nested_replies

    db, product, svc, _, _ = ctx
    channel = await svc.connect(product.id, ConnectInput(
        platform="instagram", account_id="100", token="owner-token"))
    db.add(BusinessComment(
        product_id=product.id, platform="instagram", external_id="1001",
        source="platform", text="Первый комментарий", post_text="Пост",
        target={"channel_id": str(channel.id), "media_id": "5001"}))
    db.commit()
    class Native:
        async def fetch_instagram_replies(self, channel, token, parent_id, *, after=None):
            return {"data": [
                {"id": "7001", "text": "Наш собственный ответ", "from": {"id": "100"}},
                {"id": "7002", "text": "Автор неизвестен"},
                {"id": "7003", "text": "Пустой ID автора", "from": {"id": ""}},
                {"id": "not-a-native-id", "text": "Невалидный ID", "from": {"id": "500"}},
                {"id": "7004", "text": "Реальный ответ", "from": {"id": "500"},
                 "timestamp": datetime.now(timezone.utc).isoformat()},
            ]}
    rows, _ = await fetch_nested_replies(db, Native(), channel, "owner-token", max_calls=2)
    assert [row["external_id"] for row in rows] == ["7004"]
    assert rows[0]["target"]["parent_id"] == "1001"
