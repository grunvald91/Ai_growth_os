"""Instagram Unified Login and Webhook tests; no external Meta requests."""
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.comments.contracts import AgentSettings, ConnectInput
from app.comments.gateway import CommentGateway, PlatformError
from app.comments.instagram_oauth import MetaUnifiedClient, new_state, verify_state
from app.comments.models import BusinessComment, CommentChannel
from app.core.config import get_settings
from app.main import app
from app.models.domain import Artifact, Approval, Publication
from test_comments import ctx, client_for


def meta_config(settings):
    settings.comment_meta_app_id = "111122223333"
    settings.comment_meta_app_secret = "only-for-test-meta-secret"
    settings.comment_meta_login_config_id = "998877"
    settings.comment_meta_webhook_verify_token = "test-verification-token"
    settings.comment_meta_webhook_enabled = True
    settings.comment_meta_public_base = "http://localhost"
    settings.comment_platform_proxy_url = "http://test-proxy:8891"
    settings.comment_meta_graph_version = "v27.0"


def signed(payload, secret):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return raw, "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def test_signed_oauth_state_is_tenant_user_and_browser_bound(ctx):
    db, product, svc, _, _ = ctx
    meta_config(svc.settings)
    state, nonce = new_state(svc.settings, product.id, "studio-owner")
    assert verify_state(svc.settings, state, nonce, product.id, "studio-owner")
    assert not verify_state(svc.settings, state, "wrong", product.id, "studio-owner")
    assert not verify_state(svc.settings, state, nonce, product.id, "other-user")
    assert not verify_state(svc.settings, state[:-2] + "xx", nonce, product.id, "studio-owner")


