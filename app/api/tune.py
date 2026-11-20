"""Owner-only /studio/tune: every agent prompt in readable form, editable, plus the
intermediate work of each task (stage outputs, models used, critic findings).

Access: a Studio operator account whose login is listed in STUDIO_TUNE_USERNAMES.
"""
from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import datetime
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

# Importing the agent modules registers their prompts.
import app.content.calendar  # noqa: F401
import app.content.creative  # noqa: F401
import app.content.quick_post  # noqa: F401
import app.content.revisions  # noqa: F401
import app.llm.critic  # noqa: F401
import app.workflows.article_production as article_production
from app import prompts
from app.api.content_studio import _studio_form
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.api.team_dashboard import _NAMES
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.domain import AgentRun, Artifact, CostRecord, Product, Task
from app.vk_service import (platform_service_token, save_platform_service_token,
                            delete_platform_service_token, verify_platform_service_token,
                            group_app_credentials, group_app_verified,
                            save_group_app_credentials, remove_group_app_credentials)

router = APIRouter()

EXTRA_NAMES = {
    "post_strategist": "Стратег поста", "post_writer_editor": "Автор и редактор поста",
    "post_rewriter": "Редактор правок поста", "creative_strategist": "Креативный стратег",
    "editorial_planner": "Редактор контент-плана", "viral_content_researcher": "Исследователь вирусного контента",
    **article_production.STAGE_TITLES,
}
# Which registered prompts each agent receives, in the order they are concatenated.
AGENT_PROMPTS = {
    **{name: ("article.task." + name, "article.common", "article.rules." + name)
       for name in article_production.STAGE_TITLES},
    "post_strategist": ("quick_post.common", "quick_post.strategist"),
    "post_writer_editor": ("quick_post.common", "quick_post.writer"),
    "creative_strategist": ("creative.common", "creative.strategist"),
    "creative_director": ("creative.common", "creative.director"),
    "post_rewriter": ("revision.post",),
    "editorial_planner": ("calendar.plan",),
}
STATUS = {"queued": "в очереди", "running": "работает", "completed": "готово", "failed": "ошибка",
          "cancelled": "отменено", "waiting_approval": "ждёт подтверждения"}
REVIEW_STATUS = {
    "no_material_issues": "серьёзных замечаний нет", "revised": "автор доработал текст по замечаниям",
    "all_rejected": "автор отклонил все замечания", "rejected_domain_revision": "доработка не прошла проверку, оставлен исходный текст",
    "review_unavailable": "критик не ответил вовремя, оставлен исходный текст",
    "revision_unavailable": "доработка не удалась, оставлен исходный текст",
    "skipped_incomplete_context": "пропущено: слишком большой контекст",
    "skipped_incomplete_original": "пропущено: ответ автора оборван", "skipped_size_limit": "пропущено: слишком длинный ответ",
    "skipped_invalid_original": "пропущено: ответ автора не по формату", "skipped_ineligible": "не проверяется",
    "advisory_only_no_domain_validator": "замечания только для информации",
    "revision_pending": "ждёт доработки", "revision_cancelled": "доработка прервана",
}


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def agent_name(name: str) -> str:
    return EXTRA_NAMES.get(name) or _NAMES.get(name) or name.replace("_", " ")


def require_tune_owner(request: Request, username=Depends(require_studio_auth),
                       settings: Settings = Depends(get_settings)) -> str:
    allowed = {u.strip() for u in (settings.studio_tune_usernames or "").split(",") if u.strip()}
    production = settings.app_env.casefold() in {"production", "prod"}
    if username is None and not production and not allowed:
        return "local_owner"  # Developer loopback without configured logins.
    if (username is None or username not in allowed
            or getattr(request.state, "studio_product_ids", None) is not None):
        raise HTTPException(403, "Страница настройки агентов доступна только владельцу")
    return username


