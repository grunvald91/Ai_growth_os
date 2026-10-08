"""Market research: no live Wordstat spend; mock all remote responses."""
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import HTTPException

from app.api.market_research import (
    BASE_URL, DurableCache, MarketQuery, Wordstat, _month_window, _phrases
)


def test_query_validation():
    assert MarketQuery(phrase="online appointment").region_id == 225
    with pytest.raises(ValueError):
        MarketQuery(phrase="x", num_phrases=200)


def test_twelve_complete_months():
    start, end = _month_window()
    dt_start = datetime.fromisoformat(start.replace("Z", "+00:00"))
    dt_end = datetime.fromisoformat(end.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    assert dt_start.month == now.month
    assert dt_start.year == now.year - 1
    assert dt_end > dt_start
    assert dt_end < now


def test_durable_budget(tmp_path):
    path = tmp_path / "cache.sqlite3"
    first = DurableCache(path, 2)
    first.reserve()
    assert first.remaining() == 1
    second = DurableCache(path, 2)
    second.reserve()
    assert first.remaining() == 0
    with pytest.raises(HTTPException) as error:
        second.reserve()
    assert error.value.status_code == 429


@pytest.mark.asyncio
async def test_top_contract_and_cache(tmp_path):
    calls = []
    def handler(req):
        calls.append(req)
        assert str(req.url) == BASE_URL + "/topRequests"
        assert req.headers["Authorization"] == "Api-Key test-key"
        payload = json.loads(req.content)
        assert payload["folderId"] == "test-folder"
        assert payload["regions"] == ["225"]
        return httpx.Response(200, json={
            "totalCount": "120", "results": [{"phrase": "запись", "count": "120"}],
            "associations": [{"phrase": "запись клиентов", "count": "55"}],
        })
    cache = DurableCache(tmp_path / "cache.sqlite3", 2)
    client = Wordstat("test-key", "test-folder", cache, transport=httpx.MockTransport(handler))
    payload = {"phrase": "запись", "regions": ["225"], "devices": ["DEVICE_ALL"],
               "numPhrases": "20"}
    r1, cached1 = await client.request("topRequests", payload)
    r2, cached2 = await client.request("topRequests", payload)
    assert r1 == r2 and not cached1 and cached2 and len(calls) == 1
    assert cache.remaining() == 1
    assert _phrases(r1["associations"]) == [{"phrase": "запись клиентов", "count": 55}]


@pytest.mark.asyncio
async def test_auth_error_redacted_and_reserved(tmp_path):
    def handler(_):
        return httpx.Response(403, text="private key leak")
    cache = DurableCache(tmp_path / "cache.sqlite3", 1)
    client = Wordstat("confidential", "folder", cache,
                      transport=httpx.MockTransport(handler))
    with pytest.raises(HTTPException) as e:
        await client.request("topRequests", {"phrase": "test"})
    assert e.value.status_code == 503
    assert "confidential" not in str(e.value.detail)
    assert cache.remaining() == 0
