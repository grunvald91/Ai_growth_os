"""Прозрачность процесса в Growth Studio: сколько агентов работало над заданием,
что сказал критик и что происходило шаг за шагом, строка за строкой.

Только чтение. Источники правды, которые уже пишут пайплайны:
  * AgentRun       — шаги агентов, время начала и конца, ошибка;
  * CostRecord     — каждое обращение к модели (автор / критик / правка) с токенами;
  * Artifact kind="agent_quality_review" — замечания критика и решения автора;
  * результаты задания (статьи, посты, сценарии).
Ничего не придумывается: нет данных — нет строки. Если критик выключен
настройкой сервера, так и сказано, а не «замечаний нет».
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.content_studio_activity import PIPELINE_ORDER, RESULT_KINDS, STAGE_NAMES, WORK_AGENTS
from app.models.domain import AgentRun, Artifact, CostRecord, Task

MSK = timezone(timedelta(hours=3))  # Москва и Сочи: без перехода на летнее время

AGENT_TITLES = {
    "content_strategist": "Стратег",
    "seo_editor": "SEO-редактор",
    "article_writer": "Автор статьи",
    "conversion_editor": "Редактор призыва",
    "human_editor": "Редактор для читателя",
    "marketing_editor": "Маркетинговый редактор",
    "repurposing_editor": "Редактор версий",
    "video_director": "Режиссёр видео",
    "story_director": "Режиссёр Stories",
    "visual_designer": "Дизайнер изображений",
    "creative_strategist": "Стратег",
    "creative_director": "Креативный директор",
    "post_strategist": "Стратег поста",
    "post_writer_editor": "Автор и редактор поста",
    "researcher": "Исследователь",
    "marketing_strategist": "Маркетолог",
    "platform_writer": "Автор версий для площадок",
    "post_rewriter": "Автор правок",
    "youtube_shorts_director": "Режиссёр Shorts",
    "instagram_reels_director": "Режиссёр Reels",
}

ATTEMPT_FAILED = {
    "provider_error": "ошибка у поставщика модели",
    "provider_timeout": "модель не успела ответить",
    "provider_cancelled": "обращение прервано",
    "fallback_error": "запасная модель тоже не ответила",
}
SKIP_REASONS = {
    "skipped_ineligible": "для этого шага проверка не предусмотрена",
    "skipped_incomplete_original": "ответ автора был оборван",
    "skipped_incomplete_context": "слишком много данных для проверки",
    "skipped_size_limit": "текст слишком длинный для проверки",
    "skipped_invalid_original": "ответ автора не прошёл формальную проверку",
}
SEVERITY = {"critical": "Критично", "major": "Важно", "minor": "Мелочь"}
CALL_STAGE = {"author": "автор", "critic": "критик", "revision": "правки"}


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _pl(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 100 in range(11, 15):
        return many
    if n % 10 == 1:
        return one
    if n % 10 in (2, 3, 4):
        return few
    return many


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _clock(value: datetime) -> str:
    return _utc(value).astimezone(MSK).strftime("%H:%M:%S")


def _span(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} с"
    if seconds < 600:
        return f"{seconds // 60} мин" + (f" {seconds % 60:02d} с" if seconds % 60 else "")
    if seconds < 3600:
        return f"{round(seconds / 60)} мин"
    return f"{seconds // 3600} ч {round(seconds % 3600 / 60)} мин"


def _num(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


def _text(value, limit: int = 600) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def agent_title(name: str) -> str:
    return AGENT_TITLES.get(name, "Помощник")


# ── загрузка ───────────────────────────────────────────────────────────────
@dataclass
class Process:
    critic_enabled: bool = False
    calls: dict = field(default_factory=dict)      # run_id -> [{at, stage, model, tin, tout}]
    reviews: dict = field(default_factory=dict)    # run_id -> [review, ...] по времени
    attempts: dict = field(default_factory=dict)   # run_id -> [{at, status}]
    results: list = field(default_factory=list)    # [(at, title)]

    def last_review(self, run_id):
        rows = self.reviews.get(run_id) or []
        return rows[-1] if rows else None


def _loads(raw):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _clean_findings(raw) -> list[dict]:
    rows = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        rows.append({
            "id": _text(item.get("id"), 20),
            "severity": item.get("severity") if item.get("severity") in SEVERITY else "major",
            "problem": _text(item.get("problem"), 420),
            "suggestion": _text(item.get("suggestion") or item.get("proposed_fix"), 420),
        })
    return rows


def _clean_decisions(raw) -> list[dict]:
    rows = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and item.get("decision") in ("accept", "reject"):
            rows.append({"id": _text(item.get("id") or item.get("finding_id"), 20),
                         "decision": item["decision"], "why": _text(item.get("rationale"), 300)})
    return rows


def load_process(db: Session, task: Task, runs: list[AgentRun], results: list[Artifact],
                 critic_enabled: bool) -> Process:
    proc = Process(critic_enabled=bool(critic_enabled))
    ids = [run.id for run in runs]
    if ids:
        for rec in db.scalars(select(CostRecord).where(
                CostRecord.agent_run_id.in_(ids), CostRecord.category == "llm",
        ).order_by(CostRecord.incurred_at, CostRecord.created_at)):
            details = rec.details if isinstance(rec.details, dict) else {}
            proc.calls.setdefault(rec.agent_run_id, []).append({
                "at": _utc(rec.incurred_at),
                "stage": details.get("llm_stage") if details.get("llm_stage") in CALL_STAGE else "author",
                "model": _text(details.get("model") or rec.provider, 80),
                "tin": rec.input_tokens, "tout": rec.output_tokens,
            })
    for art in db.scalars(select(Artifact).where(
            Artifact.task_id == task.id, Artifact.kind == "agent_quality_review",
    ).order_by(Artifact.created_at, Artifact.id)):
        data = _loads(art.content)
        if not isinstance(data, dict) or art.agent_run_id is None:
            continue
        status = str(data.get("status") or "")
        if status in ATTEMPT_FAILED:
            proc.attempts.setdefault(art.agent_run_id, []).append(
                {"at": _utc(art.created_at), "status": status})
            continue
        proc.reviews.setdefault(art.agent_run_id, []).append({
            "at": _utc(art.created_at), "status": status,
            "findings": _clean_findings(data.get("findings")),
            "decisions": _clean_decisions(data.get("decisions")),
        })
    proc.results = [(_utc(row.created_at), _text(row.title, 120)) for row in results if row.created_at]
    return proc


# ── разбор замечаний критика ───────────────────────────────────────────────
def _serious(review: dict) -> list[dict]:
    return [f for f in review["findings"] if f["severity"] in ("critical", "major")]


def _tally(review: dict) -> tuple[int, int, int]:
    accepted = sum(1 for d in review["decisions"] if d["decision"] == "accept")
    rejected = sum(1 for d in review["decisions"] if d["decision"] == "reject")
    return len(_serious(review)), accepted, rejected


def review_headline(review: dict) -> tuple[str, str]:
    """(тон, фраза) — что сделал критик на этом шаге."""
    status = review["status"]
    serious, accepted, rejected = _tally(review)
    minor = len(review["findings"]) - len(_serious(review))
    found = f"Нашёл {serious} {_pl(serious, 'замечание', 'замечания', 'замечаний')}"
    if status == "no_material_issues":
        extra = f", мелких: {minor}" if minor else ""
        return "ok", "Серьёзных замечаний нет" + extra
    if status == "revised":
        return "warn", f"{found}. Автор принял {accepted}, отклонил {rejected}. Текст обновлён"
    if status == "all_rejected":
        return "ok", f"{found}. Автор отклонил все, текст остался прежним"
    if status == "advisory_only_no_domain_validator":
        return "warn", f"{found}. Это совет: текст не менялся"
    if status == "rejected_domain_revision":
        return "warn", f"{found}. Правки автора не прошли проверку, оставлена прежняя версия"
    if status == "revision_pending":
        return "run", f"{found}. Автор вносит правки"
    if status in ("revision_cancelled", "revision_unavailable", "review_unavailable"):
        tail = f" Нашёл {serious}." if serious else ""
        return "err", "Проверка не завершилась." + tail
    if status.startswith("skipped"):
        return "mute", "Проверка пропущена: " + SKIP_REASONS.get(status, "причина не указана")
    return "mute", "Проверка записана, статус не распознан"


def critic_block_html(review: dict) -> str:
    tone, headline = review_headline(review)
    decisions = {d["id"]: d for d in review["decisions"]}
    rows = []
    for f in _serious(review):
        d = decisions.get(f["id"])
        verdict = ""
        if d:
            word = "Автор принял" if d["decision"] == "accept" else "Автор отклонил"
            verdict = (f'<p class="sg-find-verdict {"yes" if d["decision"] == "accept" else "no"}">'
                       f'<b>{word}.</b> {_e(d["why"])}</p>')
        rows.append(
            f'<li class="sg-find"><span class="sg-sev sev-{f["severity"]}">{SEVERITY[f["severity"]]}</span>'
            f'<p>{_e(f["problem"])}</p>'
            + (f'<p class="sg-find-fix"><b>Предложено:</b> {_e(f["suggestion"])}</p>' if f["suggestion"] else "")
            + verdict + '</li>'
        )
    minors = [f for f in review["findings"] if f["severity"] == "minor"]
    for f in minors:
        rows.append(f'<li class="sg-find"><span class="sg-sev sev-minor">{SEVERITY["minor"]}</span>'
                    f'<p>{_e(f["problem"])}</p></li>')
    detail = f'<ul class="sg-find-list">{"".join(rows)}</ul>' if rows else ""
    return (f'<div class="sg-critic {tone}"><div class="sg-critic-head"><b>Критик</b>'
            f'<span>{_e(headline)}</span></div>{detail}</div>')


# ── один шаг: модель, токены, критик ───────────────────────────────────────
def step_meta_html(run: AgentRun, proc: Process) -> str:
    calls = proc.calls.get(run.id) or []
    meta = []
    if calls:
        meta.append(f"{len(calls)} {_pl(len(calls), 'обращение', 'обращения', 'обращений')} к модели")
        model = next((c["model"] for c in calls if c["model"]), "") or (run.model or "")
        if model:
            meta.append(_e(model))
        tokens = sum((c["tin"] or 0) + (c["tout"] or 0) for c in calls)
        if tokens:
            meta.append(f"{_num(tokens)}\u00a0токенов")
    elif run.model:
        meta.append(_e(run.model))
    out = f'<p class="sg-step-meta">{" · ".join(meta)}</p>' if meta else ""
    for attempt in proc.attempts.get(run.id) or []:
        out += (f'<p class="sg-step-meta warn">Обращение к модели не удалось: '
                f'{_e(ATTEMPT_FAILED[attempt["status"]])}</p>')
    return out


def step_critic_html(run: AgentRun, proc: Process) -> str:
    review = proc.last_review(run.id)
    if review:
        return critic_block_html(review)
    if proc.critic_enabled and run.status == "completed":
        return '<p class="sg-step-meta">Критик: на этом шаге не запускался.</p>'
    return ""


# ── сводка ─────────────────────────────────────────────────────────────────
def _timing(runs: list[AgentRun]) -> tuple[float | None, bool]:
    starts = [_utc(r.started_at) for r in runs if r.started_at]
    if not starts:
        return None, False
    live = any(r.status == "running" for r in runs)
    ends = [_utc(r.finished_at) for r in runs if r.finished_at]
    end = datetime.now(timezone.utc) if live or not ends else max(ends)
    return (end - min(starts)).total_seconds(), live


def _ordered(runs: list[AgentRun]) -> list[AgentRun]:
    return sorted(runs, key=lambda r: (r.started_at is None,
                  _utc(r.started_at).timestamp() if r.started_at else
                  (PIPELINE_ORDER.index(r.agent_name) if r.agent_name in PIPELINE_ORDER else 99)))


def summary_html(runs: list[AgentRun], proc: Process, map_href: str = "") -> str:
    if not runs:
        return ""
    names = []
    for run in _ordered(runs):
        if run.agent_name not in names:
            names.append(run.agent_name)
    done = sum(r.status == "completed" for r in runs)
    live = sum(r.status == "running" for r in runs)
    failed = sum(r.status == "failed" for r in runs)
    waiting = len(runs) - done - live - failed
    steps = [f"готово {done}"]
    if live:
        steps.append(f"работает {live}")
    if waiting:
        steps.append(f"ждут {waiting}")
    if failed:
        steps.append(f"ошибок {failed}")
    seconds, running = _timing(runs)
    all_calls = [c for rows in proc.calls.values() for c in rows]
    by_stage = {k: sum(1 for c in all_calls if c["stage"] == k) for k in CALL_STAGE}
    tiles = [
        f'<div class="sg-tile"><b>{len(names)}</b><span>{_pl(len(names), "агент", "агента", "агентов")} в задании</span>'
        f'<small>{len(runs)} {_pl(len(runs), "шаг", "шага", "шагов")}: {", ".join(steps)}</small></div>',
        '<div class="sg-tile"><b>' + (_span(seconds) if seconds is not None else "—") + '</b><span>'
        + ("идёт сейчас" if running else "от начала до конца" if seconds is not None else "ещё не начато")
        + '</span><small>Агенты передают результат друг другу по цепочке</small></div>',
    ]
    if all_calls:
        parts = [f"{CALL_STAGE[k]} {v}" for k, v in by_stage.items() if v]
        tokens = sum((c["tin"] or 0) + (c["tout"] or 0) for c in all_calls)
        tiles.append(
            f'<div class="sg-tile"><b>{len(all_calls)}</b><span>{_pl(len(all_calls), "обращение", "обращения", "обращений")} к модели</span>'
            f'<small>{", ".join(parts)}' + (f" · {_num(tokens)} токенов" if tokens else "") + '</small></div>')
    reviews = [proc.last_review(r.id) for r in runs if proc.last_review(r.id)]
    done_reviews = [x for x in reviews if not x["status"].startswith("skipped")]
    if reviews or proc.critic_enabled:
        found = sum(_tally(x)[0] for x in done_reviews)
        accepted = sum(_tally(x)[1] for x in done_reviews)
        rejected = sum(_tally(x)[2] for x in done_reviews)
        tiles.append(
            f'<div class="sg-tile"><b>{len(done_reviews)}</b><span>{_pl(len(done_reviews), "проверка", "проверки", "проверок")} критиком</span>'
            f'<small>Замечаний: {found}' + (f" · принято {accepted}, отклонено {rejected}" if found else "") + '</small></div>')
    else:
        tiles.append('<div class="sg-tile crit-off"><b>Выключен</b><span>Критик</span>'
                     '<small>Второй ИИ, который ищет слабые места в тексте</small></div>')
    state_class = {"completed": "done", "running": "run", "failed": "err"}
    team = "".join(
        f'<li class="{state_class.get(next((r.status for r in _ordered(runs) if r.agent_name == n and r.status != "completed"), "completed"), "")}">{_e(agent_title(n))}</li>'
        for n in names)
    note = ""
    if not reviews and not proc.critic_enabled:
        note = ('<p class="sg-proc-note"><b>Про критика.</b> Это отдельный ИИ: он перечитывает работу автора и ищет слабые места, '
                'а автор сам решает, принять замечание или отклонить. Сейчас критик выключен настройкой сервера: '
                'он добавляет до двух обращений к модели на каждый шаг, то есть время и расходы. '
                'Когда его включат, замечания и решения автора появятся под каждым шагом.</p>')
    return (f'<section class="sg-proc" aria-label="Сводка по заданию"><div class="sg-proc-tiles">{"".join(tiles)}</div>'
            f'<ul class="sg-team" aria-label="Команда задания">{team}</ul>'
            '<div class="sg-proc-links"><a class="sg-mini-link" href="#sg-log">Журнал по строкам ↓</a>'
            + (f'<a class="sg-mini-link" href="{_e(map_href)}">Схема всей команды →</a>' if map_href else '')
            + '</div>' + note + '</section>')


# ── журнал по строкам ──────────────────────────────────────────────────────
def _events(task: Task, runs: list[AgentRun], proc: Process) -> list[tuple]:
    ev: list[tuple] = []   # (время, место в цепочке, порядок, тон, метка, текст, подпись)
    if task.created_at:
        ev.append((_utc(task.created_at), -1, 0, "info", "Задание", "Принято в работу", ""))
    for rank, run in enumerate(_ordered(runs)):
        who = agent_title(run.agent_name)
        stage = STAGE_NAMES.get(run.agent_name, "Подготовка материала")
        if run.started_at:
            ev.append((_utc(run.started_at), rank, 1, "run", "Старт", f"{who} начал: {stage}", ""))
        for call in proc.calls.get(run.id) or []:
            text = {"author": f"{who}: ответ модели получен",
                    "critic": "Критик перечитал работу автора",
                    "revision": "Автор переработал текст по замечаниям"}[call["stage"]]
            bits = [call["model"]] if call["model"] else []
            if call["tin"] is not None and call["tout"] is not None:
                bits.append(f"{_num(call['tin'])} → {_num(call['tout'])} токенов")
            ev.append((call["at"], rank, 2, "info", "Модель", text, " · ".join(bits)))
        for review in proc.reviews.get(run.id) or []:
            tone, headline = review_headline(review)
            ev.append((review["at"], rank, 3, tone, "Критик", headline, ""))
        for attempt in proc.attempts.get(run.id) or []:
            ev.append((attempt["at"], rank, 3, "err", "Сбой",
                       "Обращение к модели не удалось: " + ATTEMPT_FAILED[attempt["status"]], ""))
        end = run.finished_at or (run.started_at if run.status == "failed" else None)
        if run.status == "completed" and end:
            took = f"заняло {_span((_utc(end) - _utc(run.started_at)).total_seconds())}" if run.started_at else ""
            ev.append((_utc(end), rank, 4, "ok", "Готово", f"{who} закончил: {stage}", took))
        elif run.status == "failed" and end:
            ev.append((_utc(end), rank, 4, "err", "Ошибка", f"{who} остановился с ошибкой: {stage}", ""))
    for at, title in proc.results:
        ev.append((at, 999, 5, "ok", "Материал", f"Получен результат: {title}", ""))
    ev.sort(key=lambda row: (row[0], row[1], row[2]))
    return ev[:300]


def log_html(task: Task, runs: list[AgentRun], proc: Process) -> str:
    events = _events(task, runs, proc)
    if len(events) < 2:
        return ""
    first = events[0][0]
    lines = []
    for at, _rank, _order, tone, tag, text, meta in events:
        offset = int((at - first).total_seconds())
        lines.append(
            f'<li class="k-{tone}"><time datetime="{at.astimezone(timezone.utc).isoformat()}">{_clock(at)}'
            f'<small>+{offset // 60}:{offset % 60:02d}</small></time>'
            f'<div><p><span class="sg-log-tag">{_e(tag)}</span> {_e(text)}</p>'
            + (f'<small>{_e(meta)}</small>' if meta else '') + '</div></li>')
    return ('<div class="sg-work-log" id="sg-log"><h3>Журнал по строкам</h3>'
            f'<p class="sg-proc-note">Все события задания по порядку, время московское. Записей: {len(events)}.</p>'
            f'<ol class="sg-log">{"".join(lines)}</ol></div>')


# ── главная: работа команды за всё время ───────────────────────────────────
def team_stats_html(db: Session, product_id: UUID) -> str:
    rows = db.execute(
        select(AgentRun.agent_name, AgentRun.status, AgentRun.started_at, AgentRun.finished_at)
        .join(Task, Task.id == AgentRun.task_id)
        .where(Task.product_id == product_id, Task.assigned_agent.in_(WORK_AGENTS))
    ).all()
    if not rows:
        return ""
    done = [r for r in rows if r.status == "completed"]
    seconds = sum(max(0, (_utc(r.finished_at) - _utc(r.started_at)).total_seconds())
                  for r in done if r.started_at and r.finished_at)
    calls = db.scalar(select(func.count(CostRecord.id)).where(
        CostRecord.product_id == product_id, CostRecord.category == "llm")) or 0
    materials = db.scalar(select(func.count(Artifact.id)).where(
        Artifact.product_id == product_id, Artifact.kind.in_(RESULT_KINDS - {"editorial_plan"}))) or 0
    tiles = [
        (str(materials), _pl(materials, "материал", "материала", "материалов") + " подготовлено"),
        (str(len(done)), _pl(len(done), "шаг", "шага", "шагов") + " выполнено агентами"),
        (_span(seconds) if seconds else "—", "суммарно работала команда"),
    ]
    if calls:
        tiles.append((str(calls), _pl(calls, "обращение", "обращения", "обращений") + " к модели"))
    cells = "".join(f'<div class="sg-tile"><b>{_e(v)}</b><span>{_e(t)}</span></div>' for v, t in tiles)
    return ('<section class="sg-surface sg-teamstats" aria-label="Работа команды"><div class="sg-section-head"><div>'
            '<h2>Что уже сделала команда</h2><p>Считается по реальным шагам агентов за всё время.</p></div></div>'
            f'<div class="sg-proc-tiles">{cells}</div></section>')


CSS = r"""
/* ── Прозрачность процесса: сводка, критик, журнал по строкам ── */
.sg-proc{display:grid;gap:14px;margin:6px 0 22px}
.sg-proc-tiles{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.sg-tile{padding:14px;border-radius:16px;background:#F7F9FD;display:grid;gap:3px;align-content:start;min-width:0}
.sg-tile b{font-size:28px;line-height:1.1;font-weight:800;color:#182b41;letter-spacing:-.01em}
.sg-tile>span{font-size:14px;font-weight:650;color:#182b41;line-height:1.3}
.sg-tile small{font-size:13px;line-height:1.4;color:#56677C}
.sg-tile.crit-off{background:#FFF6E0}
.sg-tile.crit-off b{font-size:22px;color:#6B4300}
.sg-proc .sg-team{list-style:none;margin:0;padding:0;display:flex;flex-direction:row;flex-wrap:wrap;gap:8px}
.sg-proc .sg-team li{display:inline-flex;width:auto;align-items:center;gap:8px;padding:8px 12px;border-radius:999px;background:#fff;border:1px solid #DFE6F0;font-size:14px;font-weight:600;color:#182b41}
.sg-proc .sg-team li::before{content:"";width:9px;height:9px;border-radius:50%;background:#C3CCDA;flex:none}
.sg-proc .sg-team li.done::before{background:#14784A}
.sg-proc .sg-team li.run::before{background:#2F5BEB;box-shadow:0 0 0 4px rgba(47,91,235,.18)}
.sg-proc .sg-team li.err::before{background:#B3261E}
.sg-proc-note{margin:0;padding:12px 14px;border-radius:12px;background:#F7F9FD;color:#41546A;font-size:14px;line-height:1.5}
.sg-work-log .sg-proc-note{margin-bottom:6px}
.sg-proc-links{display:flex;flex-wrap:wrap;gap:6px 18px}
.sg-step-meta{margin:0;font-size:13px;color:#56677C;line-height:1.4}
.sg-step-meta.warn{color:#8A5A00}
.sg-critic{border-radius:12px;border:1px solid #DFE6F0;background:#fff;padding:10px 14px;display:grid;gap:8px}
.sg-critic-head{display:flex;gap:4px 10px;justify-content:space-between;flex-wrap:wrap;align-items:baseline}
.sg-critic-head b{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#56677C}
.sg-critic-head span{font-size:14px;font-weight:650;color:#182b41;line-height:1.4}
.sg-critic.ok{border-color:#CFE9DA;background:#F2FAF5}
.sg-critic.warn{border-color:#F3D9A4;background:#FFFAEE}
.sg-critic.err{border-color:#F2C4C0;background:#FEF3F2}
.sg-critic.run{border-color:#C9D7FA;background:#F2F6FF}
.sg-critic.mute{background:#F7F9FD}
.sg-find-list{list-style:none;margin:0;padding:0;display:grid}
.sg-find{display:grid;gap:5px;padding:10px 0;border-top:1px solid rgba(24,43,65,.08)}
.sg-stage-body{grid-template-columns:minmax(0,1fr)}
.sg-work-steps li.sg-find{display:grid;justify-content:stretch;align-items:start;gap:5px;padding:10px 0;font-size:14px;background:none;border-radius:0;color:inherit}
.sg-work-steps li.sg-find p{color:#182b41}
.sg-work-steps li.sg-find .sg-find-fix{color:#41546A}
.sg-work-steps li.sg-find .sg-find-verdict.yes{color:#14784A}
.sg-work-steps li.sg-find .sg-find-verdict.no{color:#8A5A00}
.sg-work-steps li.sg-find b{white-space:normal;color:inherit;font-weight:700}
.sg-find p{margin:0;font-size:14px;line-height:1.5;overflow-wrap:anywhere}
.sg-find-fix{color:#41546A}
.sg-find-verdict.yes{color:#14784A}
.sg-find-verdict.no{color:#8A5A00}
.sg-sev{justify-self:start;font-size:12px;font-weight:700;padding:3px 9px;border-radius:999px;background:#F7F9FD;color:#56677C}
.sg-sev.sev-critical{background:#FDE7E5;color:#B3261E}
.sg-sev.sev-major{background:#FFF0D2;color:#8A5A00}
.sg-work-log{margin-top:22px}
.sg-log{list-style:none;margin:0;padding:0;display:grid}
.sg-log li{display:grid;grid-template-columns:70px minmax(0,1fr);gap:12px;padding:10px 0;border-top:1px solid #E9EEF6}
.sg-log time{font-size:13px;font-weight:650;font-variant-numeric:tabular-nums;color:#182b41;line-height:1.3}
.sg-log time small{display:block;font-weight:500;color:#7A8AA0}
.sg-log li>div{min-width:0;display:grid;gap:2px;justify-items:start}
.sg-log p{margin:0;font-size:14px;line-height:1.55;color:#182b41}
.sg-log-tag{margin-right:4px;vertical-align:1px}
.sg-log li>div>small{font-size:12.5px;color:#7A8AA0;line-height:1.4}
.sg-log-tag{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:2px 8px;border-radius:999px;background:#EEF2F8;color:#56677C}
.sg-log .k-ok .sg-log-tag{background:#E0F3E8;color:#14784A}
.sg-log .k-run .sg-log-tag{background:#E8EEFF;color:#2349C4}
.sg-log .k-warn .sg-log-tag{background:#FFF0D2;color:#8A5A00}
.sg-log .k-err .sg-log-tag{background:#FDE7E5;color:#B3261E}
.sg-teamstats .sg-proc-tiles{margin-top:4px}
@media(min-width:921px){.sg-proc-tiles{grid-template-columns:repeat(4,minmax(0,1fr))}.sg-teamstats .sg-proc-tiles{grid-template-columns:repeat(4,minmax(0,1fr))}.sg-log li{grid-template-columns:90px minmax(0,1fr)}}
"""
