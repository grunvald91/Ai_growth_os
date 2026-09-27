"""Deterministic policy, independent of model instructions or approval claims."""

from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict

from app.tools import ToolOperation


class RiskLevel(StrEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: ToolOperation
    risk_level: RiskLevel
    requires_approval: bool


_RISK_LEVELS = MappingProxyType({
    ToolOperation.RESEARCH: RiskLevel.GREEN,
    ToolOperation.ANALYTICS: RiskLevel.GREEN,
    ToolOperation.CALCULATE: RiskLevel.GREEN,
    ToolOperation.GENERATE_DRAFT: RiskLevel.GREEN,
    ToolOperation.DELEGATE_TASK: RiskLevel.GREEN,
    ToolOperation.PUBLISH: RiskLevel.YELLOW,
    ToolOperation.SEND_EMAIL: RiskLevel.YELLOW,
    ToolOperation.MODIFY_WEBSITE: RiskLevel.YELLOW,
    ToolOperation.LAUNCH_AD: RiskLevel.YELLOW,
    ToolOperation.INCREASE_BUDGET: RiskLevel.RED,
    ToolOperation.CHANGE_PRICING: RiskLevel.RED,
    ToolOperation.CHANGE_BILLING: RiskLevel.RED,
    ToolOperation.CHANGE_CREDENTIALS: RiskLevel.RED,
    ToolOperation.CHANGE_ACCESS: RiskLevel.RED,
    ToolOperation.DESTRUCTIVE_OPERATION: RiskLevel.RED,
})


class PolicyEvaluator:
    def evaluate(self, operation: ToolOperation) -> PolicyDecision:
        # Normalize at the boundary so unknown strings fail closed, never default GREEN.
        operation = ToolOperation(operation)
        risk = _RISK_LEVELS[operation]
        return PolicyDecision(
            operation=operation,
            risk_level=risk,
            requires_approval=risk != RiskLevel.GREEN,
        )
