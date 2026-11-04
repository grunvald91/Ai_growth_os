"""Yandex can be used by agents automatically, without user-supplied evidence."""
import os
from types import SimpleNamespace

import pytest

from app.competitive_intelligence import yandex_sources


@pytest.mark.asyncio
async def test_discovery_uses_company_scope_and_enriches_public_sites(monkeypatch, tmp_path):
    monkeypatch.setenv("API_YANDEX", "dummy")
    identity = SimpleNamespace(category="Автосервис", industry="", service_geography=["Сочи"], geography="")
    monkeypatch.setattr(yandex_sources, "load_company_profile", lambda root, slug:
                        SimpleNamespace(profile=SimpleNamespace(identity=identity)))
    calls = []
    async def lookup(query, city, **kwargs):
        calls.append((query, city))
        return {"organizations": [{
            "id": "123", "name": "Сервис Южный", "address": "Сочи",
            "categories": ["Ремонт"], "maps_url": "https://yandex.ru/maps/org/123",
            "website": "https://example.org"
        }]}
    monkeypatch.setattr(yandex_sources, "search", lookup)
    class Fetcher:
        async def fetch(self, url):
            assert url == "https://example.org"
            return SimpleNamespace(status="read", text="Ремонт автотранспорта в Сочи по записи.")
    monkeypatch.setattr(yandex_sources, "PublicWebsiteFetcher", Fetcher)
    product = SimpleNamespace(slug="owner-company")
    request = SimpleNamespace(competitors=[], region="Сочи")
    bundle, status = await yandex_sources.collect_sources(product, request, tmp_path)
    assert status == "collected"
    assert calls == [("Автосервис", "Сочи")]
    assert len(bundle.sources) == 2
    assert all(source.source_type == "website" for source in bundle.sources)
    assert "Сочи" in bundle.sources[0].excerpt
    assert "Ремонт автотранспорта" in bundle.sources[1].excerpt


@pytest.mark.asyncio
async def test_missing_context_never_invents_organization(monkeypatch, tmp_path):
    monkeypatch.setenv("API_YANDEX", "dummy")
    identity = SimpleNamespace(category="", industry="", service_geography=[], geography="")
    monkeypatch.setattr(yandex_sources, "load_company_profile", lambda root, slug:
                        SimpleNamespace(profile=SimpleNamespace(identity=identity)))
    bundle, status = await yandex_sources.collect_sources(
        SimpleNamespace(slug="empty"), SimpleNamespace(region="", competitors=[]), tmp_path)
    assert bundle is None and status == "insufficient_company_context"