CSS = """
:root{--bg:#f5f7fb;--card:#fff;--text:#16202e;--muted:#5d6b80;--line:#e3e9f2;--accent:#067cff;--bad:#c62828;--ok:#1b7f3a;--warn:#b26a00}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line);padding:14px 20px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
header b{font-size:18px}nav a{margin-right:14px;color:var(--muted);text-decoration:none;font-weight:600}nav a.on{color:var(--accent)}
main{max-width:1100px;margin:0 auto;padding:20px 16px 60px}h1{font-size:24px;margin:8px 0 4px}h2{font-size:19px;margin:28px 0 10px}
.lead{color:var(--muted);margin:0 0 16px}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin:12px 0}
.card h3{margin:0 0 4px;font-size:16px}.muted{color:var(--muted);font-size:13px}.tag{display:inline-block;font-size:12px;padding:2px 8px;border-radius:99px;background:#eef3fb;color:var(--muted);margin-left:6px}
.tag.changed{background:#fff3df;color:var(--warn)}.tag.ok{background:#e6f5ea;color:var(--ok)}.tag.bad{background:#fdeaea;color:var(--bad)}
textarea{width:100%;min-height:180px;font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;border:1px solid var(--line);border-radius:10px;padding:10px;resize:vertical}
input[type=text],input[type=password]{display:block;max-width:560px;width:100%;padding:10px;border:1px solid var(--line);border-radius:10px;font:inherit;margin:4px 0 14px}
input[type=checkbox]{margin:10px 5px 10px 0}
button{background:var(--accent);color:#fff;border:0;border-radius:9px;padding:8px 14px;font-weight:650;cursor:pointer}button.plain{background:#eef3fb;color:var(--text)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px}details summary{cursor:pointer;color:var(--accent);font-weight:600}
.notice{padding:10px 14px;border-radius:10px;background:#e6f5ea;color:var(--ok);margin:10px 0}.error{background:#fdeaea;color:var(--bad)}
table{width:100%;border-collapse:collapse;background:var(--card);border-radius:12px;overflow:hidden}td,th{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;font-size:14px}
dl{margin:6px 0}dt{font-weight:650;color:var(--muted);font-size:13px;margin-top:8px}dd{margin:2px 0 0 0;white-space:pre-wrap}
ul.v{margin:4px 0;padding-left:18px}.finding{border-left:3px solid var(--line);padding:4px 10px;margin:8px 0}.finding.critical{border-color:var(--bad)}.finding.major{border-color:var(--warn)}
@media(max-width:640px){header{padding:12px}main{padding:14px 12px 40px}td,th{font-size:13px;padding:7px}}
"""


