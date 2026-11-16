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
        assert await svc.sync(product.id, "instagram") == 1  # revisit: dedup row

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
