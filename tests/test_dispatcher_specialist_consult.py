"""Dispatcher specialist handoff: one read-only answer in the current thread."""
import json
import time

import pytest
from sqlalchemy import select

from app.ai_chat.specialists import SPECIALISTS, get_specialist_prompt
from app.core.config import Settings
from app.dispatcher import service
from app.dispatcher.decision import DispatcherDecision
from app.dispatcher.fast_routes import quick_capability, quick_video_request
from app.dispatcher.models import DispatcherMessage
from app.dispatcher.outcome import Context
from app.dispatcher.specialist_consult import consult
from app.llm.schemas import LLMResponse, LLMUsage
from app.llm.service import LLMService
from app.models.domain import CostRecord, Product, Task, ToolCall
from app.privacy import PrivacyGateway


class Provider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return LLMResponse(content=response, model="deepseek-v4.1-flash",
                           usage=LLMUsage(input_tokens=50, output_tokens=20))


def context(db, tmp_path, **overrides):
    product = Product(name="Своя компания", slug="expert-own", description="Описание своего бизнеса")
    db.add(product)
    db.add(Product(name="ЧУЖАЯ КОМПАНИЯ", slug="expert-foreign", description="Чужие сведения"))
    db.commit()
    settings = Settings(_env_file=None, products_root=tmp_path, llm_provider="xai",
                        **overrides)
    return Context(db=db, product=product, settings=settings, account="owner")


def model(responses):
    provider = Provider(responses)
    return LLMService(privacy_gateway=PrivacyGateway(), provider=provider), provider


@pytest.mark.asyncio
@pytest.mark.parametrize("role", tuple(SPECIALISTS))
async def test_expert_uses_existing_role_prompt_and_no_actions(db, tmp_path, role):
    ctx = context(db, tmp_path)
    llm, provider = model([json.dumps({"intent": "specialist", "specialist_id": role,
                                       "confidence": 1}), "Рекомендация специалиста."])
    answer = await service.handle_message(db, ctx, llm, text="Нужна экспертная консультация",
                                          client_key="expert-1")
    assistant = answer["messages"][-1]
    assert assistant["intent"] == "specialist"
    assert SPECIALISTS[role]["name"] in assistant["text"]
    assert "Рекомендация специалиста." in assistant["text"]
    assert len(provider.requests) == 2
    request = provider.requests[-1]
    assert get_specialist_prompt(role) in request.system_prompt
    assert request.model == "deepseek-v4.1-flash"
    assert request.response_schema is None
    payload = json.loads(request.messages[-1].content)
    assert payload["company"] == "Своя компания"
    assert "ЧУЖАЯ" not in request.messages[-1].content
    assert [b["type"] for b in assistant["blocks"]] == ["text"]
    saved = db.scalar(select(DispatcherMessage).where(DispatcherMessage.intent == "specialist"))
    assert saved.params["specialist_id"] == role and saved.capability is None
    assert not list(db.scalars(select(Task))) and not list(db.scalars(select(ToolCall)))
    assert len(list(db.scalars(select(CostRecord)))) == 2
    replay = await service.handle_message(db, ctx, llm, text="Нужна экспертная консультация",
                                          client_key="expert-1")
    assert replay["replayed"] and replay["messages"][-1]["id"] == assistant["id"]
    assert len(provider.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", ["claude-opus-4-7", "gemini-3.1-flash-image", "unreviewed"])
async def test_expert_denies_unapproved_or_image_model_before_call(db, tmp_path, selected):
    ctx = context(db, tmp_path, dispatcher_llm_model=selected)
    llm, provider = model([])
    answer, actual = await consult(ctx, llm, "marketer", "Совет", [],
        deadline=time.monotonic()+10, record_cost=service.record_llm_cost)
    assert actual is None and "не настроена" in answer
    assert not provider.requests


@pytest.mark.asyncio
async def test_expert_budget_and_outage_fail_closed(db, tmp_path):
    ctx = context(db, tmp_path, model_router_enabled=False, model_router_max_call_usd="0.001")
    llm, provider = model([])
    answer, actual = await consult(ctx, llm, "marketer", "Совет", [],
        deadline=time.monotonic()+10, record_cost=service.record_llm_cost)
    assert actual is None and "предел стоимости" in answer and not provider.requests
    ctx.settings = ctx.settings.model_copy(update={"model_router_max_call_usd": None})
    llm, provider = model([RuntimeError("offline")])
    answer, actual = await consult(ctx, llm, "marketer", "Совет", [],
        deadline=time.monotonic()+10, record_cost=service.record_llm_cost)
    assert actual is None and "Консультация не выполнена" in answer
    assert len(provider.requests) == 1


def test_role_validation_and_explicit_routing():
    assert service._decision_needs_recovery(DispatcherDecision(
        intent="specialist", specialist_id="invented"))
    assert service._decision_needs_recovery(DispatcherDecision(
        intent="specialist", specialist_id="marketer", capability="schedule_post"))
    assert not service._decision_needs_recovery(DispatcherDecision(
        intent="specialist", specialist_id="marketer"))
    assert quick_capability("Маркетолог, посоветуй что сделать для бизнеса") is None
    assert quick_video_request("Копирайтер, подготовь объясняющее видео про записи") is None


def test_specific_advice_does_not_get_swallowed_by_daily_recommendations():
    assert quick_capability("Посоветуй стратегию продвижения бизнеса") is None
    assert quick_capability("Что улучшить в маркетинге компании?") is None
    assert quick_capability("Посоветуй рекламный бюджет бизнеса на сегодня") is None
    assert quick_capability("Что порекомендуешь сегодня сделать для бизнеса") == "recommend_today"
    assert service._decision_needs_recovery(DispatcherDecision.model_validate({
        "intent": "specialist", "specialist_id": "marketer",
        "next_chips": [{"label": "Опубликовать", "capability": "schedule_post", "params": {}}]}))


@pytest.mark.asyncio
async def test_expert_checks_fresh_router_limit_before_provider_call(db, tmp_path):
    ctx = context(db, tmp_path, model_router_enabled=True, model_router_max_call_usd="0.00000001",
                  model_router_profiles=json.dumps([{
                      "model": "deepseek-v4.1-flash", "tier": "economy", "enabled": True,
                      "input_usd_per_million": "1", "output_usd_per_million": "1"}]))
    llm, provider = model([])
    answer, actual = await consult(ctx, llm, "marketer", "Совет", [],
        deadline=time.monotonic()+10, record_cost=service.record_llm_cost)
    assert actual is None and not provider.requests
    assert "Консультация не выполнена" in answer


@pytest.mark.asyncio
async def test_expert_rejects_provider_model_substitution(db, tmp_path):
    ctx = context(db, tmp_path)
    class Substituted:
        async def generate(self, request):
            return LLMResponse(content="Нельзя показывать как проверенную консультацию",
                               model="gpt-4o-mini", usage=LLMUsage(input_tokens=50, output_tokens=20))
    llm = LLMService(privacy_gateway=PrivacyGateway(), provider=Substituted())
    answer, actual = await consult(ctx, llm, "marketer", "Совет", [],
        deadline=time.monotonic()+10, record_cost=service.record_llm_cost)
    assert actual is None and "Консультация не выполнена" in answer
    assert "Нельзя показывать" not in answer
    assert len(list(db.scalars(select(CostRecord)))) == 1
