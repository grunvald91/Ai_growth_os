from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agents import AGENT_CONFIGS, Agent, AgentRole
from app.llm.fake import FakeLLMProvider
from app.llm.schemas import LLMResponse
from app.llm.service import LLMService
from app.privacy import PrivacyGateway
from app.tools import FakeToolGateway, ToolOperation, ToolRequest, ToolStatus


class RecordingSkillLoader:
    def __init__(self):
        self.names = []

    def load(self, name):
        self.names.append(name)
        return SimpleNamespace(content="Selected skill password=skill-fixture")


def setup_agent(role=AgentRole.RESEARCH_PMM, operation=ToolOperation.RESEARCH):
    request = ToolRequest(operation=operation, product_id=uuid4(), task_id=uuid4(), resource_id=uuid4())
    provider = FakeLLMProvider(LLMResponse(content=request.model_dump_json(), model="fake"))
    gateway = FakeToolGateway()
    loader = RecordingSkillLoader()
    agent = Agent(role=role, llm_service=LLMService(privacy_gateway=PrivacyGateway(), provider=provider),
                  skill_loader=loader, tool_gateway=gateway)
    return agent, request, provider, gateway, loader


async def run(agent, request):
    return await agent.run(skill_name="research", task="Research password=task-fixture",
                           product_id=request.product_id, task_id=request.task_id,
                           resource_id=request.resource_id)


def test_frozen_configs_cover_declared_team_and_separation():
    assert set(AGENT_CONFIGS) == set(AgentRole)
    assert len(AGENT_CONFIGS) >= 10
    assert AGENT_CONFIGS[AgentRole.CMO].allowed_operations == {ToolOperation.DELEGATE_TASK}
    assert ToolOperation.PUBLISH not in AGENT_CONFIGS[AgentRole.CONTENT_SEO].allowed_operations
    for config in AGENT_CONFIGS.values():
        assert config.responsibilities
        with pytest.raises(ValidationError):
            config.role = AgentRole.CMO
    with pytest.raises(TypeError):
        AGENT_CONFIGS[AgentRole.CMO] = None


@pytest.mark.asyncio
async def test_one_skill_one_llm_call_and_sanitized_context():
    agent, request, provider, gateway, loader = setup_agent()
    result = await run(agent, request)
    assert result.status == ToolStatus.EXECUTED
    assert gateway.requests == [request]
    assert loader.names == ["research"]
    assert len(provider.requests) == 1
    outbound = provider.requests[0].model_dump_json()
    assert "skill-fixture" not in outbound
    assert "task-fixture" not in outbound
    assert "[REDACTED_SECRET]" in outbound


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", list(ToolOperation))
async def test_every_operation_capability_and_policy_before_execution(operation):
    role = AgentRole.DISTRIBUTION if operation in AGENT_CONFIGS[AgentRole.DISTRIBUTION].allowed_operations else AgentRole.RESEARCH_PMM
    agent, request, provider, gateway, _ = setup_agent(role, operation)
    result = await run(agent, request)
    if operation == ToolOperation.RESEARCH:
        assert result.status == ToolStatus.EXECUTED
        assert gateway.requests == [request]
    else:
        expected = ToolStatus.APPROVAL_REQUIRED if role == AgentRole.DISTRIBUTION else ToolStatus.CAPABILITY_DENIED
        assert result.status == expected
        assert gateway.requests == []
    assert len(provider.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("role,operation", [(AgentRole.CMO, ToolOperation.GENERATE_DRAFT),
                                          (AgentRole.CONTENT_SEO, ToolOperation.PUBLISH)])
async def test_specialist_boundary_cannot_be_overridden(role, operation):
    agent, request, _, gateway, _ = setup_agent(role, operation)
    assert (await agent.request_tool(request)).status == ToolStatus.CAPABILITY_DENIED
    assert gateway.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["not-json", '{"operation":"unknown"}', '[]'])
async def test_malformed_proposal_fails_without_execution(response):
    agent, request, provider, gateway, _ = setup_agent()
    provider.response.content = response
    with pytest.raises(ValidationError):
        await run(agent, request)
    assert len(provider.requests) == 1
    assert gateway.requests == []


@pytest.mark.asyncio
async def test_model_cannot_replace_scope_or_inject_credentials():
    agent, request, provider, gateway, _ = setup_agent()
    provider.response.content = request.model_copy(update={"product_id": uuid4()}).model_dump_json()
    with pytest.raises(ValueError, match="scope"):
        await run(agent, request)
    provider.response.content = request.model_dump_json()[:-1] + ',"credentials":"fixture"}'
    with pytest.raises(ValidationError):
        await run(agent, request)
    assert gateway.requests == []
