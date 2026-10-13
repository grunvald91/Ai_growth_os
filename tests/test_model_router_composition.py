"""Routing composition tests require no provider API calls."""
import json

import pytest

from app.core.composition import build_llm_provider
from app.core.config import Settings
from app.llm.fallback import FallbackProvider
from app.llm.model_router import ModelRoutingProvider, RoutingError
from app.llm.schemas import LLMMessage, LLMRequest


def test_router_disabled_preserves_current_fallback_provider():
    settings = Settings(_env_file=None, xai_api_key="placeholder", model_router_enabled=False)
    provider = build_llm_provider(settings)
    assert isinstance(provider, FallbackProvider)


def test_router_enabled_selects_per_role_without_network():
    profiles = [
        {"model": "cheap", "tier": "economy"},
        {"model": "business", "tier": "balanced"},
        {"model": "strategy", "tier": "premium"},
    ]
    settings = Settings(
        _env_file=None,
        xai_api_key="placeholder",
        model_router_enabled=True,
        model_router_profiles=json.dumps(profiles),
        model_router_overrides='{"company_intake":["business"],"cmo":["strategy"]}',
    )
    provider = build_llm_provider(settings)
    assert isinstance(provider, ModelRoutingProvider)
    def selected(role):
        return provider.router.select(LLMRequest(
            messages=[LLMMessage(role="user", content="hello")],
            metadata={"team_role": role},
        )).candidates[0]
    assert selected("company_intake") == "business"
    assert selected("cmo") == "strategy"
    assert selected("article_writer") == "cheap"


def test_router_enabled_refuses_premium_downgrade():
    settings = Settings(_env_file=None, xai_api_key="placeholder", model_router_enabled=True)
    provider = build_llm_provider(settings)
    with pytest.raises(RoutingError, match="premium"):
        provider.router.select(LLMRequest(
            messages=[LLMMessage(role="user", content="strategy")],
            metadata={"team_role": "cmo"},
        ))


def test_router_anthropic_mode_does_not_silently_redirect():
    settings = Settings(_env_file=None, llm_provider="anthropic",
                        anthropic_api_key="placeholder", model_router_enabled=True)
    with pytest.raises(ValueError, match="only"):
        build_llm_provider(settings)
