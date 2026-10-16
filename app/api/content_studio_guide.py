"""First-visit guidance for Growth Studio: how work gets done, where things are,
and what each step of the AI team concluded.

Everything here is read-only presentation. Step conclusions come from the
`content_stage_output` artifacts the pipelines already persist; nothing is
invented. Unknown fields are skipped, never dumped as raw JSON.
"""
from __future__ import annotations

import html
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import AgentRun, Artifact


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


# What each persisted stage output means, in the owner's words.
# Keys not listed here are deliberately not shown.
FIELD_LABELS = {
    "audience": "Для кого",
    "audience_pain": "Что волнует аудиторию",
    "pain": "Что волнует аудиторию",
    "audience_insight": "Что важно понять про аудиторию",
    "search_intent": "Что человек ищет",
    "angle": "Угол подачи",
    "hypothesis": "Предположение, которое проверяем",
    "hook": "Зацепка",
    "title": "Заголовок",
    "primary_query": "Главный поисковый запрос",
    "useful_takeaway": "Чем полезно читателю",
    "core_takeaway": "Главная мысль",
    "suggested_cta": "Призыв к действию",
    "cta": "Призыв к действию",
    "lead": "Начало текста",
    "closing": "Концовка",
    "adaptation_notes": "Как адаптировать под площадки",
}
LIST_LABELS = {
    "titles": "Варианты заголовка",
    "related_queries": "Похожие запросы",
    "outline": "План текста",
    "editor_notes": "Замечания редактора",
    "editorial_notes": "Замечания редактора",
}
ORDER = list(FIELD_LABELS) + list(LIST_LABELS)


