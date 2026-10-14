"""Usage-based tariff estimates. Never confuse them with invoiced charges.

Unknown prices, missing usage, or unexpected gateway model aliases remain unknown.
All monetary values use Decimal and explicit USD units; no extra paid APIs.
"""
from __future__ import annotations

from decimal import Decimal

from app.llm.schemas import LLMResponse


def estimate_cost_usd(
    input_tokens: int,
    output_tokens: int,
    *,
    input_usd_per_million: Decimal,
    output_usd_per_million: Decimal,
) -> Decimal:
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("Token counts must be nonnegative")
    for price in (input_usd_per_million, output_usd_per_million):
        if not price.is_finite() or price < 0:
            raise ValueError("Prices must be nonnegative and finite")
    return (
        Decimal(input_tokens) * input_usd_per_million
        + Decimal(output_tokens) * output_usd_per_million
    ) / Decimal(1_000_000)


def usage_cost_details(response: LLMResponse | None) -> dict:
    """Safe CostRecord.details: USD estimates never masquerade as billed amounts."""
    if response is None:
        return {"cost_known": False, "estimate_known": False}
    estimate = response.estimated_cost_usd
    details = {
        "cost_known": False,
        "estimate_known": estimate is not None,
        "response_model": response.model,
    }
    if estimate is not None:
        details.update({
            "estimated_amount_usd": str(estimate),
            "price_basis": "operator_configured_usd_per_million_x_provider_reported_tokens",
            "billing_note": "Tariff estimate only; not reconciled with ClaudeHub invoice.",
        })
    else:
        details["billing_note"] = "Price or usable token counts unavailable; zero in amount is not free."
    return details
