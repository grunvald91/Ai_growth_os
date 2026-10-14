"""The model listing probe makes only GET requests and spends zero LLM tokens."""
import httpx
import pytest

from app.llm.model_catalog import (
    CatalogUnavailable, catalog_comparison, list_gateway_models,
)
from app.llm.model_router import ModelProfile


@pytest.mark.asyncio
async def test_catalog_probe_lists_ids_using_get_only():
    seen = []
    def reply(request):
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer temporary"
        return httpx.Response(200, json={"data": [
            {"id": "kimi-k3"}, {"id": "claude-opus-5-5"}, {"id": "kimi-k3"}, {"id": 7},
        ]})
    found = await list_gateway_models(
        base_url="https://provider.example/v1",
        api_key="temporary", transport=httpx.MockTransport(reply),
    )
    assert found == ("claude-opus-5-5", "kimi-k3")
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_probe_refuses_http_redirect_and_invalid_catalog():
    for status, body in [
        (302, {"data": []}), (200, {"models": ["a"]}), (401, {"error": "invalid"}),
    ]:
        async def fake():
            return await list_gateway_models(
                base_url="https://provider.example/v1",
                api_key="temporary",
                transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body)),
            )
        with pytest.raises(CatalogUnavailable):
            await fake()


@pytest.mark.asyncio
async def test_probe_rejects_unsafe_url_without_network():
    for url in ("http://localhost:8000/v1", "https://u:p@provider.example/v1",
                "https://provider.example/v1?token=bad"):
        with pytest.raises(CatalogUnavailable):
            await list_gateway_models(base_url=url, api_key="secret")
    with pytest.raises(CatalogUnavailable):
        await list_gateway_models(base_url="https://provider.example/v1", api_key="")


def test_listed_model_does_not_claim_web_search():
    profile = ModelProfile(model="kimi-k3", tier="balanced", enabled=False)
    report = catalog_comparison(("kimi-k3",), (profile,))
    assert report == [{
        "model": "kimi-k3", "enabled": False,
        "listed_by_gateway": True, "web_search": "unverified", "prices": "unknown",
    }]
