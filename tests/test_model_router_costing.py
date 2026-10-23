"""Read-only per-product usage accounting never treats 0 as a billed cost."""
from decimal import Decimal
from types import SimpleNamespace

from app.llm.costing import estimate_cost_usd, usage_cost_details
from app.llm.schemas import LLMResponse, LLMUsage
from app.llm.usage_report import summarize_usage_rows


def test_price_calculation_uses_actual_tokens_and_decimal_tariff():
    assert estimate_cost_usd(
        1000, 500, input_usd_per_million=Decimal("0.20"),
        output_usd_per_million=Decimal("0.50"),
    ) == Decimal("0.00045")


def test_unknown_cost_is_not_free_and_estimate_is_not_billed():
    response = LLMResponse(content="ok", model="model1", usage=LLMUsage(
        input_tokens=1000, output_tokens=500))
    assert usage_cost_details(response)["estimate_known"] is False
    response.estimated_cost_usd = Decimal("0.000450")
    detail = usage_cost_details(response)
    assert detail["cost_known"] is False
    assert detail["estimated_amount_usd"] == "0.000450"
    assert detail["price_basis"].startswith("operator_configured")


def test_role_cost_report_keeps_unknown_counts():
    def entry(n, estimate):
        return SimpleNamespace(
            input_tokens=n, output_tokens=n//2,
            details={} if estimate is None else {"estimated_amount_usd": estimate},
        )
    rows = [(entry(400, "0.00015"), "cmo"),
            (entry(300, None), "cmo"),
            (entry(500, "0.00020"), "content_strategist")]
    result = summarize_usage_rows(rows)
    assert result["total_calls"] == 3
    assert result["unpriced_calls"] == 1
    assert result["estimated_usd"] == "0.00035"
    assert result["by_role"]["cmo"]["unpriced_calls"] == 1
    assert result["pricing_status"] == "estimated_not_billed"


def test_calls_outside_agent_runs_are_named_by_their_task_type():
    """The owner's dispatcher has no agent run; its calls must not hide under "unknown"."""
    dispatcher = SimpleNamespace(input_tokens=900, output_tokens=150, details={"task_type": "dispatcher"})
    stray = SimpleNamespace(input_tokens=10, output_tokens=5, details={})
    result = summarize_usage_rows([(dispatcher, None), (stray, None)])
    assert result["by_role"]["dispatcher"]["calls"] == 1
    assert result["by_role"]["dispatcher"]["unpriced_calls"] == 1  # no price configured: stays unknown, not zero
    assert result["by_role"]["unknown"]["calls"] == 1
