import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from app.llm.service import LLMService
from app.llm.schemas import LLMMessage, LLMRequest, LLMResponse, LLMUsage
from app.llm.model_router import ModelRouter, ModelRoutingProvider
from app.llm.audit import critic_cost_details
from app.privacy import PrivacyGateway


@pytest.mark.asyncio
async def test_critic_uses_original_role_critic_route():
    calls = []
    class Provider:
        async def generate(self, request):
            calls.append(request)
            content = {"title": "draft"} if len(calls) == 1 else {"findings": []}
            return LLMResponse(content=json.dumps(content), model=request.model,
                usage=LLMUsage(input_tokens=10, output_tokens=10), stop_reason="end_turn")
    router = ModelRouter(profiles_json=json.dumps([
        {"model": "author", "tier": "premium"},
        {"model": "reviewer", "tier": "premium"}]),
        overrides_json=json.dumps({"cmo": ["author"], "cmo:critic": ["reviewer"]}))
    service = LLMService(privacy_gateway=PrivacyGateway(allowed_context_fields={
        "team_role", "task_type", "reasoning_effort", "routing_phase", "provider_timeout_seconds"}),
        provider=ModelRoutingProvider(provider=Provider(), router=router), critic_enabled=True)
    schema = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
    result = await service.generate(LLMRequest(messages=[LLMMessage(role="user", content="draft")],
        response_schema=schema, metadata={"team_role": "cmo"}))
    assert [c.model for c in calls] == ["author", "reviewer"]
    assert result.quality_review["status"] == "no_material_issues"


def test_cost_estimate_survives_critic_audit():
    response = LLMResponse(content="ok", model="author", estimated_cost_usd=Decimal("0.001"),
        usage=LLMUsage(input_tokens=1, output_tokens=2))
    detail = critic_cost_details(response)
    assert detail["estimated_amount_usd"] == "0.001"
    assert detail["cost_known"] is False
