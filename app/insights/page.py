"""The page of one business review: what the team decided and which facts each decision rests on.

Opened from the dispatcher card and from «В работе». Everything shown comes from stored rows: the review summary,
the decisions with their evidence (metric label, value, period) and the tasks the team proposed.
"""
from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.content_studio_activity import safe
from app.insights.worker import RESULT_KIND
from app.models.domain import Artifact, Decision, Metric, Task, TaskStatus

DECISION_LABELS = {"KEEP": "Оставить", "STOP": "Остановить", "SCALE": "Усилить", "TEST": "Проверить"}
STYLE = """<style>
.ins-body{padding:0 16px 16px}.ins-body p{margin:0 0 10px;line-height:1.5;white-space:pre-wrap}
.ins-item{border-top:1px solid var(--sg-line,#E6E9EF);padding:12px 0}.ins-item:first-child{border-top:0}
.ins-tag{display:inline-block;font-weight:600;margin-bottom:6px}
.ins-facts{margin:8px 0 0;padding:0;list-style:none;font-size:14px;opacity:.85}
.ins-facts li{margin:3px 0}.ins-muted{opacity:.75;font-size:14px}
</style>"""


def _ids(raw) -> list[UUID]:
    found = []
    for item in raw or []:
        try:
            found.append(UUID(str(item)))
        except ValueError:
            continue
    return found


def _value(metric: Metric) -> str:
    number = f"{Decimal(str(metric.value)).normalize():f}" if metric.value is not None else "—"
    return f"{number} {metric.unit}" if metric.unit else number


def review_html(db: Session, product_id: UUID, task: Task) -> str:
    base = f"/studio/products/{product_id}"
    result = db.scalar(select(Artifact).where(Artifact.task_id == task.id, Artifact.product_id == product_id,
                                              Artifact.kind == RESULT_KIND).order_by(Artifact.created_at.desc()).limit(1))
    if result is None:
        state = {TaskStatus.QUEUED: "Ждёт очереди: команда скоро начнёт разбор.",
                 TaskStatus.RUNNING: "Команда разбирает цифры. Обычно это занимает несколько минут.",
                 TaskStatus.FAILED: "Разбор не получился. Попросите диспетчера передать данные команде ещё раз."
                 }.get(task.status, "Результата пока нет.")
        return (STYLE + '<section class="panel"><div class="panel-head"><h2>Разбор данных бизнеса</h2></div>'
                f'<div class="ins-body"><p>{safe(state)}</p><p class="ins-muted">{safe(task.description or "")}</p>'
                f'<p><a href="{base}">← К диспетчеру</a></p></div></section>')
    try:
        data = json.loads(result.content)
    except ValueError:
        data = {}
    ids = _ids(data.get("decision_ids"))
    decisions = list(db.scalars(select(Decision).where(Decision.product_id == product_id, Decision.id.in_(ids)))) \
        if ids else []
    decisions.sort(key=lambda row: ids.index(row.id))
    metric_ids = _ids(ref.get("id") for d in decisions for ref in (d.evidence_references or [])
                      if isinstance(ref, dict) and ref.get("kind") == "metric")
    metrics = {m.id: m for m in db.scalars(select(Metric).where(Metric.product_id == product_id,
                                                                  Metric.id.in_(metric_ids)))} if metric_ids else {}
    items = []
    for decision in decisions:
        facts = []
        for ref in decision.evidence_references or []:
            if not isinstance(ref, dict):
                continue
            metric = metrics.get(next(iter(_ids([ref.get("id")])), None)) if ref.get("kind") == "metric" else None
            if metric is not None:
                label = (metric.dimensions or {}).get("label") or metric.name
                facts.append(f"<li>{safe(label)}: <b>{safe(_value(metric))}</b></li>")
            else:
                facts.append(f"<li>{safe(ref.get('kind', 'запись'))}</li>")
        evidence = (f'<ul class="ins-facts">{"".join(facts)}</ul>' if facts
                    else '<p class="ins-muted">Без опоры на цифры.</p>')
        kind = decision.decision_type.value
        items.append(f'<div class="ins-item"><span class="ins-tag">{safe(DECISION_LABELS.get(kind, kind))}</span>'
                     f'<p>{safe(decision.rationale)}</p>{evidence}</div>')
    tasks = list(db.scalars(select(Task).where(Task.product_id == product_id,
                                               Task.id.in_(_ids(data.get("task_ids")))))) if data.get("task_ids") else []
    proposed = "".join(f"<li>{safe(t.title)}</li>" for t in tasks)
    return (STYLE
            + '<section class="panel"><div class="panel-head"><h2>Что увидела команда</h2></div>'
            + f'<div class="ins-body"><p>{safe(data.get("summary") or "Итог без пояснения.")}</p></div></section>'
            + '<section class="panel"><div class="panel-head"><h2>Решения</h2></div>'
            + f'<div class="ins-body">{"".join(items) or "<p>Решений нет.</p>"}</div></section>'
            + ('<section class="panel"><div class="panel-head"><h2>Предложенные задачи</h2></div>'
               f'<div class="ins-body"><ul>{proposed}</ul><p class="ins-muted">Автоматически они пока не выполняются.</p></div>'
               '</section>' if proposed else '')
            + f'<p><a href="{base}">← К диспетчеру</a></p>')
