"""Bounded logical agents; capabilities and policy are enforced outside prompts."""

import json
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.llm.schemas import LLMMessage, LLMRequest
from app.llm.service import LLMService
from app.policies import PolicyEvaluator
from app.tools import ToolGateway, ToolOperation, ToolRequest, ToolResult, ToolStatus

if TYPE_CHECKING:
    from app.skills import SkillLoader


class AgentRole(StrEnum):
    CMO = "cmo"
    RESEARCH_PMM = "research_pmm"
    CONTENT_SEO = "content_seo"
    DISTRIBUTION = "distribution"
    ANALYTICS = "analytics"
    CONTENT_STRATEGIST = "content_strategist"
    SEO_EDITOR = "seo_editor"
    ARTICLE_WRITER = "article_writer"
    CONVERSION_EDITOR = "conversion_editor"
    SCRIPTWRITER = "scriptwriter"
    CREATIVE_DIRECTOR = "creative_director"
    VIDEO_PRODUCER = "video_producer"
    CRO = "cro"
    COMMUNITY_MANAGER = "community_manager"
    RESEARCH_LEAD = "research_lead"
    SEGMENT_RESEARCHER = "segment_researcher"
    CHANNEL_RESEARCHER = "channel_researcher"
    PROBLEM_RESEARCHER = "problem_researcher"
    COMPETITOR_VOC_RESEARCHER = "competitor_voc_researcher"


class AgentConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: AgentRole
    responsibilities: tuple[str, ...]
    allowed_operations: frozenset[ToolOperation]


AGENT_CONFIGS = MappingProxyType({
    AgentRole.CMO: AgentConfig(
        role=AgentRole.CMO,
        responsibilities=("Review evidence and propose strategy decisions.",
                          "Delegate specialist work; never execute it directly."),
        allowed_operations=frozenset({ToolOperation.DELEGATE_TASK}),
    ),
    AgentRole.RESEARCH_PMM: AgentConfig(
        role=AgentRole.RESEARCH_PMM,
        responsibilities=("Produce evidence-backed market, audience and positioning hypotheses.",),
        allowed_operations=frozenset({ToolOperation.RESEARCH}),
    ),
    AgentRole.CONTENT_SEO: AgentConfig(
        role=AgentRole.CONTENT_SEO,
        responsibilities=("Produce content and SEO briefs or drafts; never publish.",),
        allowed_operations=frozenset({ToolOperation.GENERATE_DRAFT}),
    ),
    AgentRole.DISTRIBUTION: AgentConfig(
        role=AgentRole.DISTRIBUTION,
        responsibilities=("Plan channel delivery of approved artifact references; external writes require approval.",),
        allowed_operations=frozenset({ToolOperation.PUBLISH, ToolOperation.SEND_EMAIL,
                                      ToolOperation.MODIFY_WEBSITE, ToolOperation.LAUNCH_AD}),
    ),
    AgentRole.ANALYTICS: AgentConfig(
        role=AgentRole.ANALYTICS,
        responsibilities=("Define and aggregate observable funnel evidence; do not make strategy decisions.",),
        allowed_operations=frozenset({ToolOperation.ANALYTICS, ToolOperation.CALCULATE}),
    ),
    AgentRole.CONTENT_STRATEGIST: AgentConfig(
        role=AgentRole.CONTENT_STRATEGIST,
        responsibilities=("Turn audience evidence into channel-specific content hypotheses and briefs.",),
        allowed_operations=frozenset({ToolOperation.GENERATE_DRAFT}),
    ),
    AgentRole.SEO_EDITOR: AgentConfig(
        role=AgentRole.SEO_EDITOR,
        responsibilities=("Design search-intent, query and structure recommendations without inventing demand.",),
        allowed_operations=frozenset({ToolOperation.GENERATE_DRAFT}),
    ),
    AgentRole.ARTICLE_WRITER: AgentConfig(
        role=AgentRole.ARTICLE_WRITER,
        responsibilities=("Write grounded long-form drafts from approved briefs; never publish.",),
        allowed_operations=frozenset({ToolOperation.GENERATE_DRAFT}),
    ),
    AgentRole.CONVERSION_EDITOR: AgentConfig(
        role=AgentRole.CONVERSION_EDITOR,
        responsibilities=("Design CTA, landing and funnel hypotheses from verified product context.",),
        allowed_operations=frozenset({ToolOperation.GENERATE_DRAFT, ToolOperation.CALCULATE}),
    ),
    AgentRole.SCRIPTWRITER: AgentConfig(
        role=AgentRole.SCRIPTWRITER,
        responsibilities=("Write short-form video scripts and hooks from grounded briefs.",),
        allowed_operations=frozenset({ToolOperation.GENERATE_DRAFT}),
    ),
    AgentRole.CREATIVE_DIRECTOR: AgentConfig(
        role=AgentRole.CREATIVE_DIRECTOR,
        responsibilities=("Create visual treatments, shot logic and creative direction; no rendering or publishing.",),
        allowed_operations=frozenset({ToolOperation.GENERATE_DRAFT}),
    ),
    AgentRole.VIDEO_PRODUCER: AgentConfig(
        role=AgentRole.VIDEO_PRODUCER,
        responsibilities=("Prepare bounded video production packages; no external rendering without a future adapter.",),
        allowed_operations=frozenset({ToolOperation.GENERATE_DRAFT}),
    ),
    AgentRole.CRO: AgentConfig(
        role=AgentRole.CRO,
        responsibilities=("Propose conversion and activation experiments tied to measurable funnel outcomes.",),
        allowed_operations=frozenset({ToolOperation.ANALYTICS, ToolOperation.CALCULATE,
                                      ToolOperation.GENERATE_DRAFT}),
    ),
    AgentRole.COMMUNITY_MANAGER: AgentConfig(
        role=AgentRole.COMMUNITY_MANAGER,
        responsibilities=("Plan non-spam community participation and distribution; external outreach requires approval.",),
        allowed_operations=frozenset({ToolOperation.PUBLISH, ToolOperation.SEND_EMAIL}),
    ),
    AgentRole.RESEARCH_LEAD: AgentConfig(
        role=AgentRole.RESEARCH_LEAD,
        responsibilities=("Decompose market research into specialist work and synthesize evidence without inventing consensus.",),
        allowed_operations=frozenset({ToolOperation.RESEARCH}),
    ),
    AgentRole.SEGMENT_RESEARCHER: AgentConfig(
        role=AgentRole.SEGMENT_RESEARCHER,
        responsibilities=("Form falsifiable customer-segment hypotheses from supplied product evidence.",),
        allowed_operations=frozenset({ToolOperation.RESEARCH}),
    ),
    AgentRole.CHANNEL_RESEARCHER: AgentConfig(
        role=AgentRole.CHANNEL_RESEARCHER,
        responsibilities=("Form falsifiable organic-channel hypotheses without claiming unobserved audience presence.",),
        allowed_operations=frozenset({ToolOperation.RESEARCH}),
    ),
    AgentRole.PROBLEM_RESEARCHER: AgentConfig(
        role=AgentRole.PROBLEM_RESEARCHER,
        responsibilities=("Map verified product capabilities to customer-problem hypotheses and evidence gaps.",),
        allowed_operations=frozenset({ToolOperation.RESEARCH}),
    ),
    AgentRole.COMPETITOR_VOC_RESEARCHER: AgentConfig(
        role=AgentRole.COMPETITOR_VOC_RESEARCHER,
        responsibilities=("Analyze only supplied competitor-review evidence and preserve source-level traceability.",),
        allowed_operations=frozenset({ToolOperation.RESEARCH}),
    ),
})


