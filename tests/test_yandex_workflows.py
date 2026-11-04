"""Yandex integration contracts: company-scoped, source-backed, no live requests."""
from types import SimpleNamespace
import pytest

from app.api.yandex_discovery import SourceSelection, _source_rows
from app.seo import local_market


def test_market_research_produces_attributed_directory_evidence():
    rows = _source_rows([{
        "id": "123", "name": "Студия Южная", "address": "Сочи, улица Роз",
        "categories": ["Студия красоты"], "maps_url": "https://yandex.ru/maps/org/123",
        "website": "https://example.org"
    }], {"123"})
    assert len(rows) == 1
    assert rows[0].platform == "Яндекс Карты"
    assert rows[0].source_type == "website"
    assert "Сочи" in rows[0].excerpt
    assert str(rows[0].url) == "https://yandex.ru/maps/org/123"


def test_selection_does_not_accept_unrelated_organization():
    assert not _source_rows([{"id": "123", "name": "Test"}], {"other"})
    assert SourceSelection(query="салоны", city="Сочи", ids=["123"]).ids == ["123"]


@pytest.mark.asyncio
async def test_seo_local_market_competitors_are_not_search_volume(monkeypatch, tmp_path):
    monkeypatch.setenv("API_YANDEX", "test-key")
    company = SimpleNamespace(id="product-id", slug="company-1")
    db = SimpleNamespace(get=lambda model, product_id: company, commit=lambda: None)
    identity = SimpleNamespace(category="Салон красоты", industry=None,
                               service_geography=["Сочи"], geography=None)
    monkeypatch.setattr(local_market, "load_company_profile",
                        lambda root, slug: SimpleNamespace(profile=SimpleNamespace(identity=identity)))
    async def fake_search(niche, city, **kwargs):
        assert niche == "Салон красоты" and city == "Сочи"
        return {"organizations": [{
            "id":"123","name":"Студия Южная","address":"Сочи",
            "categories":["Салоны красоты"],"maps_url":"https://yandex.ru/maps/org/123",
            "website":""
        }]}
    monkeypatch.setattr(local_market, "search", fake_search)
    saved=[]
    class FakeArtifacts:
        def __init__(self, db): pass
        def create(self, **kwargs): saved.append(kwargs)
    monkeypatch.setattr(local_market, "ArtifactService", FakeArtifacts)
    result=await local_market.collect_local_market(db, company.id, "task-id", tmp_path)
    assert result["result_count"] == 1
    assert "frequency" not in result
    assert result["organizations"][0]["source_url"].endswith("/123")
    assert saved[0]["kind"] == "seo_local_market"


@pytest.mark.asyncio
async def test_seo_no_api_key_does_not_spend_queries(monkeypatch, tmp_path):
    monkeypatch.delenv("API_YANDEX", raising=False)
    assert await local_market.collect_local_market(None, "p", "t", tmp_path) is None
