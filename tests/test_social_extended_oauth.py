"""VK ID (PKCE) and Meta Threads OAuth contracts without real provider credentials."""
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.models import Base
from app.models.domain import Product
from app.social.models import SocialOAuthAttempt
from app.social import oauth, extended_oauth
from app.comments.models import CommentChannel
from app.comments.secrets import CommentSecrets


@pytest.fixture
def state(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    product = Product(name="Growth", slug="growth")
    db.add(product); db.commit()
    settings = Settings(_env_file=None, products_root=tmp_path,
        comment_platform_proxy_url="http://proxy.test:8891",
        social_threads_client_id="threads-id", social_threads_client_secret="threads-secret",
        social_vk_client_id="vk-id", social_oauth_redirect_base="https://growth.servgo.ru")
    yield db, product, settings
    db.close(); engine.dispose()


def query(url):
    return parse_qs(urlsplit(url).query)


def test_threads_auth_uses_dedicated_app_and_scopes(state):
    db, product, settings = state
    url = oauth.authorize(db, settings, product.id, "threads")
    params = query(url)
    assert urlsplit(url).netloc == "threads.net"
    assert params["redirect_uri"] == ["https://growth.servgo.ru/social/oauth/threads/callback"]
    assert params["client_id"] == ["threads-id"]
    assert "threads_manage_replies" in params["scope"][0]


def test_vk_id_auth_pkce_binds_group_and_scopes_without_manual_token(state):
    db, product, settings = state
    with pytest.raises(ValueError):
        oauth.authorize(db, settings, product.id, "vk", account_id="club123")
    url = oauth.authorize(db, settings, product.id, "vk", account_id="123")
    params = query(url)
    assert urlsplit(url).netloc == "id.vk.com"
    assert params["response_type"] == ["code"]
    assert params["code_challenge_method"] == ["s256"]
    assert params["scope"] == ["groups wall"]
    assert params["redirect_uri"] == ["https://growth.servgo.ru/social/oauth/vk/callback"]
    assert params["code_challenge"][0]
    attempt = db.scalar(select(SocialOAuthAttempt))
    assert attempt.target_account_id == "123"
    assert attempt.pkce_verifier and attempt.pkce_verifier not in url


@pytest.mark.asyncio
async def test_threads_oauth_exchange_never_returns_tokens_to_browser(state, monkeypatch):
    db, product, settings = state
    params = query(oauth.authorize(db, settings, product.id, "threads"))
    secret = "test-access-token"
    calls = []

    async def fake_http(settings, method, url, **kwargs):
        calls.append(url)
        if url.endswith("/oauth/access_token"):
            assert kwargs["params"]["client_secret"] == "threads-secret"
            return {"access_token": secret, "user_id": "456"}
        if url.endswith("/access_token"):
            assert kwargs["params"]["access_token"] == secret
            return {"access_token": "LONG_LIVED", "expires_in": 5184000}
        raise AssertionError(url)

    class VerifiedService:
        def __init__(self, db, settings):
            self.db, self.settings = db, settings
        async def connect(self, product_id, request):
            assert request.platform == "threads"
            assert request.account_id == "456"
            assert request.token.get_secret_value() == "LONG_LIVED"
            row = CommentChannel(product_id=product_id, platform="threads",
                account_id="456", account_name="mythreads", status="connected")
            self.db.add(row); self.db.commit(); self.db.refresh(row)
            CommentSecrets(settings.products_root).save_token(row.id, "LONG_LIVED")
            return row

    monkeypatch.setattr(oauth, "oauth_http", fake_http)
    monkeypatch.setattr(extended_oauth, "CommentService", VerifiedService)
    connected = await oauth.complete(db, settings, "threads", params["state"][0], "CODE")
    assert connected.product_id == product.id
    assert CommentSecrets(settings.products_root).load_oauth_info(connected.id)["platform"] == "threads"
    assert b"LONG_LIVED" not in CommentSecrets(settings.products_root).path(connected.id).read_bytes()
    with pytest.raises(ValueError, match="уже использована"):
        await oauth.complete(db, settings, "threads", params["state"][0], "CODE")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_vk_id_state_device_and_group_must_match(state, monkeypatch):
    db, product, settings = state
    params = query(oauth.authorize(db, settings, product.id, "vk", account_id="123"))
    state_code = params["state"][0]
    async def fake_http(settings, method, url, **kwargs):
        assert url == "https://id.vk.com/oauth2/auth"
        assert kwargs["params"]["state"] == state_code
        assert kwargs["params"]["code_verifier"]
        assert kwargs["params"]["device_id"] == "device-123456789"
        return {"state": state_code, "access_token": "VK_SECRET",
                "refresh_token": "VK_REFRESH", "expires_in": 3600}

    class VerifiedService:
        def __init__(self, db, settings):
            self.db, self.settings = db, settings
        async def connect(self, product_id, request):
            assert request.account_id == "123"
            assert request.platform == "vk"
            assert request.token.get_secret_value() == "VK_SECRET"
            row = CommentChannel(product_id=product_id, platform="vk",
                account_id="123", account_name="Community", status="connected")
            self.db.add(row); self.db.commit(); self.db.refresh(row)
            CommentSecrets(settings.products_root).save_token(row.id, "VK_SECRET")
            return row

    monkeypatch.setattr(oauth, "oauth_http", fake_http)
    monkeypatch.setattr(extended_oauth, "CommentService", VerifiedService)
    channel = await oauth.complete(db, settings, "vk", state_code, "code",
                                   device_id="device-123456789")
    assert channel.account_id == "123"
    metadata = CommentSecrets(settings.products_root).load_oauth_info(channel.id)
    assert metadata["device_id"] == "device-123456789"
    assert metadata["refresh_token"] == "VK_REFRESH"
    assert "VK_REFRESH" not in (CommentSecrets(settings.products_root).oauth_path(channel.id).read_bytes()).decode("latin1")
