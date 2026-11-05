"""Yandex Mail availability depends on the server API, not license switches."""
import os
import asyncio
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from fastapi import HTTPException
from web.api.routes import mail_company_search as m


def test_yandex_provider_hidden_when_growth_api_unavailable(monkeypatch):
    async def cap(_path):
        return {"enabled": False}
    monkeypatch.setattr(m, "studio_get", cap)
    assert asyncio.run(m.providers(None))["yandex"] is False
    with pytest.raises(HTTPException) as err:
        asyncio.run(m.start(m.SearchInput(niche="салоны красоты", city="Сочи", provider="yandex"), None))
    assert err.value.status_code == 503


def test_yandex_provider_visible_with_key(monkeypatch):
    async def cap(_path):
        return {"enabled": True}
    monkeypatch.setattr(m, "studio_get", cap)
    assert asyncio.run(m.providers(None))["yandex"] is True


def test_yandex_supported_as_explicit_source():
    assert m.SearchInput(niche="автосервис", provider="yandex").provider == "yandex"
    assert m.SearchInput(niche="автосервис").provider == "2gis"


def test_yandex_job_preserves_sources_and_does_not_send_mail(monkeypatch, tmp_path):
    """Only public-site emails can become leads; nothing enters a campaign."""
    from web.api.routes.owner_mail import db_open
    monkeypatch.setenv("SERVGO_MAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SERVGO_YANDEX_MAIL_MAX_PAGES", "2")
    asked = []
    def entry(index):
        return {"id": str(index), "name": "Салон " + str(index), "address": "Сочи",
                "categories": ["Красота"], "phones": [], "website": "https://example.org"}
    async def gateway(path):
        asked.append(path)
        if path.endswith("/capabilities"):
            return {"enabled": True}
        if "skip=0" in path:
            return {"organizations": [entry(i) for i in range(1, 26)]}
        if "skip=25" in path:
            return {"organizations": [entry(1), entry(26)]}
        return {"organizations": []}
    def fake_site(*args):
        return {"mailto": ["hello@example.org"], "full_text": ""}
    async def fake_judge(card, brief):
        return 1, "Соответствует нише"
    monkeypatch.setattr(m, "studio_get", gateway)
    monkeypatch.setattr(m, "research_public_site", fake_site)
    monkeypatch.setattr(m, "judge", fake_judge)
    with db_open() as db:
        m.initialize(db)
        db.execute(
            "INSERT INTO search_job (id,niche,city,want,status,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            ("test-yandex", "салон красоты", "Сочи", None, "running", m.utcnow(), m.utcnow()))
    asyncio.run(m.run_yandex_job("test-yandex", "салон красоты", "Сочи", None))
    with db_open() as db:
        view = m._view(db, "test-yandex")
    assert view["job"]["status"] == "done"
    assert len(view["hits"]) == 26
    assert all(hit["source"] == "Яндекс Карты" for hit in view["hits"])
    assert all(hit["email"] == "hello@example.org" for hit in view["hits"])
    assert all(hit["fit"] == 1 for hit in view["hits"])
    assert any("skip=25" in path for path in asked)
