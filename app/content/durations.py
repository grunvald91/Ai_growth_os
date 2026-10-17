"""Фактическая скорость ИИ-вызовов и ожидаемая длительность заданий.

Источник — только записи agent_runs (started_at/finished_at). Ничего не
выдумываем: если истории нет, показываем честный ориентир «по опыту».
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import AgentRun, Task, TaskStatus

# Тип задания → (название, ориентир в минутах без истории: обычно, максимум).
KINDS = {
    "whiteboard": ("Рисованное видео", (25, 60)),
    "package": ("Пакет контента со статьёй", (20, 60)),
    "post": ("Один пост", (5, 15)),
    "creative": ("Сценарий / визуал", (5, 20)),
    "plan": ("Контент-план", (5, 20)),
}
_AGENT_KIND = {"whiteboard_renderer": "whiteboard", "content_creative": "creative",
               "editorial_planner": "plan", "content_factory": "package"}


def _percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def call_speed(db: Session, *, days: int = 7, now: datetime | None = None) -> list[dict]:
    """Секунды на вызов по провайдеру и модели: число, ошибки, p50/p90/max."""
    since = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    rows = db.execute(select(AgentRun.provider, AgentRun.model, AgentRun.status,
                             AgentRun.started_at, AgentRun.finished_at)
                      .where(AgentRun.started_at >= since, AgentRun.finished_at.is_not(None)))
    groups: dict[tuple[str, str], dict] = defaultdict(lambda: {"seconds": [], "failed": 0})
    for provider, model, status, started, finished in rows:
        group = groups[(provider or "—", model or "—")]
        group["seconds"].append(max(0.0, (_aware(finished) - _aware(started)).total_seconds()))
        if status == "failed":
            group["failed"] += 1
    result = [{"provider": provider, "model": model, "calls": len(g["seconds"]), "failed": g["failed"],
               "p50": _percentile(g["seconds"], 0.5), "p90": _percentile(g["seconds"], 0.9),
               "max": max(g["seconds"])}
              for (provider, model), g in groups.items()]
    return sorted(result, key=lambda item: -item["calls"])


def task_durations(db: Session, *, days: int = 30, now: datetime | None = None) -> dict[str, dict]:
    """Минуты от первого до последнего ИИ-вызова у завершённых заданий по типам."""
    since = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    rows = db.execute(select(Task.id, Task.assigned_agent, AgentRun.agent_name,
                             AgentRun.started_at, AgentRun.finished_at)
                      .join(AgentRun, AgentRun.task_id == Task.id)
                      .where(Task.status == TaskStatus.COMPLETED, Task.created_at >= since,
                             Task.assigned_agent.in_(_AGENT_KIND), AgentRun.started_at.is_not(None),
                             AgentRun.finished_at.is_not(None)))
    spans: dict = {}
    for task_id, agent, run_name, started, finished in rows:
        span = spans.setdefault(task_id, {"kind": _AGENT_KIND[agent], "start": _aware(started),
                                          "end": _aware(finished)})
        span["start"] = min(span["start"], _aware(started))
        span["end"] = max(span["end"], _aware(finished))
        if run_name == "post_strategist":
            span["kind"] = "post"
    minutes: dict[str, list[float]] = defaultdict(list)
    for span in spans.values():
        minutes[span["kind"]].append((span["end"] - span["start"]).total_seconds() / 60)
    return {kind: {"count": len(values), "median": median(values), "p90": _percentile(values, 0.9)}
            for kind, values in minutes.items()}


def _minutes(value: float) -> str:
    value = max(1, round(value))
    if value < 60:
        return f"{value} мин"
    hours, rest = divmod(value, 60)
    return f"{hours} ч" + (f" {rest} мин" if rest else "")


def duration_estimate(stats: dict[str, dict], kind: str) -> str:
    """Ожидаемая длительность задания, посчитанная по истории."""
    name, (usual, longest) = KINDS[kind]
    item = stats.get(kind)
    if item and item["count"] >= 3:
        text = (f"{name}: обычно готово за {_minutes(item['median'])}, иногда до {_minutes(item['p90'])} "
                f"(по {item['count']} заданиям за 30 дней).")
    else:
        text = f"{name}: обычно около {_minutes(usual)}, иногда до {_minutes(longest)} — истории пока мало."
    return text


def duration_notice(stats: dict[str, dict], kind: str) -> str:
    """Предупреждение для формы создания."""
    return duration_estimate(stats, kind) + " ИИ-модели отвечают небыстро; страницу можно закрыть, задание продолжится."
