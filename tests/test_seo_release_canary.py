"""Simulate the one-time release canary against the actual API and disposable DB."""
import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Product, Publication


SCRIPT = Path(__file__).parents[1] / "deploy/ops/20261102/seo_smoke_publish.py"
spec = importlib.util.spec_from_file_location("seo_release_canary", SCRIPT)
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)


@pytest.fixture
def isolated_api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    def factory():
        return Session(engine, expire_on_commit=False)
    def override():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as client:
            yield factory, client
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_canary_publish_live_http_then_cleanup(isolated_api):
    factory, client = isolated_api
    assert canary.perform(factory, client.get) is True
    with factory() as db:
        assert list(db.scalars(select(Product).where(Product.slug.like("seo-release-canary-%")))) == []
        assert list(db.scalars(select(Publication))) == []


def test_canary_cleans_up_if_public_endpoint_fails(isolated_api):
    factory, client = isolated_api
    feed_requests = 0
    def broken(path):
        nonlocal feed_requests
        if path.endswith("/articles"):
            feed_requests += 1
            if feed_requests == 1:
                class Response:
                    status_code = 503
                return Response()
        return client.get(path)
    with pytest.raises(AssertionError, match="CANARY_FEED_HTTP_503"):
        canary.perform(factory, broken)
    with factory() as db:
        assert list(db.scalars(select(Product).where(Product.slug.like("seo-release-canary-%")))) == []
        assert list(db.scalars(select(Publication))) == []
