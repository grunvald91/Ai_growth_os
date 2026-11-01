"""The owner's dispatcher has a separately selected, fast classification model.

These tests do not use the upstream API. They protect independent model
selection without changing the other AI-team roles or their model router.
"""
import json
from types import SimpleNamespace

import pytest

from app.api.dispatcher import get_dispatcher_llm
from app.core.composition import build_dispatcher_provider, build_llm_provider
from app.core.config import Settings
from app.dispatcher import service
from app.dispatcher.executors import Context
from app.llm.xai import XAIProvider
from app.llm.model_router import ModelRoutingProvider
from app.models.domain import Product


def fast_settings(**updates):
    values = dict(xai_api_key="placeholder", llm_provider="xai", llm_model="grok-4.7",
                  dispatcher_llm_model="deepseek-v4.1-flash", model_router_enabled=False)
    values.update(updates)
    return Settings(_env_file=None, **values)


def test_dispatcher_uses_separate_verified_candidate():
    settings = fast_settings()
    dispatcher = build_dispatcher_provider(settings)
    assert isinstance(dispatcher, XAIProvider)
    assert dispatcher._model == "deepseek-v4.1-flash"
    assert dispatcher._model != settings.llm_model
    assert settings.dispatcher_llm_model == "deepseek-v4.1-flash"
    assert get_dispatcher_llm(settings)._provider._model == "deepseek-v4.1-flash"


def test_general_team_router_remains_unchanged():
    profiles = json.dumps([
        {"model": "kimi-2.7", "tier": "economy"},
        {"model": "deepseek-v4.1-flash", "tier": "economy"},
        {"model": "grok-4.7", "tier": "balanced"},
    ])
    settings = fast_settings(model_router_enabled=True,
                             model_router_profiles=profiles,
                             model_router_overrides='{"article_writer":["kimi-2.7"],"dispatcher":["kimi-2.7"]}')
    general = build_llm_provider(settings)
    dispatcher = build_dispatcher_provider(settings)
    assert isinstance(general, ModelRoutingProvider)
    assert general.router.overrides["article_writer"] == ("kimi-2.7",)
    assert dispatcher._model == "deepseek-v4.1-flash"


def test_dispatcher_only_can_be_reconfigured_independently():
    settings = fast_settings(dispatcher_llm_model="claude-haiku-5-5")
    assert build_dispatcher_provider(settings)._model == "claude-haiku-5-5"
    assert settings.llm_model == "grok-4.7"


def test_dispatcher_rejects_missing_model_id():
    settings = fast_settings(dispatcher_llm_model=" ")
    with pytest.raises(ValueError, match="DISPATCHER_LLM_MODEL"):
        build_dispatcher_provider(settings)


@pytest.mark.asyncio
async def test_dispatcher_llm_request_selects_dedicated_model(monkeypatch):
    captured = []

    class FakeLLM:
        async def generate(self, request, **kwargs):
            captured.append(request)
            return SimpleNamespace(
                content=json.dumps({"intent":"answer","reply_text":"Принято","confidence":0.9}),
                model="deepseek-v4.1-flash", usage=None)

    class FakeDB:
        def add(self, row):
            pass
        def commit(self):
            pass

    ctx = Context(db=None, product=Product(name="Company", slug="company"),
                  settings=fast_settings(), account="owner")
    monkeypatch.setattr(service, "snapshot", lambda *_: {"company":"Company"})
    monkeypatch.setattr(service, "record_llm_cost", lambda *args, **kwargs: None)
    response, name = await service.ask_model(FakeDB(), FakeLLM(), ctx.settings, ctx, [],
                                              "Напиши короткий пост")
    assert response.intent == "answer"
    assert name == "deepseek-v4.1-flash"
    assert captured[0].model == "deepseek-v4.1-flash"
    assert captured[0].metadata["team_role"] == "dispatcher"