class Agent:
    def __init__(
        self,
        *,
        role: AgentRole,
        llm_service: LLMService,
        skill_loader: "SkillLoader",
        tool_gateway: ToolGateway,
    ) -> None:
        self.config = AGENT_CONFIGS[AgentRole(role)]
        self._llm_service = llm_service
        self._skill_loader = skill_loader
        self._tool_gateway = tool_gateway
        self._policy = PolicyEvaluator()

    async def run(
        self,
        *,
        skill_name: str,
        task: str,
        product_id: UUID,
        task_id: UUID,
        resource_id: UUID,
    ) -> ToolResult:
        """Load one skill, make one sanitized LLM call, authorize at most one tool.

        IDs are caller-owned context, not authority granted by model output. A future
        workflow can persist APPROVAL_REQUIRED with ApprovalService; this runner
        deliberately has no approved flag or approval-execution path.
        """
        skill = self._skill_loader.load(skill_name)
        scope = {"product_id": str(product_id), "task_id": str(task_id),
                 "resource_id": str(resource_id)}
        response = await self._llm_service.generate(LLMRequest(
            system_prompt=(
                f"Role: {self.config.role}\n"
                + "\n".join(self.config.responsibilities)
                + "\nSelected skill:\n" + skill.content
                + "\nReturn exactly one JSON ToolRequest matching this schema:\n"
                + json.dumps(ToolRequest.model_json_schema())
                + "\nUse only these caller-supplied references: " + json.dumps(scope)
                + "\nAllowed operations: "
                + ", ".join(sorted(self.config.allowed_operations))
            ),
            messages=[LLMMessage(role="user", content=task)],
        ))
        request = ToolRequest.model_validate_json(response.content)
        if (request.product_id, request.task_id, request.resource_id) != (
            product_id, task_id, resource_id
        ):
            raise ValueError("Tool proposal references do not match task scope")
        return await self.request_tool(request)

    async def request_tool(self, request: ToolRequest) -> ToolResult:
        """Authorize a typed proposal, without an LLM round trip or approval bypass."""
        request = ToolRequest.model_validate(request.model_dump())
        decision = self._policy.evaluate(request.operation)
        if request.operation not in self.config.allowed_operations:
            return ToolResult(request=request, status=ToolStatus.CAPABILITY_DENIED)
        if decision.requires_approval:
            return ToolResult(request=request, status=ToolStatus.APPROVAL_REQUIRED)
        return await self._tool_gateway.execute(request)
