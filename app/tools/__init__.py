"""Credential-free tool contracts. Resource contents are resolved by adapters, not agents."""

from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ToolOperation(StrEnum):
    RESEARCH = "research"
    ANALYTICS = "analytics"
    CALCULATE = "calculate"
    GENERATE_DRAFT = "generate_draft"
    DELEGATE_TASK = "delegate_task"
    PUBLISH = "publish"
    SEND_EMAIL = "send_email"
    MODIFY_WEBSITE = "modify_website"
    LAUNCH_AD = "launch_ad"
    INCREASE_BUDGET = "increase_budget"
    CHANGE_PRICING = "change_pricing"
    CHANGE_BILLING = "change_billing"
    CHANGE_CREDENTIALS = "change_credentials"
    CHANGE_ACCESS = "change_access"
    DESTRUCTIVE_OPERATION = "destructive_operation"


class ToolRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: ToolOperation
    product_id: UUID
    task_id: UUID
    resource_id: UUID


class ToolStatus(StrEnum):
    EXECUTED = "executed"
    APPROVAL_REQUIRED = "approval_required"
    CAPABILITY_DENIED = "capability_denied"


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: ToolRequest
    status: ToolStatus


class ToolGateway(Protocol):
    """Execution seam; callers must authorize the request before invoking it."""

    async def execute(self, request: ToolRequest) -> ToolResult: ...


class FakeToolGateway:
    """Records calls only; never resolves resources or performs external writes."""

    def __init__(self) -> None:
        self.requests: list[ToolRequest] = []

    async def execute(self, request: ToolRequest) -> ToolResult:
        # Defense in depth: direct fake calls cannot bypass approval policy.
        # Role authorization belongs to Agent; gateway callers are trusted core code.
        from app.policies import PolicyEvaluator

        request = ToolRequest.model_validate(request.model_dump())
        if PolicyEvaluator().evaluate(request.operation).requires_approval:
            return ToolResult(request=request, status=ToolStatus.APPROVAL_REQUIRED)
        self.requests.append(request.model_copy(deep=True))
        return ToolResult(request=request, status=ToolStatus.EXECUTED)
