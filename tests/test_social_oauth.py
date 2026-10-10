"""OAuth callback state, tenant scope and encrypted provider metadata tests."""
from urllib.parse import urlsplit, parse_qs
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.models import Base
from app.models.domain import Product
from app.social.models import SocialOAuthAttempt
from app.social import oauth
from app.comments.models import CommentChannel
from app.comments.secrets import CommentSecrets


@pytest.fixture
def ctx(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    first, second = Product(name="One", slug="one"), Product(name="Two", slug="two")
    db.add_all([first, second]); db.commit()
    settings = Settings(_env_file=None, products_root=tmp_path,
        comment_platform_proxy_url="http://foreign-proxy:8891",
        social_google_client_id="google-app", social_google_client_secret="private-google",
        social_instagram_client_id="instagram-app", social_instagram_client_secret="private-ig",
        social_oauth_redirect_base="https://growth.servgo.ru")
    yield db, first, second, settings
    db.close(); engine.dispose()


def test_auth_start_generates_unique_states_with_fixed_callback(ctx):
    db, first, second, settings = ctx
    one = parse_qs(urlsplit(oauth.authorize(db, settings, first.id, "youtube")).query)
    two = parse_qs(urlsplit(oauth.authorize(db, settings, second.id, "youtube")).query)
    assert one["redirect_uri"] == ["https://growth.servgo.ru/social/oauth/youtube/callback"]
    assert one["state"] != two["state"]
    assert one["access_type"] == ["offline"]
    assert len(list(db.scalars(select(SocialOAuthAttempt)))) == 2


@pytest.mark.asyncio
async def test_state_is_single_use_and_tokens_stay_encrypted(ctx, monkeypatch):
    db, first, _, settings = ctx
    query = parse_qs(urlsplit(oauth.authorize(db, settings, first.id, "youtube")).query)
    state = query["state"][0]

    async def fake_exchange(settings, method, url, **kwargs):
        return {"access_token": "SECRET_ACCESS_TOKEN", "refresh_token": "SECRET_REFRESH_TOKEN",
                "expires_in": 3600}

    class Gateway:
        async def request(self, method, url, token, **kwargs):
            assert token == "SECRET_ACCESS_TOKEN"
            return {"items": [{"id": "UC_CHANNEL", "snippet": {"title": "Own"}}]}

    class FakeService:
        def __init__(self, db, settings):
            self.db, self.settings = db, settings
            self.gateway = Gateway()

        async def connect(self, product_id, value):
            row = CommentChannel(product_id=product_id, platform=value.platform,
                account_id=value.account_id, account_name="Own", status="connected")
            self.db.add(row); self.db.commit()
            self.db.refresh(row)
            CommentSecrets(self.settings.products_root).save_token(row.id, value.token.get_secret_value())
            return row

    monkeypatch.setattr(oauth, "oauth_http", fake_exchange)
    monkeypatch.setattr(oauth, "CommentService", FakeService)
    connected = await oauth.complete(db, settings, "youtube", state, "code")
    info = CommentSecrets(settings.products_root).load_oauth_info(connected.id)
    assert info["refresh_token"] == "SECRET_REFRESH_TOKEN"
    assert b"SECRET_REFRESH_TOKEN" not in CommentSecrets(settings.products_root).oauth_path(connected.id).read_bytes()
    with pytest.raises(ValueError, match="уже использована"):
        await oauth.complete(db, settings, "youtube", state, "code")


@pytest.mark.asyncio
async def test_invalid_state_does_not_call_provider(ctx, monkeypatch):
    db, first, _, settings = ctx
    called = []
    async def forbidden(*args, **kwargs):
        called.append(True)
    monkeypatch.setattr(oauth, "oauth_http", forbidden)
    with pytest.raises(ValueError):
        await oauth.complete(db, settings, "youtube", "not-legit", "code")
    assert called == []