def page(title: str, active: str, body: str) -> HTMLResponse:
    tabs = "".join(f'<a class="{"on" if key == active else ""}" href="{href}">{label}</a>' for key, href, label in (
        ("prompts", "/studio/tune", "Промпты агентов"), ("work", "/studio/tune/work", "Ход работы"),
        ("chat", "/studio/chat", "ИИ-чат"), ("models", "/studio/team", "Модели и критик"),
        ("vk", "/studio/tune/vk", "VK API"), ("studio", "/studio", "← В Studio")))
    return HTMLResponse(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Настройка агентов</title>
<style>{CSS}</style></head><body><header><b>Настройка агентов</b><nav>{tabs}</nav></header>
<main>{body}</main></body></html>""", headers={"Cache-Control": "no-store"})


def _routes() -> dict:
    """Main and critic models per role, as the router would choose them now."""
    try:
        from app.api.team_dashboard import preview_configuration
        from app.llm.router_store import read_state
        state = read_state(get_settings())
        if not state.config.enabled:
            return {}
        return {row["id"]: (row["main"].get("models") or [], row["critic"].get("models") or [])
                for row in preview_configuration(state.config)["roles"]}
    except Exception:
        return {}


@router.get("/studio/tune", response_class=HTMLResponse)
def tune_prompts(saved: str | None = None, error: str | None = None, owner: str = Depends(require_tune_owner)):
    state = prompts.read_state()
    overrides = state["prompts"]
    groups = defaultdict(list)
    for spec in prompts.REGISTRY.values():
        groups[spec.group].append(spec)
    routes = _routes()
    agents_of = defaultdict(list)
    for agent, keys in AGENT_PROMPTS.items():
        for key in keys:
            agents_of[key].append(agent)
    parts = ["<h1>Промпты агентов</h1><p class=\"lead\">Здесь видно, по каким инструкциям работает каждый агент, "
             "и их можно поправить. Изменения применяются к следующим заданиям без перезапуска. "
             "Данные компании, результаты предыдущих этапов и формат ответа (JSON-схема) подставляются автоматически.</p>"]
    if saved:
        parts.append(f'<div class="notice">Сохранено: {esc(prompts.REGISTRY[saved].title) if saved in prompts.REGISTRY else ""}</div>')
    if error:
        parts.append(f'<div class="notice error">{esc(error)}</div>')
    parts.append(_stage_map(routes))
    for group, specs in groups.items():
        parts.append(f"<h2>{esc(group)}</h2>")
        for spec in specs:
            override = overrides.get(spec.key)
            current = override["text"] if override else spec.default
            status = (f'<span class="tag changed">изменён {esc(override.get("updated_at", "")[:16].replace("T", " "))}</span>'
                      if override else '<span class="tag">по умолчанию</span>')
            used_by = ", ".join(agent_name(a) for a in agents_of.get(spec.key, []))
            holders = (f'<p class="muted">Обязательные подстановки (не удаляйте): '
                       f'{", ".join("{" + p + "}" for p in spec.placeholders)}</p>' if spec.placeholders else "")
            history = len(state["history"].get(spec.key, []))
            parts.append(f"""<div class="card" id="{esc(spec.key)}"><h3>{esc(spec.title)} {status}</h3>
<p class="muted">{esc(spec.description)}{(' Используют: ' + esc(used_by) + '.') if used_by else ''}
 Ключ: {esc(spec.key)}. Версий в истории: {history}.</p>{holders}
<form method="post" action="/studio/tune/prompts/{esc(spec.key)}">
<input type="hidden" name="revision" value="{state['revision']}">
<textarea name="text" rows="{min(28, max(6, len(current) // 110))}">{esc(current)}</textarea>
<div class="row"><button name="action" value="save">Сохранить</button>
{'<button class="plain" name="action" value="reset">Вернуть по умолчанию</button>' if override else ''}</div></form>
{f'<details><summary>Текст по умолчанию</summary><textarea readonly rows="8">{esc(spec.default)}</textarea></details>' if override else ''}
</div>""")
    return page("Промпты", "prompts", "".join(parts))


def _stage_map(routes: dict) -> str:
    """The content-package pipeline: order, inputs and models of every stage."""
    deps = article_production.STAGE_INPUTS
    rows = []
    for order, name in enumerate(article_production.STAGE_TITLES, 1):
        main, critic = routes.get(name, ([], []))
        inputs = ", ".join(agent_name(d) for d in deps.get(name, ())) or "только задание и данные компании"
        rows.append(f"<tr><td>{order}</td><td><a href=\"#article.rules.{esc(name)}\">{esc(agent_name(name))}</a></td>"
                    f"<td>{esc(inputs)}</td><td>{esc(' → '.join(main) or 'по умолчанию')}</td>"
                    f"<td>{esc(' → '.join(critic) or '—')}</td></tr>")
    return ("<h2>Как устроен контент-пакет</h2><p class=\"lead\">Агенты работают по очереди. Каждый получает "
            "задание, данные компании и результаты перечисленных этапов. Модели можно поменять на странице "
            "«Модели и критик».</p><div style=\"overflow-x:auto\"><table><tr><th>#</th><th>Агент</th>"
            "<th>Получает результаты</th><th>Модель (основная → запасные)</th><th>Критик</th></tr>"
            + "".join(rows) + "</table></div>")


@router.post("/studio/tune/prompts/{key}", dependencies=[Depends(require_studio_csrf)])
async def tune_save(key: str, request: Request, owner: str = Depends(require_tune_owner)):
    fields = await _studio_form(request)
    try:
        revision = int(fields.get("revision", "-1"))
        value = None if fields.get("action") == "reset" else fields.get("text", "")
        prompts.save(key, value, expected_revision=revision, actor=owner)
    except (prompts.PromptStoreError, ValueError) as exc:
        message = str(exc) if isinstance(exc, prompts.PromptStoreError) else "Обновите страницу и повторите."
        return RedirectResponse(f"/studio/tune?error={quote(message)}#{quote(key)}", status_code=303)
    return RedirectResponse(f"/studio/tune?saved={quote(key)}#{quote(key)}", status_code=303)


def _when(value: datetime | None) -> str:
    return value.strftime("%d.%m %H:%M") if value else "—"


def _duration(run: AgentRun) -> str:
    if run.started_at and run.finished_at:
        seconds = int((run.finished_at - run.started_at).total_seconds())
        return f"{seconds // 60} мин {seconds % 60} с" if seconds >= 60 else f"{seconds} с"
    return "—"


@router.get("/studio/tune/work", response_class=HTMLResponse)
def tune_work(db: Session = Depends(get_db), owner: str = Depends(require_tune_owner)):
    tasks = list(db.scalars(select(Task).where(Task.id.in_(select(AgentRun.task_id)))
                            .order_by(Task.created_at.desc()).limit(60)))
    names = {p.id: p.name for p in db.scalars(select(Product).where(Product.id.in_({t.product_id for t in tasks})))} if tasks else {}
    runs = defaultdict(list)
    if tasks:
        for run in db.scalars(select(AgentRun).where(AgentRun.task_id.in_([t.id for t in tasks]))):
            runs[run.task_id].append(run)
    rows = []
    for task in tasks:
        done = sum(r.status == "completed" for r in runs[task.id])
        status = task.status.value
        cls = "ok" if status == "completed" else ("bad" if status == "failed" else "")
        rows.append(f"<tr><td>{_when(task.created_at)}</td><td>{esc(names.get(task.product_id, ''))}</td>"
                    f"<td><a href=\"/studio/tune/work/{task.id}\">{esc(task.title[:90])}</a></td>"
                    f"<td><span class=\"tag {cls}\">{esc(STATUS.get(status, status))}</span></td>"
                    f"<td>{done} из {len(runs[task.id])}</td></tr>")
    body = ("<h1>Ход работы</h1><p class=\"lead\">Последние задания агентов. Откройте задание, чтобы увидеть каждый этап: "
            "кто работал, на какой модели, что получилось и что заметил критик.</p><div style=\"overflow-x:auto\"><table>"
            "<tr><th>Когда</th><th>Компания</th><th>Задание</th><th>Статус</th><th>Этапов готово</th></tr>"
            + ("".join(rows) or "<tr><td colspan=5>Заданий пока нет.</td></tr>") + "</table></div>")
    return page("Ход работы", "work", body)


def readable(value, depth: int = 0) -> str:
    """JSON output as headings and lists instead of code."""
    if isinstance(value, dict):
        if not value:
            return "<span class=\"muted\">—</span>"
        return "<dl>" + "".join(f"<dt>{esc(str(k).replace('_', ' '))}</dt><dd>{readable(v, depth + 1)}</dd>"
                                for k, v in value.items()) + "</dl>"
    if isinstance(value, list):
        if not value:
            return "<span class=\"muted\">—</span>"
        if all(not isinstance(v, (dict, list)) for v in value):
            return "<ul class=\"v\">" + "".join(f"<li>{esc(v)}</li>" for v in value) + "</ul>"
        return "".join(f"<div class=\"card\" style=\"margin:6px 0;padding:10px 12px\">{readable(v, depth + 1)}</div>"
                       for v in value)
    if value is None:
        return "<span class=\"muted\">не указано</span>"
    if isinstance(value, bool):
        return "да" if value else "нет"
    return esc(value)


@router.get("/studio/tune/work/{task_id}", response_class=HTMLResponse)
def tune_task(task_id: UUID, db: Session = Depends(get_db), owner: str = Depends(require_tune_owner)):
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Задание не найдено")
    product = db.get(Product, task.product_id)
    runs = sorted(db.scalars(select(AgentRun).where(AgentRun.task_id == task.id)),
                  key=lambda r: (r.created_at, r.started_at or r.created_at))
    artifacts = defaultdict(list)
    for row in db.scalars(select(Artifact).where(Artifact.task_id == task.id).order_by(Artifact.created_at)):
        artifacts[row.agent_run_id].append(row)
    costs = defaultdict(list)
    if runs:
        for row in db.scalars(select(CostRecord).where(CostRecord.agent_run_id.in_([r.id for r in runs]))):
            costs[row.agent_run_id].append(row)
    parts = [f"<h1>{esc(task.title)}</h1><p class=\"lead\">{esc(product.name if product else '')} · создано {_when(task.created_at)} · "
             f"статус: {esc(STATUS.get(task.status.value, task.status.value))}</p>"]
    request_row = next((a for a in artifacts.get(None, []) if a.kind in {"content_request", "creative_request"}), None)
    if request_row:
        try:
            parts.append("<div class=\"card\"><h3>Задание</h3>" + readable(json.loads(request_row.content)) + "</div>")
        except ValueError:
            pass
    for order, run in enumerate(runs, 1):
        status = run.status
        cls = "ok" if status == "completed" else ("bad" if status == "failed" else "")
        models = []
        for cost in costs.get(run.id, []):
            details = cost.details or {}
            stage = {"author": "автор", "critic": "критик", "revision": "доработка"}.get(details.get("llm_stage"), details.get("llm_stage") or "вызов")
            models.append(f"{stage}: {details.get('model') or run.model} ({cost.input_tokens or 0}+{cost.output_tokens or 0} ток.)")
        prompt_links = " · ".join(f'<a href="/studio/tune#{esc(k)}">{esc(prompts.REGISTRY[k].title)}</a>'
                                  for k in AGENT_PROMPTS.get(run.agent_name, ()) if k in prompts.REGISTRY)
        block = [f"<div class=\"card\"><h3>{order}. {esc(agent_name(run.agent_name))} "
                 f"<span class=\"tag {cls}\">{esc(STATUS.get(status, status))}</span></h3>"
                 f"<p class=\"muted\">Время: {_duration(run)}. Модели: {esc('; '.join(models) or run.model or '—')}</p>"]
        if prompt_links:
            block.append(f"<p class=\"muted\">Инструкции: {prompt_links}</p>")
        if run.error:
            block.append(f"<p class=\"tag bad\">Ошибка: {esc(run.error[:400])}</p>")
        for row in artifacts.get(run.id, []):
            try:
                data = json.loads(row.content)
            except ValueError:
                continue
            if row.kind == "content_stage_output":
                if data.get("fallback_used"):
                    block.append(f"<p class=\"tag changed\">Модель не справилась, использован запасной вариант: "
                                 f"{esc(data.get('fallback_reason') or '')}</p>")
                block.append("<details><summary>Результат этапа</summary>" + readable(data.get("output")) + "</details>")
            elif row.kind == "agent_quality_review":
                block.append(_review_html(data))
        block.append("</div>")
        parts.append("".join(block))
    if not runs:
        parts.append("<div class=\"card\">У этого задания нет этапов агентов.</div>")
    return page(task.title, "work", "".join(parts))


def _review_html(data: dict) -> str:
    status = data.get("status", "")
    findings = data.get("findings") or []
    decisions = {d.get("id"): d for d in data.get("decisions") or []}
    items = []
    for item in findings:
        decision = decisions.get(item.get("id"))
        verdict = ""
        if decision:
            verdict = (" — <b>принято</b>" if decision.get("decision") == "accept" else " — <b>отклонено</b>") + \
                      f": {esc(decision.get('rationale'))}"
        items.append(f"<div class=\"finding {esc(item.get('severity'))}\"><b>{esc(item.get('severity'))}</b>: "
                     f"{esc(item.get('problem'))}<br><span class=\"muted\">Где: {esc(item.get('evidence'))}</span><br>"
                     f"Предложение: {esc(item.get('suggestion'))}{verdict}</div>")
    models = ", ".join(m.get("model", "") for m in data.get("extra_usage_by_model") or [] if m.get("model"))
    if data.get("failure_reason"):
        items.append(f"<p class=\"muted\">Причина: {esc(data['failure_reason'])}</p>")
    return (f"<details{' open' if findings else ''}><summary>Критик: {esc(REVIEW_STATUS.get(status, status))}"
            f"{' · ' + esc(models) if models else ''}</summary>{''.join(items) or '<p class=muted>Замечаний нет.</p>'}</details>")


@router.get("/studio/tune/vk", response_class=HTMLResponse)
def vk_service_settings(result: str = "", owner: str = Depends(require_tune_owner),
                        settings: Settings = Depends(get_settings)):
    """Operator-only one-time VK read key setup; never return a saved key."""
    source = "переменных сервера" if settings.social_vk_service_token else "защищённого хранилища"
    configured = bool(platform_service_token(settings))
    note = {
        "saved": "Ключ сохранён в зашифрованном хранилище. Чтение стены и комментариев проверено.",
        "wall_only": "Ключ сохранён: чтение стены работает, но проверить комментарии пока нельзя — в указанном сообществе нет доступных записей.",
        "invalid": "ВКонтакте не подтвердил ключ, ссылку или доступ к стене/комментариям. Ключ не сохранялся.",
        "removed": "Ключ из зашифрованного хранилища удалён.",
        "blocked": "Настройка ключа управляется окружением сервера.",
        "app_saved": "Приложение сообщества VK сохранено. Проверьте реальный вход на собственной группе; клиентам кнопка пока скрыта.",
        "app_removed": "Параметры приложения VK и подтверждение его работы удалены.",
        "app_invalid": "Неверный ID или защищённый ключ приложения VK.",
        "app_blocked": "Приложение VK задаётся сервером, его нельзя заменить на этой странице.",
    }.get(result, "")
    header = (
        "<p class='notice'>Сервисный ключ настроен (источник: " + esc(source) + ").</p>"
        if configured else "<p class='notice error'>Сервисный ключ не настроен. Чтение комментариев VK недоступно.</p>"
    )
    form = (
        "<p class='muted'>Этот ключ управляется через конфигурацию сервера и не может изменяться здесь.</p>"
        if settings.social_vk_service_token else """
<form action="/studio/tune/vk" method="post">
 <p>Вставьте сервисный ключ приложения ВКонтакте и ссылку на открытое сообщество с публикациями.
 Система проверит чтение стены и комментариев, затем сохранит ключ зашифрованным. Значение ключа после сохранения не показывается.</p>
 <label>Сервисный ключ приложения VK
 <input name="token" type="password" autocomplete="off" maxlength="8000" required></label>
 <label>Сообщество для проверки
 <input name="community_link" type="text" maxlength="255"
        placeholder="https://vk.com/my_community" required></label>
 <div class="row"><button type="submit">Проверить и сохранить</button></div>
</form>
<form method="post" action="/studio/tune/vk/remove">
 <label><input type="checkbox" name="confirm" value="yes" required>
 Я подтверждаю отключение платформенного ключа VK.</label>
 <button class="plain" type="submit">Отключить ключ</button>
</form>
"""
    )
    app_creds = group_app_credentials(settings)
    app_source_locked = bool(settings.social_vk_community_app_id or settings.social_vk_community_app_secret)
    is_verified = group_app_verified(settings)
    app_status = (
        "<p class='notice'>Авторизация сообщества проверена настоящим подключением. Клиенты могут подключаться через окно VK.</p>"
        if is_verified else
        ("<p class='notice'>Приложение сохранено, но реальный вход VK ещё не проверен. "
         "Кнопка доступна только владельцу ServGo.</p>" if app_creds else
         "<p class='notice error'>Приложение для авторизации сообществ пока не задано.</p>")
    )
    app_form = (
        "<p>Параметры заданы в окружении сервера и не меняются из Studio.</p>"
        if app_source_locked else """
<form method="post" action="/studio/tune/vk/app">
 <label>ID приложения VK для авторизации сообщества
 <input type="text" name="app_id" inputmode="numeric" maxlength="20" autocomplete="off" required></label>
 <label>Защищённый ключ приложения (client_secret)
 <input type="password" name="app_secret" maxlength="500" autocomplete="off" required></label>
 <div class="row"><button type="submit">Сохранить приложение</button></div>
</form>
<form method="post" action="/studio/tune/vk/app/remove">
 <label><input type="checkbox" name="confirm" value="yes" required>
 Отключить вход через это приложение VK.</label>
 <button class="plain" type="submit">Удалить приложение</button>
</form>
"""
    )
    body = (
        "<h1>Подключение ВКонтакте</h1>"
        "<h2>Авторизация сообщества без ручного ключа</h2>"
        "<p class='lead'>Настраивается один раз для всей платформы ServGo. "
        "Клиенту останется вставить ссылку сообщества и подтвердить доступ в VK.</p>"
        + app_status + "<div class='card'>"
        "<p>Нужны ID и защищённый ключ <strong>классического OAuth-приложения для сообществ VK</strong>, "
        "не обычного VK ID. Проверьте разрешённый callback: "
        "<code>https://servgo.ru/studio/social/vk/group-callback</code>. "
        "VK может отказать новым приложениям в старом OAuth; тест до выпуска для клиентов обязателен.</p>"
        + app_form + "</div>"
        "<h2>Чтение комментариев ВКонтакте</h2>"

        "<p class='lead'>Только владелец ServGo может задать платформенный сервисный ключ. "
        "Он используется исключительно для чтения публичных записей и комментариев; "
        "публикации и ответы выполняются ключом конкретного сообщества.</p>"
        + header + (f"<p class='notice'>{esc(note)}</p>" if note else "")
        + "<div class='card'><p>Получите сервисный ключ в настройках собственного "
        "<a href='https://dev.vk.com/' rel='noopener noreferrer'>приложения ВКонтакте</a>. "
        "Не отправляйте ключ в чат или GitHub.</p>" + form + "</div>"
        "<p>После настройки вернитесь в страницу сообщества VK нужной компании и нажмите "
        "«Подключить чтение комментариев». Уже сохранённый ключ сообщества повторно вводить не нужно.</p>"
    )
    return page("Ключ VK API", "vk", body)


@router.post("/studio/tune/vk", dependencies=[Depends(require_studio_csrf)])
async def vk_service_save(request: Request, settings: Settings = Depends(get_settings),
                          owner: str = Depends(require_tune_owner)):
    fields = await _studio_form(request)
    token = fields.get("token", "")
    community_link = fields.get("community_link", "")
    if settings.social_vk_service_token:
        result = "blocked"
    else:
        try:
            _, _, comments_checked = await verify_platform_service_token(token, community_link)
            save_platform_service_token(settings, token)
            result = "saved" if comments_checked else "wall_only"
        except (ValueError, LookupError):
            result = "invalid"
    return RedirectResponse("/studio/tune/vk?result=" + result, status_code=303)


@router.post("/studio/tune/vk/remove", dependencies=[Depends(require_studio_csrf)])
async def vk_service_remove(request: Request, settings: Settings = Depends(get_settings),
                            owner: str = Depends(require_tune_owner)):
    fields = await _studio_form(request)
    if fields.get("confirm") != "yes":
        raise HTTPException(422, "Подтвердите отключение ключа VK")
    try:
        delete_platform_service_token(settings)
        result = "removed"
    except ValueError:
        result = "blocked"
    return RedirectResponse("/studio/tune/vk?result=" + result, status_code=303)

@router.post("/studio/tune/vk/app", dependencies=[Depends(require_studio_csrf)])
async def vk_group_app_save(request: Request, settings: Settings = Depends(get_settings),
                            owner: str = Depends(require_tune_owner)):
    fields = await _studio_form(request)
    try:
        save_group_app_credentials(settings,
                                   fields.get("app_id", ""), fields.get("app_secret", ""))
        result = "app_saved"
    except ValueError:
        result = "app_blocked" if (
            settings.social_vk_community_app_id or settings.social_vk_community_app_secret
        ) else "app_invalid"
    return RedirectResponse("/studio/tune/vk?result=" + result, status_code=303)


@router.post("/studio/tune/vk/app/remove", dependencies=[Depends(require_studio_csrf)])
async def vk_group_app_remove(request: Request, settings: Settings = Depends(get_settings),
                              owner: str = Depends(require_tune_owner)):
    fields = await _studio_form(request)
    if fields.get("confirm") != "yes":
        raise HTTPException(422, "Подтвердите отключение приложения VK")
    try:
        remove_group_app_credentials(settings)
        result = "app_removed"
    except ValueError:
        result = "app_blocked"
    return RedirectResponse("/studio/tune/vk?result=" + result, status_code=303)
