"""Bounded evidence-driven investigation; never executes mutations or approvals."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.dispatcher.blocks import clean_blocks
from app.dispatcher.decision import PlannedStep
from app.dispatcher.outcome import Context, Outcome
from app.dispatcher.policy import Cost, Verdict, verdict
from app.dispatcher.registry import CAPABILITIES
from app.tools import ToolOperation

# Individually audited owner views: no CRM rows, material bodies, preferences,
# navigation, paid tools, or writes. Never infer read-only from a model label.
READS = frozenset({"list_tasks", "show_task", "show_work", "show_recent_videos", "list_publications"})
MAX_READS = 3


class NextRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    next_step: PlannedStep | None = None
    conclusion: str = Field(default="", max_length=600)


def read_params(step: PlannedStep):
    from app.dispatcher import executors
    cap = CAPABILITIES.get(step.capability)
    if (cap is None or cap.id not in READS or cap.operation != ToolOperation.ANALYTICS
            or cap.cost != Cost.NONE or cap.always_confirm or cap.id not in executors.EXECUTORS
            or verdict(cap.operation, cap.cost) != Verdict.RUN):
        raise ValueError("Недоступный инструмент диагностики")
    return cap.params.model_validate(step.params)


def recent_task_context(db, product_id) -> list[dict]:
    # Only task views, not the full snapshot (which also loads plan/material data).
    from app.dispatcher.readers import work_rows
    return [{"task_id": str(row["id"]), "title": str(row["title"])[:140],
             "status": str(row["state"])} for row in work_rows(db, product_id)[:15]]


def evidence(result: Outcome, capability: str, params: dict) -> dict:
    # Do not send links, buttons, full material text or other executable data.
    tables = []
    resources = []
    for block in clean_blocks(result.blocks):
        if block["type"] == "table" and len(tables) < 2:
            tables.append({"columns": [v[:80] for v in block["columns"][:6]],
                           "rows": [[v[:180] for v in row[:6]] for row in block["rows"][:8]],
                           "truncated": len(block["rows"]) > 8})
        elif block["type"] == "resource_list":
            resources.extend({"title": item["title"][:140], "status": item["status"][:80],
                              "detail": item.get("detail", "")[:180]}
                             for item in block["items"][:8])
    return {"capability": capability, "params": params, "ok": result.ok, "no_data": result.no_data,
            "text": result.text[:600], "tables": tables, "resources": resources[:8],
            "tasks": result.diagnostic_tasks[:8]}


async def run_investigation(ctx: Context, first: PlannedStep, *, act: Callable,
                            next_read: Callable[[list[dict]], Awaitable[NextRead | None]],
                            deadline: float | None = None) -> Outcome:
    step = first
    seen = set()
    results = []
    observations = []
    conclusion = ""
    incomplete = False
    for index in range(MAX_READS):
        if deadline is not None and time.monotonic() >= deadline:
            incomplete = True
            break
        try:
            params = read_params(step)
            signature = (step.capability, json.dumps(params.model_dump(mode="json"), sort_keys=True))
            if signature in seen:
                raise ValueError("Повторный просмотр")
            seen.add(signature)
        except (ValueError, ValidationError):
            incomplete = True
            break
        try:
            operation = act(ctx, step.capability, params.model_dump(exclude_none=True))
            if deadline is None:
                result, confirmation, _ = await operation
            else:
                result, confirmation, _ = await asyncio.wait_for(
                    operation, timeout=max(0, deadline - time.monotonic()))
            if confirmation is not None or result.confirmation is not None or result.task_id is not None:
                result = Outcome("Инструмент вернул неподходящий результат диагностики.", ok=False)
        except Exception:
            ctx.db.rollback()
            logging.getLogger(__name__).warning("dispatcher_investigation_read_failed", exc_info=True)
            result = Outcome("Не удалось получить данные этого раздела.", ok=False)
        observations.append(evidence(result, step.capability, params.model_dump(mode="json")))
        results.append({"title": CAPABILITIES[step.capability].title,
                        "status": "failed" if not result.ok else "no_data" if result.no_data else "completed",
                        "detail": result.text[:600],
                        "blocks": [b for b in clean_blocks(result.blocks)
                                   if b["type"] in {"text", "table", "links", "chips", "resource_list"}][:8]})
        # One model call per observation, including final synthesis. After the
        # third read, the model may conclude but cannot request another tool.
        try:
            if deadline is None:
                follow = await next_read(observations)
            else:
                remaining = deadline - time.monotonic()
                follow = (await asyncio.wait_for(next_read(observations), timeout=remaining)
                          if remaining > 0 else None)
        except Exception:
            ctx.db.rollback()
            logging.getLogger(__name__).warning("dispatcher_investigation_continuation_failed", exc_info=True)
            follow = None
        if follow is None:
            incomplete = True
            break
        if follow.next_step is None:
            conclusion = follow.conclusion.strip()
            incomplete = not bool(conclusion)
            break
        if index + 1 >= MAX_READS:
            incomplete = True
            break
        step = follow.next_step
    if not results:
        return Outcome("Не удалось подобрать разрешённый инструмент диагностики. Уточните, что проверить.", ok=False)
    completed = sum(r["status"] != "failed" for r in results)
    summary = f"Проверено разделов: {completed} из {len(results)}."
    if incomplete or completed != len(results):
        summary += " Анализ не завершён; показываю полученные данные."
    if len(results) == 1:
        blocks = [{"type": "text", "text": results[0]["detail"]}, *results[0]["blocks"]]
    else:
        blocks = [{"type": "workflow", "title": "Проверка по данным платформы", "steps": results}]
    if conclusion and completed and not incomplete:
        prefix = ("Вывод ИИ по показанным данным: " if completed == len(results)
                  else "Предварительный вывод ИИ по частичным данным: ")
        blocks.append({"type": "text", "text": prefix + conclusion})
    return Outcome(summary, blocks=blocks, ok=completed == len(results) and not incomplete)
