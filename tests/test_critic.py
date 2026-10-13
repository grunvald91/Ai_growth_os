"""Tests for subordinate critics: strict scope, bounded calls and author veto."""
import json

import pytest
from pydantic import BaseModel, Field

from app.llm.schemas import LLMMessage, LLMRequest, LLMResponse, LLMUsage
from app.llm.service import LLMService
from app.privacy import PrivacyGateway


class Draft(BaseModel):
    title: str = Field(min_length=1)
    steps: list[str] = Field(min_length=1)


class SequenceProvider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return LLMResponse(content=json.dumps(output), model="fake",
                           usage=LLMUsage(input_tokens=10, output_tokens=20),
                           stop_reason="end_turn")


def task():
    return LLMRequest(
        system_prompt="Only prepare onboarding steps relevant to the user's goal.",
        messages=[LLMMessage(role="user", content="Plan customer onboarding.")],
        response_schema=Draft.model_json_schema(), model="fake", max_tokens=600,
        metadata={"team_role": "content_strategist", "task_type": "content_plan"},
    )


def finding(severity="major"):
    return {"findings": [{
        "id": "m1", "severity": severity, "problem": "No onboarding step described",
        "evidence_in_draft": "Output has only an acquisition step",
        "proposed_fix": "Add the onboarding action as a step",
        "link_to_original_goal": "The request is specifically about onboarding",
    }]}


async def run(provider, *, enabled=True):
    return await LLMService(privacy_gateway=PrivacyGateway(), provider=provider,
                            critic_enabled=enabled).generate(task())


@pytest.mark.asyncio
async def test_critic_can_recommend_one_goal_aligned_correction():
    provider = SequenceProvider([
        {"title": "Plan", "steps": ["Traffic"]}, finding(),
        {"decisions": [{"finding_id": "m1", "decision": "accept",
                        "rationale": "The user explicitly requested onboarding"}],
         "updated_output": {"title": "Plan", "steps": ["Traffic", "Onboarding"]}},
    ])
    result = await run(provider)
    assert json.loads(result.content)["steps"] == ["Traffic", "Onboarding"]
    assert result.quality_review["status"] == "revised"
    assert result.quality_review["extra_calls"] == 2
    assert result.usage.input_tokens == 30
    assert len(provider.requests) == 3


@pytest.mark.asyncio
async def test_no_material_findings_do_not_trigger_rework():
    provider = SequenceProvider([{"title": "Plan", "steps": ["Onboarding"]}, {"findings": []}])
    result = await run(provider)
    assert result.quality_review["status"] == "no_material_issues"
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_minor_findings_are_not_mandatory():
    provider = SequenceProvider([{"title": "Plan", "steps": ["Onboarding"]}, finding("minor")])
    result = await run(provider)
    assert json.loads(result.content)["steps"] == ["Onboarding"]
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_author_rejects_critic_and_preserves_original():
    provider = SequenceProvider([
        {"title": "Plan", "steps": ["Onboarding"]}, finding(),
        {"decisions": [{"finding_id": "m1", "decision": "reject",
                        "rationale": "Already sufficiently covered"}],
         "updated_output": {"title": "Unwanted", "steps": ["Unrelated"]}},
    ])
    result = await run(provider)
    assert json.loads(result.content)["title"] == "Plan"
    assert result.quality_review["status"] == "all_rejected"


@pytest.mark.asyncio
async def test_invalid_revision_falls_back_to_valid_original():
    provider = SequenceProvider([
        {"title": "Plan", "steps": ["Onboarding"]}, finding(),
        {"decisions": [{"finding_id": "m1", "decision": "accept",
                        "rationale": "Material improvement"}],
         "updated_output": {"title": "Oops"}},
    ])
    result = await run(provider)
    assert json.loads(result.content)["steps"] == ["Onboarding"]
    assert result.quality_review["status"] == "review_unavailable"


@pytest.mark.asyncio
async def test_critic_outage_does_not_break_original_task():
    provider = SequenceProvider([
        {"title": "Plan", "steps": ["Onboarding"]}, TimeoutError("not available"),
    ])
    result = await run(provider)
    assert result.quality_review["status"] == "review_unavailable"
    assert json.loads(result.content)["title"] == "Plan"


@pytest.mark.asyncio
async def test_disabled_default_does_not_add_calls():
    provider = SequenceProvider([{"title": "Plan", "steps": ["Onboarding"]}])
    result = await run(provider, enabled=False)
    assert len(provider.requests) == 1
    assert result.quality_review is None


@pytest.mark.asyncio
async def test_invalid_initial_draft_is_not_reviewed():
    provider = SequenceProvider([{"title": "Plan"}])
    result = await run(provider)
    assert len(provider.requests) == 1
    assert result.quality_review is None


@pytest.mark.asyncio
async def test_review_revision_schema_reuses_original_contract():
    from jsonschema import Draft202012Validator
    provider = SequenceProvider([
        {"title": "Draft", "steps": ["Traffic"]}, finding(),
        {"decisions": [{"finding_id": "m1", "decision": "accept",
                        "rationale": "Onboarding is essential"}],
         "updated_output": {"title": "Draft", "steps": ["Onboarding"]}},
    ])
    await run(provider)
    sent = provider.requests[2].response_schema
    assert sent["properties"]["updated_output"] == task().response_schema
    Draft202012Validator.check_schema(sent)
    Draft202012Validator(sent).validate({
        "decisions": [{"finding_id": "m1", "decision": "accept",
                       "rationale": "Onboarding is essential"}],
        "updated_output": {"title": "Draft", "steps": ["Onboarding"]},
    })


@pytest.mark.asyncio
async def test_large_context_is_compacted_instead_of_skipping_review():
    provider = SequenceProvider([
        {"title": "Draft", "steps": ["Onboarding"]},
        {"findings": []},
    ])
    request = task()
    request.messages[0].content = json.dumps({
        "request": {"goal": "Explain customer onboarding"},
        "task": {"title": "Onboarding steps"},
        "prior_stage_outputs": {"history": "example " * 9000},
    })
    result = await LLMService(privacy_gateway=PrivacyGateway(), provider=provider,
                              critic_enabled=True).generate(request)
    assert len(provider.requests) == 2
    assert result.quality_review["status"] == "no_material_issues"
