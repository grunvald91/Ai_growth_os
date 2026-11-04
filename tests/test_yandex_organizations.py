"""No live requests, no real credentials, and no external budget spend."""
import os
from pathlib import Path

import httpx
import pytest

from app.integrations.yandex_organizations import (
    YandexOrganizationsError, capability, normalize, search,
)


def test_normalization_never_invents_email():
    result = normalize({"features": [{"properties": {"CompanyMetaData": {
        "id": "77", "name": "Студия", "address": "Сочи",
        "url": "https://example.org", "Categories": [{"name": "Красота"}],
        "Phones": [{"formatted": "+7 900 000 00 00"}]
    }}}]})
    assert result["organizations"][0]["website"] == "https://example.org"
    assert result["organizations"][0]["email"] is None
    assert result["organizations"][0]["categories"] == ["Красота"]


@pytest.mark.asyncio
async def test_key_not_exposed_and_budget_is_enforced(monkeypatch, tmp_path):
    monkeypatch.setenv("API_YANDEX", "sensitive-test-key")
    monkeypatch.setenv("SERVGO_YANDEX_PPO_DAILY_BUDGET", "1")
    seen = []
    def responder(request: httpx.Request):
        seen.append(request.url.params.get("apikey"))
        return httpx.Response(200, json={"features": []})
    transport = httpx.MockTransport(responder)
    result = await search("парикмахерские", "Сочи", quota_path=tmp_path/"quota.sqlite",
                          transport=transport)
    assert result["organizations"] == []
    assert seen == ["sensitive-test-key"]
    with pytest.raises(YandexOrganizationsError) as err:
        await search("парикмахерские", "Сочи", quota_path=tmp_path/"quota.sqlite",
                     transport=transport)
    assert "sensitive-test-key" not in str(err.value)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_disabled_without_runtime_key(monkeypatch, tmp_path):
    monkeypatch.delenv("API_YANDEX", raising=False)
    assert not capability()["enabled"]
    with pytest.raises(YandexOrganizationsError):
        await search("автосервисы", "Сочи", quota_path=tmp_path/"quota.sqlite")
    assert not (tmp_path/"quota.sqlite").exists()


@pytest.mark.asyncio
async def test_key_alone_enables_search(monkeypatch, tmp_path):
    monkeypatch.setenv("API_YANDEX", "test-key")
    monkeypatch.delenv("SERVGO_YANDEX_PPO_LICENSE_CONFIRMED", raising=False)
    monkeypatch.delenv("SERVGO_YANDEX_PPO_STORAGE_ALLOWED", raising=False)
    def handle(request):
        return httpx.Response(200, json={"features": []})
    assert capability()["enabled"] is True
    result = await search("кафе", "Сочи", quota_path=tmp_path/"usage.sqlite",
                          transport=httpx.MockTransport(handle))
    assert result["organizations"] == []