def _short(value, limit: int = 420) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def stage_notes_from_content(raw: str) -> list[tuple[str, str | list[str]]]:
    """Turn one stored stage output into labelled owner-readable notes."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    output = data.get("output") if isinstance(data, dict) else None
    if not isinstance(output, dict):
        return []
    notes: list[tuple[str, str | list[str]]] = []
    for key in ORDER:
        value = output.get(key)
        if key in FIELD_LABELS and isinstance(value, str) and value.strip():
            notes.append((FIELD_LABELS[key], _short(value)))
        elif key in LIST_LABELS and isinstance(value, list):
            items = [_short(v, 220) for v in value if isinstance(v, str) and v.strip()][:8]
            if items:
                notes.append((LIST_LABELS[key], items))
    return notes


def load_stage_notes(db: Session, runs: list[AgentRun]) -> dict[UUID, list]:
    """run.id -> notes, read from the artifacts each run referenced."""
    wanted: dict[UUID, UUID] = {}
    for run in runs:
        for ref in run.output_references or []:
            if isinstance(ref, dict) and ref.get("kind") == "artifact" and ref.get("id"):
                try:
                    wanted[UUID(str(ref["id"]))] = run.id
                except ValueError:
                    continue
    if not wanted:
        return {}
    rows = db.scalars(select(Artifact).where(
        Artifact.id.in_(list(wanted)), Artifact.kind == "content_stage_output"))
    result: dict[UUID, list] = {}
    for artifact in rows:
        notes = stage_notes_from_content(artifact.content)
        if notes:
            result.setdefault(wanted[artifact.id], []).extend(notes)
    return result


def notes_html(notes: list) -> str:
    parts = []
    for label, value in notes:
        if isinstance(value, list):
            body = "<ul>" + "".join(f"<li>{_e(v)}</li>" for v in value) + "</ul>"
        else:
            body = f"<p>{_e(value)}</p>"
        parts.append(f'<div class="sg-note-row"><span class="sg-note-label">{_e(label)}</span>{body}</div>')
    return "".join(parts)


# ── Главная: как это работает и что где находится ──────────────────────────
HOW_STEPS = (
    ("Изучение темы", "Стратег выясняет, кому и зачем пишем."),
    ("Идея и план", "Выбирает зацепку, заголовок и порядок."),
    ("Написание", "Автор пишет текст или сценарий."),
    ("Редактура", "Редактор убирает лишнее и обещания, которые нельзя давать."),
    ("Версии под площадки", "Подгоняет материал под Telegram, ВКонтакте, Дзен."),
)


def how_it_works_html(product_id: UUID) -> str:
    base = f"/studio/products/{product_id}"
    steps = "".join(
        f'<li style="--i:{i}"><span class="sg-how-n">{i + 1}</span>'
        f'<div><b>{_e(t)}</b><span>{_e(d)}</span></div></li>'
        for i, (t, d) in enumerate(HOW_STEPS)
    )
    return f"""
    <section class="sg-surface sg-how" aria-labelledby="sg-how-h">
      <div class="sg-section-head"><div>
        <h2 id="sg-how-h">Как делается материал</h2>
        <p>Над заданием работает команда ИИ-помощников. Каждый делает свою часть и передаёт результат дальше.</p>
      </div></div>
      <ol class="sg-how-steps">{steps}</ol>
      <div class="sg-how-time">
        <b>Это не мгновенно.</b> Короткий пост готовится быстрее, полный пакет для нескольких площадок
        обычно занимает от получаса до часа. Страницу можно закрыть: задание не пропадёт.
        Что вывел каждый помощник, смотрите в разделе <a href="{base}/work">«В работе»</a>.
      </div>
    </section>"""


def where_html(product_id: UUID) -> str:
    base = f"/studio/products/{product_id}"
    rows = (
        ("＋", f"{base}/create", "Дать задание", "Материал на тему, контент-план или рисованное видео"),
        ("◴", f"{base}/work", "В работе", "Как идёт задание и что вывел каждый помощник"),
        ("▤", f"{base}/materials", "Материалы", "Готовые тексты и сценарии: прочитать, поправить, опубликовать"),
        ("↗", f"{base}/channels", "Публикации", "Подключить Telegram и посмотреть, что отправлено"),
        ("☷", f"{base}/comments", "Комментарии", "Ответы подписчикам, которые подготовила команда"),
        ("◎", f"/intelligence?product_id={product_id}", "Бизнес-разведка", "Изучить нишу и конкурентов"),
    )
    items = "".join(
        f'<a href="{href}"><span class="sg-where-ic" aria-hidden="true">{ic}</span>'
        f'<span><b>{_e(t)}</b><small>{_e(d)}</small></span><span class="sg-where-go" aria-hidden="true">›</span></a>'
        for ic, href, t, d in rows
    )
    return f"""
    <section class="sg-where" aria-labelledby="sg-where-h">
      <h2 id="sg-where-h">Что где находится</h2>
      <div class="sg-where-grid">{items}</div>
    </section>"""


CSS = r"""
/* ── Руководство для новичка: как делается материал / что где находится / выводы шагов ── */
.sg-how{display:grid;gap:14px}
.sg-how-steps{list-style:none;margin:0;padding:0;display:grid;gap:8px;grid-template-columns:1fr;counter-reset:none}
.sg-how-steps li{display:grid;grid-template-columns:36px minmax(0,1fr);gap:12px;align-items:start;padding:12px;border:1px solid #DFE6F0;border-radius:14px;background:#F7F9FD;animation:sgHow 12s infinite;animation-delay:calc(var(--i)*2s)}
.sg-how-steps b{display:block;font-size:15px;line-height:1.3}
.sg-how-steps span:not(.sg-how-n){display:block;font-size:14px;line-height:1.4;color:#56677C}
.sg-how-n{width:36px;height:36px;border-radius:50%;display:grid;place-items:center;font-weight:800;font-size:14px;background:#fff;border:2px solid #DFE6F0;color:#56677C}
.sg-how-time{padding:12px 14px;border-radius:12px;background:#FFF6E0;color:#6B4300;font-size:14px;line-height:1.5}
.sg-how-time a{color:inherit;font-weight:650}
@keyframes sgHow{0%,2%{background:#F7F9FD;border-color:#DFE6F0}4%,16%{background:#E8EEFF;border-color:#2F5BEB}20%,100%{background:#F7F9FD;border-color:#DFE6F0}}
@media(min-width:921px){.sg-how-steps{grid-template-columns:repeat(5,minmax(0,1fr))}.sg-how-steps li{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){.sg-how-steps li{animation:none}}
.sg-where{display:grid;gap:12px}
.sg-where-grid{display:grid;gap:10px;grid-template-columns:1fr}
.sg-where-grid a{display:grid;grid-template-columns:44px minmax(0,1fr) auto;gap:14px;align-items:center;padding:14px;border:1px solid #DFE6F0;border-radius:14px;background:#fff;color:inherit;text-decoration:none;min-height:72px}
.sg-where-ic{width:44px;height:44px;border-radius:12px;background:#E8EEFF;color:#2349C4;display:grid;place-items:center;font-size:20px}
.sg-where-grid b{display:block;font-size:16px}
.sg-where-grid small{display:block;font-size:14px;line-height:1.4;color:#56677C}
.sg-where-go{color:#56677C;font-size:20px}
@media(min-width:921px){.sg-where-grid{grid-template-columns:1fr 1fr}}
/* страница задания: шаги с выводами */
.sg-work-steps li.sg-stage{display:grid;grid-template-columns:36px minmax(0,1fr);gap:12px;align-items:start;padding:0 0 16px;background:none;border-radius:0;font-size:15px;color:inherit}
.sg-work-steps ol.sg-stage-list{gap:0}
.sg-stage-list{list-style:none;margin:0;padding:0;display:grid}
.sg-stage{display:grid;grid-template-columns:36px minmax(0,1fr);gap:12px;position:relative;padding-bottom:16px}
.sg-stage:not(:last-child)::before{content:"";position:absolute;left:17px;top:38px;bottom:2px;width:2px;background:#DFE6F0}
.sg-stage.done:not(:last-child)::before{background:#14784A}
.sg-stage-mk{width:36px;height:36px;border-radius:50%;display:grid;place-items:center;font-weight:800;font-size:14px;background:#F7F9FD;border:2px solid #DFE6F0;color:#56677C}
.sg-stage.done .sg-stage-mk{background:#14784A;border-color:#14784A;color:#fff}
.sg-stage.run .sg-stage-mk{border-color:#2F5BEB;background:#E8EEFF;color:#2349C4}
.sg-stage.err .sg-stage-mk{border-color:#B3261E;background:#FDE7E5;color:#B3261E}
.sg-stage-body{min-width:0;display:grid;gap:8px}
.sg-stage-head{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:baseline}
.sg-stage-head>div{min-width:0}
.sg-stage-head h4{margin:0;font-size:16px;line-height:1.3}
.sg-stage-head small{display:block;font-size:14px;color:#56677C;line-height:1.4}
.sg-stage-state{font-size:13px;font-weight:650;padding:4px 10px;border-radius:999px;background:#F7F9FD;color:#56677C;border:1px solid #DFE6F0;white-space:nowrap}
.sg-stage.done .sg-stage-state{background:#E0F3E8;border-color:#E0F3E8;color:#14784A}
.sg-stage.run .sg-stage-state{background:#E8EEFF;border-color:#E8EEFF;color:#2349C4}
.sg-stage.err .sg-stage-state{background:#FDE7E5;border-color:#FDE7E5;color:#B3261E}
.sg-stage-out{border:1px solid #DFE6F0;border-radius:12px;background:#F7F9FD}
.sg-stage-out>summary{min-height:44px;display:flex;align-items:center;padding:0 14px;cursor:pointer;font-weight:600;font-size:15px;color:#2349C4;list-style:none}
.sg-stage-out>summary::-webkit-details-marker{display:none}
.sg-stage-out>summary::after{content:"+";margin-left:auto;font-size:20px}
.sg-stage-out[open]>summary::after{content:"–"}
.sg-stage-out .sg-note-body{padding:0 14px 14px;display:grid;gap:10px;font-size:15px;line-height:1.5}
.sg-note-label{display:block;font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#56677C;margin-bottom:2px}
.sg-note-row p{margin:0}
.sg-stage-out .sg-note-row ul{list-style:disc;margin:0;padding:0 0 0 20px;display:block}
.sg-stage-out .sg-note-row li{margin:2px 0;padding:0;display:list-item}
.sg-stage-wait{margin:0;font-size:14px;color:#56677C}
"""