@pytest.mark.asyncio
async def test_unified_meta_client_exchanges_code_and_pages_assets(ctx):
    _, _, svc, _, _ = ctx
    meta_config(svc.settings)
    received = []
    async def transport(request):
        received.append(request)
        assert request.url.host == "graph.facebook.com"
        if request.url.path.endswith("/oauth/access_token"):
            assert request.url.params["client_secret"] == svc.settings.comment_meta_app_secret
            return httpx.Response(200, json={"access_token": "valid-meta-business-token"})
        if request.url.path.endswith("/me/assigned_instagram_accounts"):
            if request.url.params.get("after"):
                return httpx.Response(200, json={"data": [{"id": "200", "username": "second"}]})
            return httpx.Response(200, json={"data": [{"id": "100", "username": "first"}],
                                              "paging": {"next": "https://unsafe.example/path",
                                                         "cursors": {"after": "next-page"}}})
        if request.url.path.endswith("/200/media"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as test_client:
        meta = MetaUnifiedClient(svc.settings, client=test_client)
        token = await meta.exchange_code("code", "http://localhost/redirect")
        assert token == "valid-meta-business-token"
        assert [a["id"] for a in await meta.accounts(token)] == ["100", "200"]
        assert await meta.verify_account(token, "200") == "second"
        with pytest.raises(PlatformError):
            await meta.verify_account(token, "300")
    assert all(x.url.host == "graph.facebook.com" for x in received)


def test_public_webhook_signature_scoped_duplicated_and_no_publication(ctx):
    db, product, svc, _, gateway = ctx
    meta_config(svc.settings)
    app.dependency_overrides[get_settings] = lambda: svc.settings
    client = client_for(ctx)
    # Connect through a mock check; the real OAuth flow is tested separately.
    import asyncio
    asyncio.run(svc.connect(product.id, ConnectInput(
        platform="instagram", account_id="100", token="token-100")))
    svc.configure(product.id, AgentSettings(auto_import=True))
    path = "/api/integrations/instagram/webhook"
    assert client.get(path, params={"hub.mode": "subscribe", "hub.verify_token": "wrong",
                                    "hub.challenge": "1234"}).status_code == 403
    challenge = client.get(path, params={"hub.mode": "subscribe",
                "hub.verify_token": svc.settings.comment_meta_webhook_verify_token,
                "hub.challenge": "1234"})
    assert challenge.status_code == 200 and challenge.text == "1234"
    payload = {"object": "instagram", "entry": [
        {"id": "100", "changes": [
            {"field": "comments", "value": {
                "id": "12345", "text": "Сколько стоит услуга?",
                "media": {"id": "45678"}, "from": {"id": "54321"}}},
            {"field": "comments", "value": {
                "id": "12346", "text": "Ответ владельца",
                "media": {"id": "45678"}, "from": {"id": "100"}}}]},
        {"id": "999", "changes": [{"field": "comments", "value": {
            "id": "777", "text": "Unknown account",
            "media": {"id": "321"}, "from": {"id": "1"}}}]},
    ]}
    raw, signature = signed(payload, svc.settings.comment_meta_app_secret)
    bad = client.post(path, content=raw, headers={"x-hub-signature-256": "sha256=" + "0"*64})
    assert bad.status_code == 401
    first = client.post(path, content=raw, headers={"x-hub-signature-256": signature})
    second = client.post(path, content=raw, headers={"x-hub-signature-256": signature})
    assert first.status_code == 200 and first.json()["imported"] == 1
    assert second.status_code == 200 and second.json()["imported"] == 0
    rows = list(db.scalars(select(BusinessComment)))
    assert len(rows) == 1
    assert rows[0].text == "Сколько стоит услуга?"
    assert rows[0].target["channel_id"] == str(db.scalar(select(CommentChannel.id)))
    assert not list(db.scalars(select(Publication))) and not gateway.sent
    assert not list(db.scalars(select(Approval)))
    oversized = client.post(path, content=b"x" * (256*1024+1),
                     headers={"x-hub-signature-256": signature})
    assert oversized.status_code == 413


def test_unified_login_full_owner_flow_with_account_picker(ctx, monkeypatch):
    from app.api import comments_instagram
    db, product, svc, _, _ = ctx
    meta_config(svc.settings)
    app.dependency_overrides[get_settings] = lambda: svc.settings

    class FakeMeta:
        def __init__(self, settings):
            self.settings = settings
        def authorization_url(self, uri, state):
            from urllib.parse import quote
            return "https://www.facebook.com/oauth?state=" + quote(state, safe="")
        async def exchange_code(self, code, uri):
            assert code == "valid-code"
            return "valid-meta-business-token"
        async def accounts(self, token):
            assert token == "valid-meta-business-token"
            return [{"id": "100", "username": "first"},
                    {"id": "200", "username": "second"}]
        async def verify_account(self, token, account_id):
            assert token == "valid-meta-business-token"
            assert account_id == "200"
            return "second"

    monkeypatch.setattr(comments_instagram, "MetaUnifiedClient", FakeMeta)
    client = client_for(ctx)
    prefix = f"/products/{product.id}/comments/instagram/oauth"
    response = client.get(prefix + "/start", follow_redirects=False)
    assert response.status_code == 302
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    callback = client.get("/studio/integrations/instagram/oauth/callback",
                          params={"state": state, "code": "valid-code"},
                          follow_redirects=False)
    assert callback.status_code == 303
    select_url = callback.headers["location"]
    page = client.get(select_url)
    assert page.status_code == 200 and "second" in page.text and "first" in page.text
    pending = parse_qs(urlsplit(select_url).query)["pending"][0]
    # A forged selection is rejected. The valid pending token remains usable.
    forged = client.post(prefix + "/finish", data={"pending": pending, "account_id": "999"},
                         follow_redirects=False)
    assert forged.status_code == 403
    result = client.post(prefix + "/finish", data={"pending": pending, "account_id": "200"},
                         follow_redirects=False)
    assert result.status_code == 303
    db.expire_all()
    channel = db.scalar(select(CommentChannel).where(CommentChannel.product_id == product.id))
    assert channel.account_id == "200"
    assert channel.cursor["_auth"]["mode"] == "unified"
    assert svc.secrets.load_token(channel.id) == "valid-meta-business-token"
    assert not svc.secrets.path(db.scalar(select(Artifact).where(
        Artifact.kind == "comment_instagram_oauth_pending")).id).exists()
    replay = client.post(prefix + "/finish", data={"pending": pending, "account_id": "200"},
                         follow_redirects=False)
    assert replay.status_code == 404


@pytest.mark.asyncio
async def test_unified_gateway_uses_facebook_graph_and_preserves_native_parent():
    from types import SimpleNamespace
    seen = []
    async def handle(request):
        seen.append(request)
        assert request.url.host == "graph.facebook.com"
        if request.url.path.endswith("/123/media"):
            if len([r for r in seen if r.url.path.endswith("/123/media")]) == 1:
                return httpx.Response(200, json={"data": [{"id": "456", "caption": "Публикация"}]})
            return httpx.Response(200, json={"data": []})
        if request.url.path.endswith("/456/comments"):
            return httpx.Response(200, json={"data": [{
                "id": "999", "text": "Вопрос?", "timestamp": "2026-11-15T10:00:00+0000",
                "from": {"id": "other"}}]})
        if request.url.path.endswith("/me/assigned_instagram_accounts"):
            return httpx.Response(200, json={"data": [{"id": "123"}]})
        if request.url.path.endswith("/999"):
            return httpx.Response(200, json={"id": "999", "media": {"id": "456"}, "text": "Вопрос?"})
        if request.url.path.endswith("/999/replies"):
            return httpx.Response(200, json={"id": "published-reply"})
        raise AssertionError("unexpected " + str(request.url))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        gateway = CommentGateway(client=client)
        channel = SimpleNamespace(platform="instagram", account_id="123",
            cursor={"_auth": {"mode": "unified", "version": "v27.0"}})
        rows, next_cursor = await gateway.fetch(channel, "token")
        assert [x["external_id"] for x in rows] == ["999"]
        comment = SimpleNamespace(external_id="999", target={"media_id": "456"})
        assert await gateway.send(channel, "token", comment, "Ответ") == "published-reply"
    assert all(r.url.host == "graph.facebook.com" for r in seen)


@pytest.mark.asyncio
async def test_webhook_comment_gets_authoritative_time_and_caption_on_sync(ctx):
    from datetime import datetime, timezone
    from app.comments.service import recent_platform_comment

    db, product, svc, _, gateway = ctx
    channel = await svc.connect(product.id, ConnectInput(
        platform="instagram", account_id="100", token="owner-token"))
    record = BusinessComment(
        product_id=product.id, platform="instagram", external_id="987654321",
        text="Сколько стоит?", post_text="", source="platform", status="new",
        target={"channel_id": str(channel.id), "media_id": "555333",
                "ingest": "meta_webhook"},
    )
    db.add(record)
    db.commit()
    assert not recent_platform_comment(record, 30)

    published = datetime.now(timezone.utc).isoformat()

    async def fetch(connected, token):
        return [{
            "external_id": "987654321", "text": "Сколько стоит?",
            "post_text": "Публикация со всеми условиями",
            "target": {"media_id": "555333", "published_at": published},
        }], {}

    gateway.fetch = fetch
    assert await svc.sync(product.id, "instagram") == 0
    db.refresh(record)
    assert record.status == "new"
    assert record.post_text == "Публикация со всеми условиями"
    assert record.target["ingest"] == "meta_webhook"
    assert record.target["published_at"] == published
    assert recent_platform_comment(record, 30)
    assert await svc.sync(product.id, "instagram") == 0
    assert len(list(db.scalars(select(BusinessComment)))) == 1


@pytest.mark.asyncio
async def test_instagram_native_account_exclusive_to_one_company(ctx):
    from app.models.domain import Product
    from sqlalchemy.exc import IntegrityError

    db, first, svc, _, _ = ctx
    second = Product(name="Другая компания", slug="second-instagram-review")
    db.add(second)
    db.commit()
    await svc.connect(first.id, ConnectInput(
        platform="instagram", account_id="123456", token="token-A"))
    with pytest.raises(ValueError, match="другой компании"):
        await svc.connect(second.id, ConnectInput(
            platform="instagram", account_id="123456", token="token-B"))
    db.rollback()
    rows = list(db.scalars(select(CommentChannel).where(
        CommentChannel.platform == "instagram", CommentChannel.status == "connected")))
    assert len(rows) == 1 and rows[0].product_id == first.id
    # The partial UNIQUE index must also reject a concurrent/direct DB writer.
    db.add(CommentChannel(product_id=second.id, platform="instagram",
                          account_id="123456", account_name="clone", cursor={},
                          status="connected", revision=1))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    # The same native account may reconnect later after the old owner disconnects.
    svc.disconnect(first.id, "instagram")
    second_channel = await svc.connect(second.id, ConnectInput(
        platform="instagram", account_id="123456", token="token-B"))
    assert second_channel.product_id == second.id


def test_abandoned_oauth_pending_tokens_are_pruned(ctx):
    from time import time
    from sqlalchemy.orm import Session
    from app.comments.instagram_oauth import prune_expired_pending_oauth
    from app.models.domain import ArtifactStatus

    db, product, svc, _, _ = ctx
    old = Artifact(
        product_id=product.id, kind="comment_instagram_oauth_pending",
        title="Expired", content=json.dumps({"expires_at": time() - 100}),
        status=ArtifactStatus.DRAFT,
    )
    fresh = Artifact(
        product_id=product.id, kind="comment_instagram_oauth_pending",
        title="Still valid", content=json.dumps({"expires_at": time() + 500}),
        status=ArtifactStatus.DRAFT,
    )
    db.add_all([old, fresh]); db.flush()
    svc.secrets.save_token(old.id, "expired-token")
    svc.secrets.save_token(fresh.id, "valid-token")
    db.commit()

    session_factory = lambda: Session(db.get_bind(), expire_on_commit=False)
    assert prune_expired_pending_oauth(session_factory, svc.settings) == 1
    db.expire_all()
    assert db.get(Artifact, old.id).status == ArtifactStatus.REJECTED
    assert not svc.secrets.path(old.id).exists()
    assert db.get(Artifact, fresh.id).status == ArtifactStatus.DRAFT
    assert svc.secrets.load_token(fresh.id) == "valid-token"


def test_instagram_tenant_unique_migration_is_partial_and_compatible():
    from pathlib import Path
    import importlib.util
    from sqlalchemy import create_engine, inspect, text
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE comment_channels ("
                                    "id VARCHAR(36) PRIMARY KEY, "
                                    "platform VARCHAR(30) NOT NULL, "
                                    "status VARCHAR(30) NOT NULL, "
                                    "account_id VARCHAR(200) NOT NULL)"))
            path = Path(__file__).parents[1] / "alembic/versions/0016_comment_instagram_unique.py"
            spec = importlib.util.spec_from_file_location("migration_0016_instagram", path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            assert any(idx["name"] == "uq_comment_ig_connected_account"
                       for idx in inspect(connection).get_indexes("comment_channels"))
            connection.execute(text("INSERT INTO comment_channels VALUES "
                                    "('a','instagram','connected','123'),"
                                    "('b','instagram','disconnected','123')"))
            with pytest.raises(Exception):
                connection.execute(text(
                    "INSERT INTO comment_channels VALUES ('c','instagram','connected','123')"))
    finally:
        engine.dispose()


def test_album_comment_without_media_id_is_retained_but_not_publishable(ctx):
    import asyncio
    from app.comments.service import recent_platform_comment
    db, product, svc, _, gateway = ctx
    meta_config(svc.settings)
    app.dependency_overrides[get_settings] = lambda: svc.settings
    asyncio.run(svc.connect(product.id, ConnectInput(
        platform="instagram", account_id="100", token="owner-token")))
    svc.configure(product.id, AgentSettings(auto_import=True))
    raw, signature = signed({"object": "instagram", "entry": [
        {"id": "100", "changes": [{"field": "comments", "value": {
            "id": "99000", "text": "Вопрос к публикации-альбому",
            "from": {"id": "98765"}}}]}]}, svc.settings.comment_meta_app_secret)
    response = client_for(ctx).post("/api/integrations/instagram/webhook",
                                  content=raw, headers={"x-hub-signature-256": signature})
    assert response.status_code == 200 and response.json()["imported"] == 1
    row = db.scalar(select(BusinessComment).where(BusinessComment.external_id == "99000"))
    assert row.target.get("needs_media_resolution") is True
    assert "media_id" not in row.target
    assert not recent_platform_comment(row, 30)

    from datetime import datetime, timezone
    async def fetch(channel, token):
        return [{"external_id": "99000", "text": row.text,
                 "post_text": "Карточки с информацией",
                 "target": {"media_id": "999888", "published_at": datetime.now(timezone.utc).isoformat()}}], {}
    gateway.fetch = fetch
    asyncio.run(svc.sync(product.id, "instagram"))
    db.refresh(row)
    assert row.target["media_id"] == "999888"
    assert "needs_media_resolution" not in row.target
    assert recent_platform_comment(row, 30)
    assert row.post_text == "Карточки с информацией"


def test_webhook_batch_deduplicates_ids_inside_one_delivery(ctx):
    import asyncio
    db, product, svc, _, _ = ctx
    meta_config(svc.settings)
    app.dependency_overrides[get_settings] = lambda: svc.settings
    asyncio.run(svc.connect(product.id, ConnectInput(
        platform="instagram", account_id="100", token="owner-token")))
    svc.configure(product.id, AgentSettings(auto_import=True))
    changes = []
    for comment_id in ["55001", "55002", "55001", "55003"]:
        changes.append({"field": "comments", "value": {
            "id": comment_id, "text": f"Вопрос {comment_id}",
            "media": {"id": "123456"}, "from": {"id": "800"}}})
    raw, sig = signed({"object": "instagram", "entry": [
        {"id": "100", "changes": changes}]}, svc.settings.comment_meta_app_secret)
    client = client_for(ctx)
    response = client.post("/api/integrations/instagram/webhook",
                           content=raw, headers={"x-hub-signature-256": sig})
    assert response.status_code == 200
    assert response.json()["imported"] == 3
    assert len(list(db.scalars(select(BusinessComment)))) == 3
    again = client.post("/api/integrations/instagram/webhook",
                        content=raw, headers={"x-hub-signature-256": sig})
    assert again.status_code == 200 and again.json()["imported"] == 0


@pytest.mark.asyncio
async def test_unresolved_album_cannot_create_approval_or_send_claim(ctx):
    db, product, svc, _, gateway = ctx
    channel = await svc.connect(product.id, ConnectInput(
        platform="instagram", account_id="100", token="owner-token"))
    row = BusinessComment(product_id=product.id, platform="instagram",
                          external_id="112233", text="Как заказать?",
                          post_text="", source="platform", status="new",
                          target={"channel_id": str(channel.id), "ingest": "meta_webhook",
                                  "needs_media_resolution": True})
    db.add(row); db.commit()
    await svc.draft(product.id, row.id)
    with pytest.raises(ValueError, match="подтвердите публикацию Instagram"):
        await svc.send(product.id, row.id, "Ответ с подтверждением")
    db.refresh(row)
    assert row.status in {"draft", "review"}
    assert not list(db.scalars(select(Approval)))
    assert not list(db.scalars(select(Publication)))
    assert not gateway.sent


def test_webhook_import_requires_both_operator_and_company_opt_in(ctx):
    import asyncio
    db, product, svc, _, _ = ctx
    meta_config(svc.settings)
    app.dependency_overrides[get_settings] = lambda: svc.settings
    asyncio.run(svc.connect(product.id, ConnectInput(
        platform="instagram", account_id="100", token="owner-token")))
    raw, sig = signed({"object": "instagram", "entry": [
        {"id": "100", "changes": [{"field": "comments", "value": {
            "id": "73001", "text": "Нужна консультация",
            "media": {"id": "97001"}, "from": {"id": "81001"}}}]}]},
        svc.settings.comment_meta_app_secret)
    client = client_for(ctx)
    url = "/api/integrations/instagram/webhook"
    headers = {"x-hub-signature-256": sig}

    svc.settings.comment_meta_webhook_enabled = False
    assert client.post(url, content=raw, headers=headers).json()["imported"] == 0

    svc.settings.comment_meta_webhook_enabled = True
    assert client.post(url, content=raw, headers=headers).json()["imported"] == 0

    svc.configure(product.id, AgentSettings(enabled=False, auto_import=True))
    assert client.post(url, content=raw, headers=headers).json()["imported"] == 0

    svc.configure(product.id, AgentSettings(enabled=True, auto_import=True))
    assert client.post(url, content=raw, headers=headers).json()["imported"] == 1
    assert len(list(db.scalars(select(BusinessComment)))) == 1


def test_meta_webhook_revalidates_tenant_consent_between_entries(ctx, monkeypatch):
    """A later event in a signed batch must not bypass a mid-batch pause."""
    import asyncio
    from app.comments.contracts import AgentSettings
    from app.comments.service import CommentService

    db, product, svc, _, _ = ctx
    meta_config(svc.settings)
    app.dependency_overrides[get_settings] = lambda: svc.settings
    asyncio.run(svc.connect(product.id, ConnectInput(
        platform="instagram", account_id="100", token="owner-token")))

    checked = []
    def consent_that_is_revoked(self, product_id):
        checked.append(str(product_id))
        return (AgentSettings(enabled=True, auto_import=len(checked) == 1), None)
    monkeypatch.setattr(CommentService, "config", consent_that_is_revoked)

    payload = {"object": "instagram", "entry": [
        {"id": "100", "changes": [{"field": "comments", "value": {
            "id": "721001", "text": "Вопрос 1", "from": {"id": "800"},
            "media": {"id": "900"}}}]},
        {"id": "100", "changes": [{"field": "comments", "value": {
            "id": "721002", "text": "Вопрос 2", "from": {"id": "801"},
            "media": {"id": "900"}}}]},
    ]}
    raw, sig = signed(payload, svc.settings.comment_meta_app_secret)
    response = client_for(ctx).post(
        "/api/integrations/instagram/webhook",
        content=raw, headers={"x-hub-signature-256": sig},
    )
    assert response.status_code == 200
    assert response.json()["imported"] == 1
    assert len(checked) == 2
    assert [item.external_id for item in db.scalars(
        select(BusinessComment).order_by(BusinessComment.external_id)).all()] == ["721001"]
