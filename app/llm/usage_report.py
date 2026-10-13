"""Read-only cost reporting for a single product.

Billing charge is not known unless separately reconciled. A configured-price
estimate is a different metric from the CostRecord.amount billed field.
This module creates no public unauthenticated endpoint.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import AgentRun, CostRecord, Product


def summarize_usage_rows(rows) -> dict:
    by_role = defaultdict(lambda: {
        "calls": 0, "input_tokens": 0, "output_tokens": 0,
        "estimated_usd": Decimal("0"), "unpriced_calls": 0,
    })
    for record, role in rows:
        group = by_role[role or "unknown"]
        group["calls"] += 1
        group["input_tokens"] += record.input_tokens or 0
        group["output_tokens"] += record.output_tokens or 0
        raw = (record.details or {}).get("estimated_amount_usd")
        try:
            value = Decimal(str(raw)) if raw is not None else None
        except (InvalidOperation, TypeError, ValueError):
            value = None
        if value is None or not value.is_finite() or value < 0:
            group["unpriced_calls"] += 1
        else:
            group["estimated_usd"] += value
    by_role_output = {
        role: {**{key: value for key, value in info.items()
                   if key != "estimated_usd"},
               "estimated_usd": str(info["estimated_usd"])}
        for role, info in sorted(by_role.items())
    }
    return {
        "pricing_status": "estimated_not_billed",
        "total_calls": sum(info["calls"] for info in by_role.values()),
        "unpriced_calls": sum(info["unpriced_calls"] for info in by_role.values()),
        "input_tokens": sum(info["input_tokens"] for info in by_role.values()),
        "output_tokens": sum(info["output_tokens"] for info in by_role.values()),
        "estimated_usd": str(sum((info["estimated_usd"] for info in by_role.values()), Decimal("0"))),
        "by_role": by_role_output,
    }


def report_product_usage(db: Session, product_id: UUID, *, limit: int = 2000) -> dict:
    if not 1 <= limit <= 10000:
        raise ValueError("Invalid reporting limit")
    if db.get(Product, product_id) is None:
        raise LookupError("Product not found")
    rows = list(db.execute(
        select(CostRecord, AgentRun.agent_name)
        .outerjoin(AgentRun, AgentRun.id == CostRecord.agent_run_id)
        .where(CostRecord.product_id == product_id, CostRecord.category == "llm")
        .order_by(CostRecord.incurred_at.desc(), CostRecord.id.desc())
        .limit(limit)
    ))
    return {"product_id": str(product_id), "scanned_limit": limit,
            **summarize_usage_rows(rows)}
