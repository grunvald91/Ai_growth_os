import pytest

from app.policies import PolicyEvaluator, RiskLevel
from app.tools import ToolOperation


@pytest.mark.parametrize("operation,risk", [
    ("research", "GREEN"), ("analytics", "GREEN"), ("calculate", "GREEN"),
    ("generate_draft", "GREEN"), ("delegate_task", "GREEN"),
    ("publish", "YELLOW"), ("send_email", "YELLOW"),
    ("modify_website", "YELLOW"), ("launch_ad", "YELLOW"),
    ("increase_budget", "RED"), ("change_pricing", "RED"),
    ("change_billing", "RED"), ("change_credentials", "RED"),
    ("destructive_operation", "RED"), ("change_access", "RED"),
])
def test_every_baseline_action(operation, risk):
    decision = PolicyEvaluator().evaluate(operation)
    assert decision.operation == ToolOperation(operation)
    assert decision.risk_level == RiskLevel(risk)
    assert decision.requires_approval is (risk != "GREEN")


def test_all_enum_operations_have_policy():
    assert len(ToolOperation) == 15
    for operation in ToolOperation:
        PolicyEvaluator().evaluate(operation)


@pytest.mark.parametrize("operation", ["unknown", "PUBLISH", "", None])
def test_unknown_action_rejected(operation):
    with pytest.raises(ValueError):
        PolicyEvaluator().evaluate(operation)
