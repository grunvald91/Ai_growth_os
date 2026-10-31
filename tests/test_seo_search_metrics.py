"""Read-only Search Console / Yandex Webmaster: strict URL tenant isolation.

MockTransports only. No real credentials, web requests, indexing or LLM spend.
"""
from datetime import datetime, timezone
import json

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.models.domain import Artifact, ArtifactStatus, Product, Publication
from app.seo.search_metrics import article_urls, company_search_metrics
from app.sso.models import StudioBusinessLink


@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        one = Product(name="Студия А", slug="studio-a")
        other = Product(name="Студия Б", slug="studio-b")
        db.add_all([one, other])
        db.flush()
        db.add_all([StudioBusinessLink(product_id=one.id, servgo_business_id=64),
                    StudioBusinessLink(product_id=other.id, servgo_business_id=65)])
        for product, slug, status in ((one, "main-topic", "published"),
                                      (one, "hidden-topic", "unpublished"),
                                      (other, "other-topic", "published")):
            a = Artifact(product_id=product.id, kind="article", title=slug, content="{}",
                         status=ArtifactStatus.PUBLISHED if status == "published"
                         else ArtifactStatus.APPROVED)
            db.add(a)
            db.flush()
            db.add(Publication(product_id=product.id, artifact_id=a.id,
                channel="own_site", status=status, external_reference="/blog/" + slug,
                published_at=datetime.now(timezone.utc)))
        db.commit()
        yield db, one.id, other.id
    engine.dispose()


@pytest.mark.asyncio
async def test_unconfigured_sources_are_unknown_not_zero(env):
    db, one, other = env
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: pytest.fail("No requests without credentials"))) as client:
        report = await company_search_metrics(db, one, days=28, client=client, env={})
    assert report["published_url_count"] == 1
    assert article_urls(db, other) == ["https://servgo.ru/business/65/blog/other-topic"]
    assert report["google_search_console"]["clicks"] is None
    assert report["yandex_webmaster"]["impressions"] is None
    assert report["bing_ai_performance"]["citations"] is None


@pytest.mark.asyncio
async def test_google_and_yandex_return_only_exact_published_company_urls(env):
    db, one, other = env
    mine = "https://servgo.ru/business/64/blog/main-topic"
    hidden = "https://servgo.ru/business/64/blog/hidden-topic"
    foreign = "https://servgo.ru/business/65/blog/other-topic"
    day = datetime.now(timezone.utc).date().isoformat()
    requests = []

    def fake(request):
        requests.append(request)
        if request.url.host == "www.googleapis.com":
            body = json.loads(request.content)
            assert body["dimensionFilterGroups"][0]["filters"][0]["expression"] == (
                "https://servgo.ru/business/64/blog/")
            assert request.headers["authorization"] == "Bearer secret-google"
            return httpx.Response(200, json={"rows": [
                {"keys": [mine], "clicks": 4, "impressions": 80},
                {"keys": [hidden], "clicks": 100, "impressions": 900},
                {"keys": [foreign], "clicks": 1000, "impressions": 9000},
            ]})
        assert request.url.host == "api.webmaster.yandex.net"
        assert request.headers["authorization"] == "OAuth secret-yandex"
        body = json.loads(request.content)
        assert body["text_indicator"] == "URL"
        assert body["filters"]["text_filters"][0]["value"] == (
            "https://servgo.ru/business/64/blog/")
        return httpx.Response(200, json={"count": 3, "text_indicator_to_statistics": [
            {"text_indicator": {"type": "URL", "value": mine},
             "statistics": [{"date": day, "field": "CLICKS", "value": 2},
                            {"date": day, "field": "IMPRESSIONS", "value": 17}]},
            {"text_indicator": {"type": "URL", "value": foreign},
             "statistics": [{"date": day, "field": "CLICKS", "value": 400}]},
            {"text_indicator": {"type": "URL", "value": hidden},
             "statistics": [{"date": day, "field": "CLICKS", "value": 50}]},
        ]})

    creds = {"SERVGO_GSC_READONLY_TOKEN": "secret-google",
             "SERVGO_GSC_SITE_URL": "sc-domain:servgo.ru",
             "SERVGO_YANDEX_WEBMASTER_TOKEN": "secret-yandex",
             "SERVGO_YANDEX_WEBMASTER_USER_ID": "123",
             "SERVGO_YANDEX_WEBMASTER_HOST_ID": "https:servgo.ru:443"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as client:
        report = await company_search_metrics(db, one, days=28, client=client, env=creds)

    google, yandex = report["google_search_console"], report["yandex_webmaster"]
    assert google["status"] == yandex["status"] == "connected"
    assert google["clicks"] == 4 and google["impressions"] == 80
    assert yandex["clicks"] == 2 and yandex["impressions"] == 17
    assert yandex["period_days"] == 14
    assert [x["url"] for x in google["pages"]] == [mine]
    assert [x["url"] for x in yandex["pages"]] == [mine]
    serialized = json.dumps(report)
    assert "secret-google" not in serialized and "secret-yandex" not in serialized
    assert "/business/65/" not in serialized and "hidden-topic" not in serialized
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_provider_error_never_looks_like_zero_traffic(env):
    db, pid, _ = env
    def unavailable(request):
        return httpx.Response(403, json={"error": "private provider message"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        report = await company_search_metrics(db, pid, client=client, env={
            "SERVGO_GSC_READONLY_TOKEN": "expired",
            "SERVGO_GSC_SITE_URL": "https://servgo.ru/",
            "SERVGO_YANDEX_WEBMASTER_TOKEN": "expired",
            "SERVGO_YANDEX_WEBMASTER_USER_ID": "123",
            "SERVGO_YANDEX_WEBMASTER_HOST_ID": "https:servgo.ru:443",
        })
    for source in ("google_search_console", "yandex_webmaster"):
        assert report[source]["status"] == "provider_unavailable"
        assert report[source]["clicks"] is None
        assert "private provider message" not in str(report)


@pytest.mark.asyncio
async def test_invalid_page_filters_are_not_accepted(env):
    db, pid, _ = env
    a = Artifact(product_id=pid, kind="article", title="Malformed", content="{}",
                 status=ArtifactStatus.PUBLISHED)
    db.add(a)
    db.flush()
    db.add(Publication(product_id=pid, artifact_id=a.id, channel="own_site",
                       status="published", external_reference="/blog/../../my",
                       published_at=datetime.now(timezone.utc)))
    db.commit()
    assert article_urls(db, pid) == ["https://servgo.ru/business/64/blog/main-topic"]
    with pytest.raises(ValueError, match="days"):
        await company_search_metrics(db, pid, days=365, env={})
