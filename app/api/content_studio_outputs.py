"""Readable view of what each agent produced, and of video scripts.

Read-only presentation over `content_stage_output` artifacts that the
pipelines already persist. Nothing is invented and nothing is cut: the owner
sees the full text of every step. Model text is always escaped.

Two formats of stored output exist and both are supported:
  * wrapped:   {"stage": ..., "output": {...}, "fallback_used": ...}   (article, quick post)
  * unwrapped: the draft itself                                         (creative director)
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


CHANNELS = {
    "own_site": "Сайт", "dzen": "Дзен", "tenchat": "TenChat", "vc_ru": "vc.ru", "telegram": "Telegram",
    "vk": "ВКонтакте", "faq": "Вопросы и ответы", "founder": "Пост основателя",
    "short_video": "Короткое видео", "story": "Stories", "static_image": "Картинка",
    "youtube_shorts": "YouTube Shorts", "instagram_reels": "Instagram Reels",
}
ENUMS = {
    "advice": "Совет", "hypothesis": "Гипотеза", "fact": "Факт",
    "prepare_draft": "Подготовить черновик", "request_review": "Запросить проверку",
    "request_publication": "Запросить публикацию",
    "lead": "Заявка", "activation": "Активация", "customer": "Клиент",
    "demo": "Демо", "registration": "Регистрация", "booking": "Запись",
}
LABELS = {
    "audience": "Для кого", "pain": "Что волнует аудиторию", "audience_pain": "Что волнует аудиторию",
    "audience_insight": "Что важно понять про аудиторию", "search_intent": "Что человек ищет",
    "angle": "Угол подачи", "titles": "Варианты заголовка", "hypothesis": "Предположение, которое проверяем",
    "primary_query": "Главный поисковый запрос", "related_queries": "Похожие запросы",
    "title": "Заголовок", "hook": "Зацепка", "outline": "План", "adaptation_notes": "Как адаптировать под площадки",
    "sections": "Разделы текста", "proof_needed_notes": "Что нужно подтвердить фактами",
    "heading": "Подзаголовок", "body": "Текст", "lead": "Начало текста", "closing": "Концовка",
    "fact_renderings": "Проверенные факты", "editorial_notes": "Замечания редактора",
    "editor_notes": "Замечания редактора", "editorial_checks": "Проверки редактора",
    "cta": "Призыв к действию", "suggested_cta": "Призыв к действию", "final_cta": "Финальный призыв",
    "landing_page": "Куда ведём читателя", "internal_links": "Ссылки внутри сайта",
    "label": "Название", "destination": "Адрес", "measurement": "Как измеряем",
    "content": "Контент", "traffic": "Трафик", "customer": "Клиент",
    "event": "Событие", "metric": "Показатель", "attribution": "Как считаем",
    "activation_definition": "Что считаем активацией", "review_window_days": "Срок оценки, дней",
    "success_hypothesis": "Что считаем успехом", "distribution": "План распространения",
    "channel": "Площадка", "action": "Действие", "rationale": "Зачем", "downstream_goal": "Цель",
    "framing": "Подача", "derivatives": "Версии под площадки", "texts": "Готовые тексты",
    "useful_takeaway": "Чем полезно читателю", "core_takeaway": "Главная мысль",
    "caption": "Подпись", "cover_text": "Текст на обложке", "voiceover_script": "Озвучка",
    "visual_style": "Визуальный стиль", "frames": "Кадры", "production_notes": "Заметки для производства",
    "objective": "Цель", "narrative_arc": "Сюжет", "typography_direction": "Шрифты и текст",
    "export_spec": "Формат выгрузки", "number": "Номер", "duration_seconds": "Длительность, сек",
    "purpose": "Задача кадра", "visual_description": "Что в кадре", "composition": "Композиция",
    "on_screen_text": "Текст на экране", "animation_or_transition": "Анимация и переход",
    "interaction": "Интерактив", "safe_area_note": "Безопасная зона", "target_formats": "Форматы",
    "subject": "Главный объект", "background_and_environment": "Фон и окружение",
    "lighting_and_mood": "Свет и настроение", "overlay_text": "Текст на картинке",
    "brand_elements": "Элементы бренда", "image_generation_prompt": "Промпт для генерации",
    "avoid_in_visual": "Чего избегать", "cta_treatment": "Как показан призыв",
    "evidence_ids": "Опора на факты о продукте", "format": "Формат", "platform": "Площадка",
    "question": "Вопрос", "answer": "Ответ", "text": "Текст", "kind": "Вид",
}
# Technical keys that mean nothing to the owner.
HIDDEN = {"schema", "stage", "claim_ids", "status", "asset_type", "video_mode", "language", "source_id",
          "schema_version", "aspect_ratio"}
PROMPT_KEYS = {"generation_prompt", "image_generation_prompt"}
TITLE_KEYS = ("heading", "title", "purpose", "question", "label", "claim", "event")
CHIP_KEYS = ("channel", "kind", "format")


def label_of(key: str) -> str:
    return LABELS.get(key) or key.replace("_", " ").capitalize()


def _scalar(key: str, value) -> str:
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    text = str(value)
    if key in ("channel", "kind", "platform", "format", "target_platform"):
        return CHANNELS.get(text, ENUMS.get(text, text))
    return ENUMS.get(text, text)


def _text(key: str, value) -> str:
    return f'<p class="sg-o-text">{_e(_scalar(key, value))}</p>'


# ── Разбор сохранённого вывода ───────────────────────────────────────────
def parse_stage(raw: str) -> dict | None:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if "output" in data:
        if not isinstance(data["output"], dict):
            return None
        return {"stage": str(data.get("stage") or ""), "output": data["output"],
                "fallback": bool(data.get("fallback_used")),
                "reason": str(data.get("fallback_reason") or "")}
    return {"stage": "", "output": data, "fallback": False, "reason": ""}


def load_stage_outputs(db: Session, runs: list[AgentRun]) -> dict[UUID, list[dict]]:
    """run.id -> parsed outputs, read from the artifacts each run referenced."""
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
        Artifact.id.in_(list(wanted)), Artifact.kind == "content_stage_output"
    ).order_by(Artifact.created_at, Artifact.id))
    result: dict[UUID, list[dict]] = {}
    for artifact in rows:
        info = parse_stage(artifact.content)
        if info:
            info["id"] = str(artifact.id)
            result.setdefault(wanted[artifact.id], []).append(info)
    return result


# ── Общий разбор любого вывода ───────────────────────────────────────────
def _copy_btn(target: str, text: str = "Копировать") -> str:
    return f'<button class="sg-copy" type="button" data-sg-copy="{_e(target)}">{_e(text)}</button>'


def _list_html(key: str, items: list) -> str:
    strings = [x for x in items if isinstance(x, (str, int, float)) and str(x).strip()]
    dicts = [x for x in items if isinstance(x, dict)]
    out = ""
    if strings:
        out += "<ul class=\"sg-o-list\">" + "".join(f"<li>{_e(_scalar(key, x))}</li>" for x in strings) + "</ul>"
    if dicts:
        out += '<div class="sg-o-items">' + "".join(_item_html(d, i) for i, d in enumerate(dicts, 1)) + "</div>"
    return out


def _value_html(key: str, value, uid: str) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return ""
    if isinstance(value, dict):
        return fields_html(value, uid)
    if isinstance(value, list):
        return _list_html(key, value)
    return _text(key, value)


def fields_html(data: dict, uid: str = "o", skip: tuple = ()) -> str:
    rows = []
    for n, (key, value) in enumerate(data.items()):
        if key in HIDDEN or key in skip:
            continue
        if key in PROMPT_KEYS:
            if value:
                rid = f"{uid}-{key}-{n}"
                rows.append(
                    f'<details class="sg-o-prompt"><summary>{_e(label_of(key))}</summary>'
                    f'<div id="{rid}" class="sg-o-pre">{_e(value)}</div>{_copy_btn(rid)}</details>')
            continue
        if key == "evidence_ids":
            if isinstance(value, list) and value:
                rows.append(f'<div class="sg-o-row sg-o-muted"><span class="sg-o-label">{_e(label_of(key))}</span>'
                            f'<p class="sg-o-text">{_e(", ".join(str(v) for v in value))}</p></div>')
            continue
        body = _value_html(key, value, f"{uid}-{n}")
        if body:
            rows.append(f'<div class="sg-o-row"><span class="sg-o-label">{_e(label_of(key))}</span>{body}</div>')
    return "".join(rows)


def _item_html(item: dict, index: int) -> str:
    title_key = next((k for k in TITLE_KEYS if item.get(k)), "")
    chip_key = next((k for k in CHIP_KEYS if item.get(k)), "")
    title = _scalar(title_key, item[title_key]) if title_key else ""
    chip = _scalar(chip_key, item[chip_key]) if chip_key else ""
    if not title and chip:
        title, chip, chip_key = chip, "", ""
    number = item.get("number")
    head = (f'<span class="sg-o-n">{_e(number if number else index)}</span>'
            f'<h5>{_e(title) or "Пункт " + str(index)}</h5>'
            + (f'<span class="sg-o-chip">{_e(chip)}</span>' if chip else ""))
    if isinstance(item.get("kind"), str) and item["kind"] in ENUMS and chip_key != "kind":
        head += f'<span class="sg-o-chip">{_e(ENUMS[item["kind"]])}</span>'
    used = {"number", title_key, chip_key, "kind"} - {""}
    rest = {k: v for k, v in item.items() if k not in used}
    return f'<article class="sg-o-item"><div class="sg-o-item-head">{head}</div>{fields_html(rest, f"i{index}")}</article>'


# ── Сценарий видео ───────────────────────────────────────────────────────
def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sec(value) -> str:
    number = _num(value)
    return str(int(number)) if number == int(number) else f"{number:g}"


def _shot(raw: dict, index: int) -> dict:
    return {
        "n": raw.get("number") or index,
        "start": _num(raw.get("start_second")), "end": _num(raw.get("end_second")),
        "purpose": raw.get("purpose") or raw.get("shot_type") or "",
        "shot_type": raw.get("shot_type") or "",
        "visual": raw.get("visual_description") or raw.get("visual_prompt") or "",
        "camera": raw.get("camera_and_movement") or "",
        "action": raw.get("action") or raw.get("action_or_animation") or "",
        "spoken": raw.get("spoken_text") or "",
        "screen": raw.get("on_screen_text") or "",
        "transition": raw.get("transition") or "",
        "sound": raw.get("sound_or_music_note") or "",
        "prompt": raw.get("generation_prompt") or "",
        "avoid": raw.get("avoid_in_generation") or "",
    }


def video_brief_of(output: dict) -> dict | None:
    """Return the dict that holds shots/frames, or None when this is not a video script."""
    if not isinstance(output, dict):
        return None
    brief = output.get("production_brief")
    if isinstance(brief, dict) and isinstance(brief.get("shots"), list) and brief["shots"]:
        return brief
    if isinstance(output.get("shots"), list) and output["shots"]:
        return output
    frames = output.get("frames")
    if (output.get("format") in ("short_video", "story") and isinstance(frames, list) and frames
            and isinstance(frames[0], dict) and "start_second" in frames[0]):
        return output
    return None


def _plain_script(brief: dict, shots: list[dict]) -> str:
    lines = []
    if brief.get("objective"):
        lines.append("Цель: " + str(brief["objective"]))
    voice = brief.get("voiceover_or_dialogue_script") or brief.get("voiceover_script")
    if voice:
        lines += ["", "Озвучка:", str(voice)]
    lines.append("")
    for s in shots:
        lines.append(f"Кадр {s['n']} · {_sec(s['start'])}–{_sec(s['end'])} сек. {s['purpose']}".strip())
        for label, key in (("Что в кадре", "visual"), ("Камера", "camera"), ("Действие", "action"),
                           ("Говорит", "spoken"), ("На экране", "screen"), ("Переход", "transition")):
            if s[key]:
                lines.append(f"  {label}: {s[key]}")
        lines.append("")
    return "\n".join(lines).strip()


def video_viewer_html(source: dict, uid: str = "vid") -> str:
    """One readable layout for every short-video script (Shorts, Reels, video director, creative)."""
    raw_shots = source.get("shots") or source.get("frames") or []
    shots = [_shot(s, i) for i, s in enumerate((x for x in raw_shots if isinstance(x, dict)), 1)]
    if not shots:
        return '<p class="sg-muted">Раскадровка пока пустая.</p>'
    total = max((s["end"] for s in shots), default=0) or _num(source.get("duration_seconds"))
    platform = source.get("target_platform") or source.get("platform") or ""
    chips = []
    if platform:
        chips.append(CHANNELS.get(platform, platform))
    chips.append("Вертикальное 9:16")
    if total:
        chips.append(f"{_sec(total)} сек")
    chips.append(f"кадров: {len(shots)}")
    chip_html = "".join(f'<span class="sg-o-chip">{_e(c)}</span>' for c in chips)

    hooks = source.get("hook_variants") or ([source["hook"]] if source.get("hook") else [])
    hooks_html = ""
    if hooks:
        hooks_html = ('<section class="sg-vid-block"><h4>Зацепка в первые секунды</h4><ul class="sg-vid-hooks">'
                      + "".join(f"<li>«{_e(h)}»</li>" for h in hooks if h) + "</ul></section>")

    voice = source.get("voiceover_or_dialogue_script") or source.get("voiceover_script") or ""
    voice_html = ""
    if str(voice).strip():
        vid = f"{uid}-voice"
        voice_html = (f'<section class="sg-vid-voice"><div class="sg-vid-voice-head"><h4>Текст озвучки</h4>'
                      f'{_copy_btn(vid)}</div><div id="{vid}" class="sg-o-pre">{_e(voice)}</div></section>')

    line = []
    for s in shots:
        dur = max(s["end"] - s["start"], 1)
        line.append(f'<li style="flex:{dur:g}"><a href="#{uid}-s{s["n"]}" title="{_e(s["purpose"])}">{_e(s["n"])}</a></li>')
    ticks = f'<div class="sg-vid-ticks"><span>0 сек</span><span>{_e(_sec(total))} сек</span></div>' if total else ""
    timeline = (f'<section class="sg-vid-block"><h4>Ход ролика по секундам</h4>'
                f'<ol class="sg-vid-line" aria-label="Кадры по времени">{"".join(line)}</ol>{ticks}</section>')

    cards = []
    for s in shots:
        rows = []
        for label, key in (("Что в кадре", "visual"), ("Камера", "camera"), ("Действие", "action"),
                           ("Звук", "sound"), ("Переход", "transition")):
            if s[key]:
                rows.append(f"<div><dt>{label}</dt><dd>{_e(s[key])}</dd></div>")
        spoken = (f'<p class="sg-shot-say"><b>Говорит</b>{_e(s["spoken"])}</p>' if s["spoken"] else "")
        screen = (f'<p class="sg-shot-screen"><b>На экране</b><span>{_e(s["screen"])}</span></p>' if s["screen"] else "")
        prompt = ""
        if s["prompt"]:
            pid = f'{uid}-p{s["n"]}'
            prompt = (f'<details class="sg-o-prompt"><summary>Промпт для генерации кадра</summary>'
                      f'<div id="{pid}" class="sg-o-pre">{_e(s["prompt"])}</div>{_copy_btn(pid)}'
                      + (f'<p class="sg-muted">Не показывать: {_e(s["avoid"])}</p>' if s["avoid"] else "")
                      + "</details>")
        cards.append(
            f'<li class="sg-shot" id="{uid}-s{s["n"]}"><div class="sg-shot-head">'
            f'<span class="sg-o-n">{_e(s["n"])}</span><div><b>{_e(s["purpose"] or "Кадр")}</b>'
            f'<small>Кадр {_e(s["n"])} · {_e(_sec(s["start"]))}–{_e(_sec(s["end"]))} сек'
            + (f' · {_e(s["shot_type"])}' if s["shot_type"] and s["shot_type"] != s["purpose"] else "")
            + f'</small></div></div>{spoken}{screen}<dl>{"".join(rows)}</dl>{prompt}</li>')
    shots_html = f'<section class="sg-vid-block"><h4>Кадры</h4><ol class="sg-shots">{"".join(cards)}</ol></section>'

    captions = source.get("caption_variants") or ([source["caption"]] if source.get("caption") else [])
    cap_html = ""
    if captions:
        items = []
        for i, c in enumerate(captions, 1):
            cid = f"{uid}-cap{i}"
            items.append(f'<div class="sg-vid-cap"><div id="{cid}" class="sg-o-pre">{_e(c)}</div>{_copy_btn(cid)}</div>')
        cap_html = f'<section class="sg-vid-block"><h4>Подпись под видео</h4>{"".join(items)}</section>'

    extra_rows = []
    for label, keys in (("Идея", ("creative_concept",)), ("Визуальный стиль", ("visual_style",)),
                        ("Место и реквизит", ("setting_and_props",)), ("Кто в кадре", ("talent_direction",)),
                        ("Субтитры", ("subtitle_style",)), ("Музыка", ("music_direction",)),
                        ("Ритм монтажа", ("editing_rhythm",)), ("Монтаж и сведение", ("editing_mix_notes",)),
                        ("Обложка", ("cover_frame", "cover_text")), ("Финальный кадр с призывом", ("cta_frame", "cta")),
                        ("Формат выгрузки", ("export_spec",))):
        value = next((source[k] for k in keys if source.get(k)), "")
        if value:
            extra_rows.append(f"<div><dt>{_e(label)}</dt><dd>{_e(value)}</dd></div>")
    notes = [n for n in source.get("production_notes") or [] if isinstance(n, str) and n.strip()]
    extra = ""
    if extra_rows or notes:
        extra = ('<details class="sg-vid-more"><summary>Монтаж, музыка и выпуск</summary>'
                 + (f'<dl class="sg-vid-dl">{"".join(extra_rows)}</dl>' if extra_rows else "")
                 + ("<h5>Заметки для производства</h5><ul class=\"sg-o-list\">"
                    + "".join(f"<li>{_e(n)}</li>" for n in notes) + "</ul>" if notes else "") + "</details>")

    objective = (f'<p class="sg-vid-obj"><b>Цель.</b> {_e(source["objective"])}</p>' if source.get("objective") else "")
    plain_id = f"{uid}-plain"
    plain = f'<div id="{plain_id}" hidden>{_e(_plain_script(source, shots))}</div>'
    return (f'<div class="sg-vid" id="{uid}"><div class="sg-vid-top"><div class="sg-vid-chips">{chip_html}</div>'
            f'{_copy_btn(plain_id, "Скопировать весь сценарий")}</div>{objective}{hooks_html}{voice_html}'
            f'{timeline}{shots_html}{cap_html}{extra}{plain}</div>')


# ── Вывод одного шага ───────────────────────────────────────────────────
def stage_output_html(info: dict, uid: str = "o") -> str:
    output = info.get("output") or {}
    parts = []
    if info.get("fallback"):
        reason = f" Причина: {_e(info['reason'])}." if info.get("reason") else ""
        parts.append('<p class="sg-o-warn">Здесь использован запасной вариант: модель не вернула нужный ответ.'
                     f'{reason} Проверьте текст особенно внимательно.</p>')
    video = video_brief_of(output)
    if video is not None:
        if video is output and output.get("title"):
            parts.append(f'<h4 class="sg-vid-title">{_e(output["title"])}</h4>')
        parts.append(video_viewer_html(video, f"{uid}v"))
        if video is output:
            # the viewer already shows hook, voice, caption, style, cover, cta and notes
            core = {k: output[k] for k in ("evidence_ids",) if k in output}
        else:
            core = {k: v for k, v in output.items() if k != "production_brief"}
        more = fields_html(core, f"{uid}c") if core else ""
        if more:
            parts.append(f'<details class="sg-vid-more"><summary>Подача и ссылки</summary>{more}</details>')
    else:
        parts.append(fields_html(output, uid))
    body = "".join(p for p in parts if p)
    return body or '<p class="sg-muted">В этом шаге нет текста для показа.</p>'


def stage_outputs_html(infos: list[dict], uid: str) -> str:
    return "".join(f'<div class="sg-o-stage">{stage_output_html(info, f"{uid}{n}")}</div>'
                   for n, info in enumerate(infos))


def plain_script_of(source: dict) -> str:
    raw = source.get("shots") or source.get("frames") or []
    return _plain_script(source, [_shot(s, i) for i, s in enumerate((x for x in raw if isinstance(x, dict)), 1)])


def video_for_record(value: dict, uid: str) -> str | None:
    """Material page / dialog: a saved video script or creative brief as one readable viewer."""
    brief = video_brief_of(value)
    if brief is None and isinstance(value.get("production_brief"), dict):
        brief = value["production_brief"]
    if brief is None or not (brief.get("shots") or brief.get("frames")):
        return None
    return video_viewer_html(brief, uid)


JS = r"""
(function(){
  if(window.__sgOutputs)return;window.__sgOutputs=true;
  document.addEventListener('click',async function(e){
    const btn=e.target.closest&&e.target.closest('[data-sg-copy]');
    if(btn){
      const el=document.getElementById(btn.dataset.sgCopy);if(!el)return;
      const text=(el.innerText||el.textContent||'').trim();const old=btn.textContent;
      try{await navigator.clipboard.writeText(text);btn.textContent='Скопировано';setTimeout(function(){btn.textContent=old},1400)}
      catch(_){const r=document.createRange();r.selectNodeContents(el);const s=window.getSelection();s.removeAllRanges();s.addRange(r);btn.textContent='Выделено: нажмите Ctrl+C'}
      return;
    }
    const all=e.target.closest&&e.target.closest('[data-sg-toggle-outputs]');
    if(all){
      const list=document.querySelectorAll('details.sg-stage-out');
      const open=all.dataset.state!=='open';
      list.forEach(function(d){d.open=open});
      all.dataset.state=open?'open':'closed';
      all.textContent=open?'Свернуть все выводы':'Раскрыть все выводы';
    }
  });
})();
"""

CSS = r"""
/* ═════ Выводы агентов и сценарии ═════ */
.sg-o-stage{display:grid;gap:12px;min-width:0}
.sg-o-row{display:grid;gap:4px;min-width:0}
.sg-o-label{font-size:12px;font-weight:600;color:var(--air-muted,#7e8d9e);letter-spacing:0}
.sg-o-text{margin:0;font-size:15px;line-height:1.55;white-space:pre-wrap;overflow-wrap:break-word;min-width:0}
.sg-o-muted .sg-o-text{font-size:12px;color:var(--air-soft,#96a4b3)}
.sg-o-list{margin:0;padding-left:20px;display:grid;gap:5px;font-size:15px;line-height:1.5}
.sg-o-items{display:grid;gap:10px}
.sg-o-item{display:grid;gap:8px;padding:12px;border-radius:14px;background:#f5f8fc;min-width:0}
.sg-o-item-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.sg-o-item-head h5{margin:0;font-size:15px;font-weight:600;line-height:1.3;flex:1 1 140px;min-width:0}
.sg-o-n{display:inline-grid;place-items:center;min-width:26px;height:26px;padding:0 6px;border-radius:13px;background:#e6eefb;color:#245aaf;font-size:12px;font-weight:700}
.sg-o-chip{display:inline-block;padding:3px 10px;border-radius:999px;background:#eef3fa;color:#3d5a80;font-size:12px;font-weight:600;white-space:nowrap}
.sg-o-warn{margin:0;padding:10px 12px;border-radius:12px;background:#fff5e7;color:#8a5a12;font-size:13px;line-height:1.45}
.sg-o-pre{white-space:pre-wrap;overflow-wrap:break-word;font-size:14px;line-height:1.55;min-width:0}
.sg-o-prompt{border-radius:12px;background:#f5f8fc;padding:0}
.sg-o-prompt>summary{cursor:pointer;padding:10px 12px;font-size:13px;font-weight:600;color:#365473;list-style:none}
.sg-o-prompt>summary::-webkit-details-marker{display:none}
.sg-o-prompt>summary:before{content:"▸ ";color:#7396c2}
.sg-o-prompt[open]>summary:before{content:"▾ "}
.sg-o-prompt>.sg-o-pre{padding:0 12px 8px;color:#41556f;font-size:13px}
.sg-o-prompt>.sg-copy{margin:0 12px 12px}
.sg-o-prompt>.sg-muted{margin:0 12px 12px}
.sg-copy{border:0;border-radius:11px;background:#fff;color:#245aaf;box-shadow:inset 0 0 0 1px #dbe5f2;font-size:13px;font-weight:600;padding:8px 12px;min-height:36px}
.sg-copy:active{background:#eef4fc}

.sg-vid{display:grid;gap:16px;min-width:0}
.sg-vid-top{display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.sg-vid-chips{display:flex;gap:6px;flex-wrap:wrap}
.sg-vid-obj{margin:0;font-size:15px;line-height:1.55}
.sg-vid-block{display:grid;gap:8px;min-width:0}
.sg-vid-block h4,.sg-vid-voice h4{margin:0;font-size:14px;font-weight:600;color:#365473}
.sg-vid-hooks{margin:0;padding:0;list-style:none;display:grid;gap:6px}
.sg-vid-hooks li{padding:10px 12px;border-radius:14px;background:#eef4fd;font-size:15px;line-height:1.4;font-weight:550}
.sg-vid-voice{display:grid;gap:8px;padding:14px;border-radius:16px;background:#f3f7fd}
.sg-vid-voice-head{display:flex;justify-content:space-between;align-items:center;gap:8px}
.sg-vid-line{display:flex;gap:3px;height:38px;margin:0;padding:0;list-style:none}
.sg-vid-line li{min-width:30px;border-radius:9px;background:linear-gradient(120deg,#2960bd,#4e93ea);overflow:hidden}
.sg-vid-line li:nth-child(even){background:linear-gradient(120deg,#35a7d8,#5fc4e8)}
.sg-vid-line a{display:grid;place-items:center;height:100%;color:#fff;font-size:13px;font-weight:700}
.sg-vid-ticks{display:flex;justify-content:space-between;font-size:11px;color:var(--air-soft,#96a4b3)}
.sg-shots{margin:0;padding:0;list-style:none;display:grid;gap:12px}
.sg-shot{display:grid;gap:10px;padding:14px;border-radius:18px;background:#fff;box-shadow:0 1px 2px rgba(24,43,65,.05),0 8px 22px rgba(24,43,65,.05);scroll-margin-top:16px;min-width:0}
.sg-shot-head{display:flex;gap:10px;align-items:center}
.sg-shot-head b{display:block;font-size:15px;font-weight:600;line-height:1.3}
.sg-shot-head small{color:var(--air-muted,#7e8d9e);font-size:12px}
.sg-shot-say{margin:0;padding:10px 12px;border-radius:12px;background:#f3f7fd;border-left:3px solid #4e93ea;font-size:15px;line-height:1.5;display:grid;gap:2px}
.sg-shot-say b,.sg-shot-screen b{font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:#7396c2}
.sg-shot-screen{margin:0;padding:10px 12px;border-radius:12px;background:#182b41;color:#fff;font-size:15px;line-height:1.4;display:grid;gap:2px}
.sg-vid .sg-shot-screen,.sg-vid .sg-shot-screen span{color:#fff!important}
.sg-vid .sg-shot-screen b{color:#9bb6dc!important}
.sg-shot dl,.sg-vid-dl{margin:0;display:grid;gap:8px}
.sg-shot dl>div,.sg-vid-dl>div{display:grid;grid-template-columns:116px minmax(0,1fr);gap:8px;font-size:14px;line-height:1.5}
.sg-shot dt,.sg-vid-dl dt{color:var(--air-muted,#7e8d9e);font-weight:600;font-size:13px}
.sg-shot dd,.sg-vid-dl dd{margin:0;overflow-wrap:break-word;min-width:0}
.sg-vid-cap{display:grid;gap:8px;padding:12px;border-radius:14px;background:#f5f8fc}
.sg-vid-more{border-radius:14px;background:#f5f8fc;padding:0}
.sg-vid-more>summary{cursor:pointer;padding:12px 14px;font-weight:600;font-size:14px;color:#365473}
.sg-vid-more>*:not(summary){margin:0 14px 14px}
.sg-vid-more h5{font-size:13px;margin:6px 14px 6px;color:#365473}
.sg-vid-all{display:flex;justify-content:flex-end}
.sg-out-toggle{border:0;background:none;color:#245aaf;font-weight:600;font-size:14px;padding:6px 2px;min-height:36px}
.sg-work-steps li.sg-stage .sg-stage-out>.sg-note-body,.sg-stage-out>.sg-o-stage{padding-top:4px}
/* на странице задания старые правила .sg-work-steps li (flex, nowrap) задевают вложенные списки */
.sg-work-steps .sg-vid-line{display:flex;gap:3px;padding:0}
.sg-work-steps .sg-vid-line>li{display:block;padding:0;background:linear-gradient(120deg,#2960bd,#4e93ea);border-radius:9px;font-size:inherit;color:inherit}
.sg-work-steps .sg-vid-line>li:nth-child(even){background:linear-gradient(120deg,#35a7d8,#5fc4e8)}
.sg-work-steps .sg-shots{display:grid;gap:12px;padding:0}
.sg-work-steps .sg-shots>li{display:grid;justify-content:stretch;align-items:start;gap:10px;padding:14px;border-radius:18px;background:#fff;font-size:inherit;color:inherit}
.sg-work-steps .sg-vid-hooks>li{display:block;padding:10px 12px;border-radius:14px;background:#eef4fd;font-size:15px;color:inherit}
.sg-work-steps .sg-o-list>li{display:list-item;padding:0;background:none;font-size:15px;color:inherit}
.sg-work-steps .sg-o-stage b{white-space:normal;color:inherit;font-weight:700}
.sg-work-steps .sg-o-stage .sg-shot-head b{font-weight:600}
@media(max-width:520px){
  .sg-shot dl>div,.sg-vid-dl>div{grid-template-columns:minmax(0,1fr);gap:2px}
  .sg-vid-top .sg-copy{width:100%}
}
"""
