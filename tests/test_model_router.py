"""No-network tests for model routing, quality floors and cost/search guardrails."""
import json

import httpx
import pytest

from app.llm.model_router import (
    BudgetExceeded, ModelRouter, ModelRoutingProvider, RoutingError, WebSearchUnavailable,
)
from app.llm.schemas import LLMMessage, LLMRequest, LLMResponse, LLMUsage

PROFILES = [
    {"model": "small", "tier": "economy", "input_usd_per_million": "0.03", "output_usd_per_million": "0.03"},
    {"model": "mid", "tier": "balanced", "input_usd_per_million": "0.20", "output_usd_per_million": "0.20"},
    {"model": "best", "tier": "premium", "input_usd_per_million": "0.60", "output_usd_per_million": "0.60"},
]


def router(**kwargs):
    return ModelRouter(profiles_json=json.dumps(PROFILES), **kwargs)


def req(role="article_writer", **metadata):
    return LLMRequest(
        system_prompt="Only JSON",
        messages=[LLMMessage(role="user", content="Write an article")],
        max_tokens=1000, metadata={"team_role": role, **metadata},
    )


def test_economy_balanced_and_premium_selection():
    policy = router()
    assert policy.select(req("article_writer")).candidates[0] == "small"
    assert policy.select(req("company_intake")).candidates[0] == "mid"
    assert policy.select(req("cmo")).candidates == ("best",)
    assert policy.select(req("unrecognized_role")).candidates[0] == "mid"


def test_all_current_roles_and_four_extra_stages_have_policy():
    from app.agents import AgentRole
    from app.llm import model_router as m
    covered = m._PREMIUM_ROLES | m._BALANCED_ROLES | m._ECONOMY_ROLES
    assert {role.value for role in AgentRole} <= covered
    assert {"platform_writer", "repurposing_editor", "youtube_shorts_director",
            "instagram_reels_director"} <= covered


def test_critic_is_at_least_balanced_and_strategy_critic_is_premium():
    policy = router()
    assert policy.select(req("article_writer", routing_phase="critic")).candidates[0] == "mid"
    assert policy.select(req("cmo", routing_phase="critic")).candidates == ("best",)
    with pytest.raises(RoutingError):
        policy.select(req("cmo", routing_phase="infinite_debate"))


def test_manual_override_and_disabled_models():
    policy = router(overrides_json='{"company_intake":["best", "mid"]}')
    assert policy.select(req("company_intake")).candidates == ("best", "mid")
    with pytest.raises(RoutingError):
        router(overrides_json='{"cmo":["small"]}')
    with pytest.raises(RoutingError):
        router(overrides_json='{"cmo":["unknown"]}')


def test_disabled_model_and_unpriced_model_not_silently_used():
    policy = ModelRouter(profiles_json='[{"model":"best","tier":"premium","enabled":false}]')
    with pytest.raises(RoutingError, match="premium"):
        policy.select(req("cmo"))
    policy = ModelRouter(profiles_json='[{"model":"best","tier":"premium"}]', max_call_usd="0.10")
    with pytest.raises(BudgetExceeded):
        policy.select(req("cmo"))


def test_per_call_ceiling_rejects_overspend_and_invalid_prices():
    with pytest.raises(BudgetExceeded):
        router(max_call_usd="0.000001").select(req("cmo"))
    assert router(max_call_usd="1").select(req("cmo")).estimated_max_cost_usd is not None
    for bad in ("-1", "NaN", "Infinity", "oops", "0"):
        with pytest.raises(RoutingError):
            router(max_call_usd=bad)


def test_search_fails_closed_without_verified_end_to_end_adapter():
    request = req("company_intake", requires_web_search=True)
    with pytest.raises(WebSearchUnavailable):
        router().select(request)
    with pytest.raises(WebSearchUnavailable):
        ModelRouter(profiles_json='[{"model":"searchish","tier":"balanced","web_search_verified":true}]').select(request)
    with pytest.raises(WebSearchUnavailable):
        router(provider_supports_web_search=True).select(request)


def test_search_routes_only_validated_model_after_adapter_is_implemented():
    policy = ModelRouter(
        profiles_json=json.dumps(PROFILES + [{
            "model": "validated-search", "tier": "balanced", "web_search_verified": True
        }]),
        provider_supports_web_search=True,
    )
    assert policy.select(req("company_intake", requires_web_search=True)).candidates == ("validated-search",)


