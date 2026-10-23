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
.ins-facts li{margin:3px 0}.ins-draft{margin-top:8px}.ins-draft p{margin:0 0 6px}.ins-draft ul{margin:0 0 8px;padding-left:20px}.ins-muted{opacity:.75;font-size:14px}
</style>"""


STATUS_LABELS = {TaskStatus.QUEUED: "ждёт специалиста", TaskStatus.RUNNING: "специалист работает",
                 TaskStatus.COMPLETED: "черновик готов", TaskStatus.FAILED: "не получилось",
                 TaskStatus.WAITING_APPROVAL: "ждёт вашего согласия", TaskStatus.CANCELLED: "отменена"}
AGENT_LABELS = {"research_pmm": "Исследователь", "content_seo": "Контент и SEO", "distribution": "Распространение",
                "analytics": "Аналитик", "content_strategist": "Контент-стратег", "seo_editor": "SEO-редактор",
                "scriptwriter": "Сценарист", "community_manager": "Сообщество", "cro": "Конверсия"}
SPECIALIST_KINDS = ("research_report", "measurement_spec", "article_brief", "video_brief", "content_plan",
                    "distribution_plan")


def _list(title: str, items) -> str:
    items = [str(i) for i in items or [] if str(i).strip()]
    return (f"<p><b>{safe(title)}</b></p><ul>{''.join(f'<li>{safe(i)}</li>' for i in items)}</ul>") if items else ""


def _line(title: str, value) -> str:
    return f"<p><b>{safe(title)}:</b> {safe(str(value))}</p>" if value else ""


def specialist_result_html(kind: str, data: dict) -> str:
    """A specialist's draft in owner words. Unknown shapes show only the summary; nothing is invented."""
    out = _line("Коротко", data.get("summary"))
    if kind == "research_report":
        for c in data.get("candidates") or []:
            if isinstance(c, dict):
                out += (_line("Идея", c.get("segment_hypothesis")) + _line("Где", c.get("channel"))
                        + _line("Что проверить", c.get("proposed_test")))
        out += _line("С чего начать", data.get("recommended_test")) + _list("Чего не хватает", data.get("evidence_gaps"))
    elif kind == "distribution_plan":
        for c in data.get("candidates") or []:
            if isinstance(c, dict):
                out += _line("Канал", c.get("channel")) + _line("Что сделать", c.get("proposed_action"))
        out += _line("Первый шаг", data.get("recommended_first_test"))
        out += _list("Потребует вашего согласия", data.get("approval_required_actions"))
    elif kind == "measurement_spec":
        out += _line("Кто считается заявкой", data.get("qualified_lead_definition"))
        out += _line("Как считать", data.get("attribution_notes")) + _list("Чего не хватает", data.get("missing_evidence"))
    else:  # article_brief, video_brief, content_plan
        out += (_line("Для кого", data.get("audience_hypothesis")) + _line("Идея", data.get("angle"))
                + _list("Варианты названия", data.get("title_options")) + _list("План", data.get("outline"))
                + _line("Призыв", data.get("cta")) + _list("Чем подтвердить", data.get("proof_needed")))
    return out


def _task_items(db: Session, product_id: UUID, tasks: list[Task]) -> str:
    results = {}
    for artifact in db.scalars(select(Artifact).where(
            Artifact.product_id == product_id, Artifact.task_id.in_([t.id for t in tasks]),
            Artifact.kind.in_(SPECIALIST_KINDS)).order_by(Artifact.created_at, Artifact.id)):
        results[artifact.task_id] = artifact
    items = []
    for task in tasks:
        who = AGENT_LABELS.get(task.assigned_agent or "", "")
        head = (f"<b>{safe(task.title)}</b><br><span class=\"ins-muted\">"
                f"{safe(who + ' · ' if who else '')}{safe(STATUS_LABELS.get(task.status, ''))}</span>")
        body = ""
        artifact = results.get(task.id)
        if artifact is not None:
            try:
                body = f'<div class="ins-draft">{specialist_result_html(artifact.kind, json.loads(artifact.content))}</div>'
            except ValueError:
                body = ""
        items.append(f'<div class="ins-item">{head}{body}</div>')
    return "".join(items)


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
    proposed = _task_items(db, product_id, tasks) if tasks else ""
    return (STYLE
            + '<section class="panel"><div class="panel-head"><h2>Что увидела команда</h2></div>'
            + f'<div class="ins-body"><p>{safe(data.get("summary") or "Итог без пояснения.")}</p></div></section>'
            + '<section class="panel"><div class="panel-head"><h2>Решения</h2></div>'
            + f'<div class="ins-body">{"".join(items) or "<p>Решений нет.</p>"}</div></section>'
            + ('<section class="panel"><div class="panel-head"><h2>Предложенные задачи</h2></div>'
               f'<div class="ins-body">{proposed}<p class="ins-muted">Специалисты готовят только черновики: ничего не публикуется, '
               'не отправляется и не запускается без вашего согласия.</p></div>'
               '</section>' if proposed else '')
            + f'<p><a href="{base}">← К диспетчеру</a></p>')
