"""Read-only, tenant-safe SQL/HTML timings for the Studio Tasks page.

Production invocation inside the API container:
    python -m app.ops.studio_tasks_perf --samples 3

No credentials, company names, IDs, SQL text, or task content are printed.
This is a representative SERVER-SIDE probe, not a substitute for authenticated
browser navigation timing (network, TTFB, layout and paint).
"""
from __future__ import annotations

import argparse
import json
import math
from time import perf_counter
from typing import Any

from sqlalchemy import event, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.api.content_studio_activity import WORK_AGENTS, activity_html, work_page
from app.models.domain import Task


def _percentile(values: list[float], level: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * level) - 1))], 2)


def _sample_once(connection: Connection, product_id, *, tab: str, query: str) -> dict[str, float | int]:
    """Observe one request-equivalent DB connection without logging SQL/data."""
    counters = {"count": 0, "sql_ms": 0.0}
    starts: dict[int, float] = {}

    def started(_conn, _cursor, _statement, _parameters, context, _many):
        starts[id(context)] = perf_counter()

    def finished(_conn, _cursor, _statement, _parameters, context, _many):
        began = starts.pop(id(context), None)
        if began is not None:
            counters["count"] += 1
            counters["sql_ms"] += (perf_counter() - began) * 1000

    event.listen(connection, "before_cursor_execute", started)
    event.listen(connection, "after_cursor_execute", finished)
    try:
        with Session(bind=connection, autoflush=False) as db:
            began = perf_counter()
            result = work_page(db, product_id, tab=tab, query=query)
            db_ms = (perf_counter() - began) * 1000
            rendering = perf_counter()
            html = activity_html(
                product_id, result["items"], tab=result["tab"], q=result["q"],
                period=result["period"], kind=result["kind"], page=result["page"],
                total=result["total"], page_size=result["page_size"],
                counts=result["counts"],
            )
            render_ms = (perf_counter() - rendering) * 1000
            if db.new or db.dirty or db.deleted:
                raise RuntimeError("read_only_invariant_violation")
            return {
                "total_ms": db_ms + render_ms,
                "db_ms": db_ms,
                "sql_ms": counters["sql_ms"],
                "render_ms": render_ms,
                "sql_calls": counters["count"],
                "html_bytes": len(html.encode("utf-8")),
            }
    finally:
        event.remove(connection, "before_cursor_execute", started)
        event.remove(connection, "after_cursor_execute", finished)
        connection.rollback()


def probe(connection: Connection, samples: int = 3) -> dict[str, Any]:
    """Pick one representative tenant without disclosing its identity."""
    if not isinstance(samples, int) or not 1 <= samples <= 7:
        raise ValueError("invalid_sample_count")
    # Read-only GROUP BY; do not print or export company IDs/labels.
    selected = connection.execute(
        select(Task.product_id)
        .where(Task.assigned_agent.in_(WORK_AGENTS))
        .group_by(Task.product_id)
        .order_by(func.count(Task.id).desc(), Task.product_id)
        .limit(1)
    ).scalar_one_or_none()
    connection.rollback()
    if selected is None:
        return {"schema": 1, "status": "no_tasks", "read_only": True}
    # One warm-up per scenario, followed by several bounded measurements.
    scenarios = (
        ("all", "all", ""),
        ("approval", "approval", ""),
        ("search_no_match", "all", "__servgo_perf_probe_never_match_42__"),
    )
    report: dict[str, Any] = {"schema": 1, "status": "ok", "read_only": True, "scenarios": {}}
    for label, tab, query in scenarios:
        _sample_once(connection, selected, tab=tab, query=query)
        readings = [
            _sample_once(connection, selected, tab=tab, query=query)
            for _ in range(samples)
        ]
        report["scenarios"][label] = {
            "samples": samples,
            "total_p50_ms": _percentile([r["total_ms"] for r in readings], 0.50),
            "total_p95_ms": _percentile([r["total_ms"] for r in readings], 0.95),
            "database_p95_ms": _percentile([r["db_ms"] for r in readings], 0.95),
            "sql_p95_ms": _percentile([r["sql_ms"] for r in readings], 0.95),
            "render_p95_ms": _percentile([r["render_ms"] for r in readings], 0.95),
            "sql_calls_max": max(int(r["sql_calls"]) for r in readings),
            "html_bytes_max": max(int(r["html_bytes"]) for r in readings),
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only server-side Studio Tasks performance probe")
    parser.add_argument("--samples", type=int, default=3)
    args = parser.parse_args()
    try:
        from app.db.session import engine
        with engine.connect() as connection:
            result = probe(connection, samples=args.samples)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except Exception as exc:  # Never print exception details in public CI logs.
        print(json.dumps({"schema": 1, "status": "error",
                          "error_type": type(exc).__name__, "read_only": True}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
