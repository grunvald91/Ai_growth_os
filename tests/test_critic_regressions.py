"""Regression cases for the five reviewer-confirmed critic defects.

No gateway, network, running worker or paid inference is used.
"""
import json
import sys
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from app.llm.critic import _review_messages
from app.llm.schemas import LLMMessage, LLMRequest, LLMResponse, LLMUsage
from app.llm.service import LLMService
from app.llm.anthropic import AnthropicProvider
from app.privacy import PrivacyGateway


class Draft(BaseModel):
    title: str
    steps: list[str] = Field(min_length=1)


def finding():
    return {"findings": [{
        "id": "fault1", "severity": "major",
        "problem": "Missing a material user requirement",
        "evidence_in_draft": "Requested step is missing",
        "proposed_fix": "Restore the specified action",
        "link_to_original_goal": "Original request explicitly requires this action",
    }]}


class FakeGateway:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(content=json.dumps(item), model="test-model",
                           usage=LLMUsage(input_tokens=12, output_tokens=15),
                           stop_reason="end_turn")


def req(payload):
    return LLMRequest(
        system_prompt="Follow the exact requested business context.",
        messages=[LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False))],
        response_schema=Draft.model_json_schema(),
        model="test-model", metadata={"team_role":"post_writer_editor", "task_type":"quick_post"},
    )


def checked(data):
    Draft.model_validate(data)


@pytest.mark.asyncio
async def test_revision_timeout_preserves_findings_and_original():
    gateway=FakeGateway([
        {"title":"Original", "steps":["Valid"]}, finding(),
        TimeoutError("author revision unavailable"),
    ])
    result=await LLMService(privacy_gateway=PrivacyGateway(), provider=gateway, critic_enabled=True).generate(
        req({"topic":"Flower care", "company_name":"Flowers", "audience":"Adults",
             "brief":"Helpful instructions", "channel":"telegram",
             "channel_guidance":"One clear CTA", "verified_facts":{}}),
        validate_revision=checked,
    )
    assert json.loads(result.content)["title"]=="Original"
    assert result.quality_review["status"]=="revision_unavailable"
    assert result.quality_review["findings"][0]["id"]=="fault1"
    assert result.quality_review["decisions"]==[]
    assert result.quality_review["extra_usage_by_model"][0]["model"]=="test-model"


@pytest.mark.asyncio
async def test_no_domain_validator_blocks_uncertified_replacement():
    gateway=FakeGateway([{"title":"Original", "steps":["Valid"]}, finding()])
    result=await LLMService(privacy_gateway=PrivacyGateway(), provider=gateway, critic_enabled=True).generate(
        req({"topic":"Flower care", "company_name":"Flowers", "audience":"Adults",
             "brief":"Helpful instructions", "channel":"telegram",
             "channel_guidance":"One clear CTA", "verified_facts":{}}),
    )
    assert json.loads(result.content)["title"]=="Original"
    assert result.quality_review["status"]=="advisory_only_no_domain_validator"
    assert len(gateway.requests)==2
    assert len(result.quality_review["findings"])==1


def test_large_quick_post_context_retains_goal_and_source_facts():
    payload={
        "company_name":"Flowers", "topic":"Selecting a bouquet",
        "audience":"Parents", "brief":"No fabricated guarantees",
        "channel":"telegram", "channel_guidance":"Single CTA",
        "verified_facts":{"src-1":"Certified supplier"},
        "company_context":"miscellaneous " * 3000,
    }
    messages=_review_messages(req(payload))
    assert messages is not None
    selected=json.loads(messages[0]["content"])
    for key in ("topic","audience","brief","channel","verified_facts"):
        assert selected[key]==payload[key]
    assert "company_context" not in selected
    assert selected["context_truncated"] is True


@pytest.mark.asyncio
async def test_oversized_verified_facts_skip_without_false_no_material_issues():
    payload={
        "company_name":"Flowers", "topic":"Choosing flowers", "audience":"Everyone",
        "brief":"Be precise", "channel":"telegram", "channel_guidance":"One CTA",
        "verified_facts":{"src-1":"evidence "*4000},
    }
    gateway=FakeGateway([{"title":"Original", "steps":["Valid"]}])
    result=await LLMService(privacy_gateway=PrivacyGateway(), provider=gateway, critic_enabled=True).generate(
        req(payload), validate_revision=checked,
    )
    assert len(gateway.requests)==1
    assert result.quality_review["status"]=="skipped_incomplete_context"


@pytest.mark.asyncio
async def test_anthropic_receives_full_revision_schema_in_system(monkeypatch):
    received=[]
    class Messages:
        async def create(self, **kwargs):
            received.append(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text",text='{"ok":true}')],
                model="test",usage=SimpleNamespace(input_tokens=9,output_tokens=8),
                stop_reason="end_turn",
            )
    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages=Messages()
    monkeypatch.setitem(sys.modules, "anthropic",
                       SimpleNamespace(AsyncAnthropic=FakeAnthropic))
    schema={"type":"object", "properties":{
        "decisions":{"type":"array","items":{"type":"object",
             "properties":{"finding_id":{"type":"string"}}, "required":["finding_id"]}},
        "updated_output":{"type":"object"},
    }, "required":["decisions","updated_output"]}
    result=await AnthropicProvider(api_key="unit-test",model="test").generate(LLMRequest(
        system_prompt="Return a JSON correction",
        messages=[LLMMessage(role="user",content="revise")], response_schema=schema,
    ))
    assert result.model=="test"
    system=received[0]["system"]
    assert "finding_id" in system
    assert "decisions" in system and "updated_output" in system
    assert json.dumps(schema,ensure_ascii=False) in system
