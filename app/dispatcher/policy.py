"""What the dispatcher may do on its own. Decided in code, never by the model.

Risk follows CLAUDE.md (GREEN / YELLOW / RED). Cost adds a second axis: a paid GREEN action
still asks first, otherwise a mistaken tap or misheard word would start an expensive job.
"""
from __future__ import annotations

from enum import StrEnum

from app.policies import PolicyEvaluator, RiskLevel
from app.tools import ToolOperation


class Cost(StrEnum):
    NONE = "none"          # reads and navigation
    CHEAP = "cheap"        # a short text generation
    PAID_LATER = "paid_later"  # creates an inert draft; the paid step has its own approval
    PAID = "paid"          # spends money the moment it runs


class Verdict(StrEnum):
    RUN = "run"
    CONFIRM_FIRST = "confirm_first"
    REFUSE = "refuse"


def verdict(operation: ToolOperation, cost: Cost) -> Verdict:
    decision = PolicyEvaluator().evaluate(operation)
    if decision.risk_level == RiskLevel.RED:
        return Verdict.REFUSE
    if decision.risk_level == RiskLevel.YELLOW or cost == Cost.PAID:
        return Verdict.CONFIRM_FIRST
    return Verdict.RUN
