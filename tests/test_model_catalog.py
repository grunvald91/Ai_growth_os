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


def test_every_gateway_model_is_offered_with_a_starting_class():
    from app.llm.model_catalog import gateway_models, is_text_model, suggest_tier
    profile = ModelProfile(model="kimi-k3", tier="balanced")
    ids = ("claude-opus-5-5", "claude-haiku-4.5", "gemini-3.1-flash-image", "grok-4.7", "kimi-k3")
    rows = {row["model"]: row for row in gateway_models(ids, (profile,))}
    assert set(rows) == set(ids)  # nothing is hidden, image models included
    assert rows["kimi-k3"]["configured"] and rows["kimi-k3"]["enabled"] and not rows["grok-4.7"]["configured"]
    assert suggest_tier("claude-opus-5-5") == "premium" and suggest_tier("claude-haiku-4.5") == "economy"
    assert suggest_tier("grok-4.7") == "balanced"
    assert rows["gemini-3.1-flash-image"]["text"] is False and is_text_model("claude-sonnet-5-5")