def test_malformed_catalog_and_overrides_rejected():
    for raw in ("NaN", '[{"model":"small","tier":"economy","enabled":"true"}]',
                '[{"model":"small","tier":"economy"},{"model":"small","tier":"premium"}]'):
        with pytest.raises(RoutingError):
            ModelRouter(profiles_json=raw)
    with pytest.raises(RoutingError):
        router(overrides_json='{"cmo":["best","best"]}')
    with pytest.raises(RoutingError):
        router(overrides_json='{"cmo":"best"}')


class FakeProvider:
    def __init__(self, failures=None):
        self.failures = list(failures or [])
        self.requests = []

    async def generate(self, request):
        self.requests.append(request.model_copy(deep=True))
        if self.failures:
            error = self.failures.pop(0)
            if error is not None:
                raise error
        return LLMResponse(
            content='{"ok":true}', model=request.model,
            usage=LLMUsage(input_tokens=15, output_tokens=10),
        )


def transient(status):
    request = httpx.Request("POST", "https://provider.example/chat")
    return httpx.HTTPStatusError(
        "failed", request=request,
        response=httpx.Response(status, request=request),
    )


@pytest.mark.asyncio
async def test_wrapper_forwards_selected_model_and_strips_internal_phase():
    fake = FakeProvider()
    provider = ModelRoutingProvider(provider=fake, router=router())
    result = await provider.generate(req("cmo", routing_phase="critic"))
    assert result.model == "best"
    assert fake.requests[0].metadata == {"team_role": "cmo"}


@pytest.mark.asyncio
async def test_transient_fallback_and_no_fallback_for_http_400():
    fake = FakeProvider(failures=[transient(429)])
    provider = ModelRoutingProvider(provider=fake, router=router())
    result = await provider.generate(req("article_writer"))
    assert [item.model for item in fake.requests] == ["small", "mid"]
    assert result.model == "mid"
    bad = FakeProvider(failures=[transient(400)])
    with pytest.raises(httpx.HTTPStatusError):
        await ModelRoutingProvider(provider=bad, router=router()).generate(req())
    assert len(bad.requests) == 1


@pytest.mark.asyncio
async def test_budget_rejection_never_contacts_provider():
    fake = FakeProvider()
    provider = ModelRoutingProvider(provider=fake, router=router(max_call_usd="0.000001"))
    with pytest.raises(BudgetExceeded):
        await provider.generate(req("cmo"))
    assert not fake.requests


@pytest.mark.asyncio
async def test_search_rejection_never_contacts_provider():
    fake = FakeProvider()
    provider = ModelRoutingProvider(provider=fake, router=router())
    with pytest.raises(WebSearchUnavailable):
        await provider.generate(req("company_intake", requires_web_search=True))
    assert not fake.requests


def test_high_complexity_never_downgrades_writer_and_overrides():
    assert router().select(req("article_writer", routing_complexity="high")).candidates == ("best",)
    with pytest.raises(RoutingError, match="quality floor"):
        router(overrides_json='{"article_writer":["small"]}').select(
            req("article_writer", routing_complexity="high")
        )
    with pytest.raises(RoutingError, match="routing_complexity"):
        router().select(req("article_writer", routing_complexity="fake"))


@pytest.mark.asyncio
async def test_router_prices_observed_usage_not_requested_token_ceiling():
    from decimal import Decimal
    fake = FakeProvider()
    r = ModelRoutingProvider(provider=fake, router=router())
    result = await r.generate(req("cmo"))
    assert result.model == "best"
    assert result.estimated_cost_usd == Decimal("0.000015")
    assert result.usage.input_tokens == 15


@pytest.mark.asyncio
async def test_alias_or_missing_usage_never_produces_false_estimate():
    class UnknownAliasProvider:
        async def generate(self, request):
            return LLMResponse(content="ok", model="unverified-alias",
                               usage=LLMUsage(input_tokens=100, output_tokens=10))

    from app.llm.schemas import LLMUsage
    aliased = await ModelRoutingProvider(
        provider=UnknownAliasProvider(), router=router()
    ).generate(req("cmo"))
    assert aliased.estimated_cost_usd is None

    class NoUsageProvider:
        async def generate(self, request):
            return LLMResponse(content="ok", model=request.model)

    missing = await ModelRoutingProvider(
        provider=NoUsageProvider(), router=router()
    ).generate(req("cmo"))
    assert missing.estimated_cost_usd is None
