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
    assert SourceSelection(query="салоны", city="Сочи", tokens=["signed.token"]).tokens == ["signed.token"]


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


@pytest.mark.asyncio
async def test_seo_seed_uses_only_current_task_competitor_snapshot(monkeypatch, tmp_path):
    from uuid import uuid4
    from app.seo import planner
    product_id, task_id = uuid4(), uuid4()
    monkeypatch.setattr(planner, "_company", lambda *a, **kw:
                        {"profile": "Профиль салона красоты", "industry": "Салон красоты"})
    looked_up = []
    def snapshot(db, product, task=None):
        looked_up.append(task)
        return {"task": str(task)} if task else None
    monkeypatch.setattr(planner, "snapshot_for_plan", snapshot)
    seen_payloads = []
    async def llm_answer(*args, **kwargs):
        seen_payloads.append(kwargs["payload"])
        return SimpleNamespace(seeds=["маникюр сочи"])
    monkeypatch.setattr(planner, "_ask", llm_answer)
    db = SimpleNamespace(get=lambda cls, obj: SimpleNamespace(id=obj))
    await planner.plan_seeds(db, None, None, product_id, products_root=tmp_path,
                             model="test", factory_task_id=task_id)
    await planner.plan_seeds(db, None, None, product_id, products_root=tmp_path,
                             model="test")
    assert looked_up == [task_id]
    assert seen_payloads[0]["company"]["local_market"] == {"task": str(task_id)}
    assert seen_payloads[1]["company"]["local_market"] is None


def test_market_research_is_company_scoped_without_forbidden_admin_link(monkeypatch, tmp_path):
    from uuid import uuid4
    from app.api import yandex_discovery
    product_id = uuid4()
    product = SimpleNamespace(id=product_id, name="Салон", slug="salon")
    monkeypatch.setattr(yandex_discovery, "_company", lambda db, pid: product)
    monkeypatch.setattr(yandex_discovery, "get_settings",
                        lambda: SimpleNamespace(products_root=tmp_path))
    monkeypatch.setattr(yandex_discovery, "load_company_profile",
                        lambda root, slug: SimpleNamespace(profile=None))
    monkeypatch.setattr(yandex_discovery, "studio_page_shell",
                        lambda *args, **kwargs: kwargs["body"])
    db = SimpleNamespace(scalars=lambda query: [])
    resp = yandex_discovery.market_research(product_id, db=db)
    content = resp.body.decode("utf-8")
    assert "Сохранённые источники компании" in content
    assert "/intelligence?product_id=" not in content
    assert "ym-saved" in content
