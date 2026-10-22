"""Add a company from Growth Studio and confirm its profile.

Reuses the company intake service (the same flow as the Telegram «Новая компания» button):
the owner's text, site pages, registry record, files and answers become intake sources,
the intake model drafts a profile, and nothing reaches the canonical profile until the
owner confirms it.
"""
from __future__ import annotations

import logging
import time
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.content_studio import (
    _owner_product, _studio_form, brand_html, esc, studio_page_shell,
)
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.company_profile import CompanyProfile, evaluate_readiness, load_company_profile
from app.core.config import get_settings
from app.db.session import get_db
from app.intake.company_admin import delete_company, rename_company
from app.intake.contracts import IntakeError
from app.intake.registry import registry_from_settings
from app.intake.service import CompanyIntakeService
from app.intake.sources import FILE_HELP, MAX_FILE_BYTES, parse_file_async
from app.intake.telegram import GATES, LABELS, render_review, value_text
from app.models.domain import Product

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])
ACTOR = "local_owner"  # Same actor as the JSON intake API, so sessions are shared.
_logger = logging.getLogger(__name__)
# Reading a site and drafting a profile take minutes; nginx cuts a request at 300 s.
# Slow work runs after the response, and the profile page polls this state.
_JOBS: dict[str, dict] = {}
JOB_STALE_SECONDS = 20 * 60
MAX_UPLOAD_FILES = 5
MAX_UPLOAD_BYTES = 25_000_000
FILE_ACCEPT = ".txt,.md,.json,.csv,.docx,.pdf,.xlsx,.xls,.jpg,.jpeg,.png,.webp,.tif,.tiff,.bmp"
SOURCE_LABELS = {
    "owner_message": "Ваши сведения",
    "owner_file": "Файл",
    "owned_website": "Сайт",
    "official_registry": "Реестр",
    "public_third_party": "Внешний источник",
    "existing_profile": "Текущий профиль",
}


def _require_operator(request: Request) -> None:
    # Company-scoped accounts cannot create companies.
    if getattr(request.state, "studio_product_ids", None) is not None:
        raise HTTPException(403, "Добавлять компании может только владелец")


def _service(db: Session) -> CompanyIntakeService:
    settings = get_settings()
    return CompanyIntakeService(db, products_root=settings.products_root, model=settings.llm_model,
                                provider_name=settings.llm_provider,
                                registry_lookup=registry_from_settings(settings))


async def _read_form(request: Request) -> tuple[dict[str, str], list[tuple[str, bytes]]]:
    """Plain forms keep the strict urlencoded parser; file forms are bounded multipart."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "multipart/form-data":
        return await _studio_form(request), []
    try:
        declared = int(request.headers.get("content-length", "0"))
    except ValueError:
        declared = 0
    if declared > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Файлы слишком большие: до 25 МБ за раз.")
    form = await request.form(max_files=MAX_UPLOAD_FILES, max_fields=80)
    fields, files, total = {}, [], 0
    for key, value in form.multi_items():
        if isinstance(value, str):
            fields[key] = value.strip()
            continue
        if not value.filename:
            continue
        data = await value.read(MAX_FILE_BYTES + 1)
        total += len(data)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Файлы слишком большие: до 25 МБ за раз.")
        files.append((value.filename, data))
    return fields, files


def _file_field(label: str = "Файлы <em>· необязательно</em>") -> str:
    return f"""<label class="sg-field"><span>{label}</span>
  <input name="files" type="file" multiple accept="{FILE_ACCEPT}">
  <small>Прайс, меню, презентация, скриншоты. До {MAX_UPLOAD_FILES} файлов за раз. {esc(FILE_HELP)}</small></label>"""


def _identifier_hint(service: CompanyIntakeService) -> str:
    return ("Проверим компанию в официальном реестре." if service.registry_lookup
            else "Сохраним в профиль; проверка в реестре пока не подключена.")


def _new_company_page(fields: dict | None = None, error: str | None = None, *, registry_hint: str = "") -> str:
    fields = fields or {}
    notice = f'<div class="sg-warning" role="alert">{esc(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Новая компания · Growth Studio</title><link rel="icon" type="image/svg+xml" href="/studio/assets/servgo.svg">
<link rel="stylesheet" href="/studio/assets/studio.css?v=14"></head>
<body><main class="product-home">
<section class="panel hero">{brand_html()}<h1>Новая компания</h1>
<p class="subtitle">Опишите бизнес своими словами и дайте всё, что уже есть: сайт, ИНН, прайс. Агенты соберут профиль компании, а вы проверите его перед работой.</p></section>
{notice}
<form class="panel sg-company-form sg-intake-form" method="post" action="/studio/new" enctype="multipart/form-data" data-once>
  <label class="sg-field"><span>Название компании</span>
    <input name="name" required minlength="2" maxlength="200" value="{esc(fields.get('name', ''))}"></label>
  <label class="sg-field"><span>Сайт <em>· если есть</em></span>
    <input name="website" type="text" inputmode="url" autocapitalize="none" autocorrect="off" spellcheck="false" maxlength="500" placeholder="nogotok.ru" value="{esc(fields.get('website', ''))}">
    <small>Прочитаем главную и до 7 страниц: услуги, цены, о компании, контакты, отзывы.</small></label>
  <label class="sg-field"><span>ИНН или ОГРН <em>· если есть</em></span>
    <input name="identifier" inputmode="numeric" maxlength="40" placeholder="10 или 12 цифр" value="{esc(fields.get('identifier', ''))}">
    <small>{esc(registry_hint)}</small></label>
  <label class="sg-field"><span>О компании</span>
    <textarea name="about" required minlength="30" maxlength="12000" rows="8"
      placeholder="Чем занимается, какие услуги и цены, для кого, город, чем отличается, чего нельзя обещать.">{esc(fields.get('about', ''))}</textarea></label>
  {_file_field()}
  <div class="sg-intake-actions"><button class="btn primary" type="submit">Добавить компанию</button>
  <a href="/studio?choose=1">← К списку проектов</a></div>
</form>{ONCE_SCRIPT}</main></body></html>"""

ONCE_SCRIPT = """<script>document.querySelectorAll('form[data-once]').forEach(function(f){f.addEventListener('submit',function(){
var b=f.querySelector('button[type=submit],button:not([type])');if(b){setTimeout(function(){b.disabled=true;b.textContent='Отправляю…';},0);}});});</script>"""


def normalize_site(value: str) -> str:
    """Owners type «nogotok.ru» or «www.nogotok.ru/prices»; the crawler needs a full address."""
    value = (value or "").strip()
    if value and not urlsplit(value).scheme:
        value = "https://" + value.lstrip("/")
    return value


def _background_session():
    from app.db.session import SessionLocal
    return SessionLocal()


def _job(session_id) -> dict | None:
    job = _JOBS.get(str(session_id))
    if job and job.get("running") and time.time() - job["started"] > JOB_STALE_SECONDS:
        job.update(running=False, error="Работа прервалась. Материалы сохранены; запустите сборку ещё раз.")
    return job


def _job_running(session_id) -> bool:
    job = _job(session_id)
    return bool(job and job.get("running"))


def _start_job(background: BackgroundTasks, product_id: UUID, session_id: UUID, *, website: str = "",
               identifier: str = "", build: bool = False, notes: list[str] | None = None) -> None:
    if _job_running(session_id):
        raise IntakeError("Уже работаю над этой компанией. Подождите, страница обновится сама.")
    stage = "Читаю сайт" if website else "Проверяю в реестре" if identifier else "Собираю профиль"
    _JOBS[str(session_id)] = {"running": True, "started": time.time(), "stage": stage,
                              "notes": list(notes or []), "error": None}
    background.add_task(_run_job, product_id, session_id, website, identifier, build)


async def _run_job(product_id: UUID, session_id: UUID, website: str, identifier: str, build: bool) -> None:
    job = _JOBS[str(session_id)]
    db = _background_session()
    try:
        service = _service(db)
        if website or identifier:
            job["notes"] += await _add_materials(service, product_id, session_id,
                                                 {"website": website, "identifier": identifier}, [],
                                                 text_reference="")
        if build:
            job["stage"] = "Собираю профиль"
            from app.core.composition import build_intake
            await build_intake(db, get_settings()).build(product_id, session_id, actor=ACTOR)
    except IntakeError as exc:
        db.rollback()
        job["error"] = str(exc)
    except ValueError:
        db.rollback()
        job["error"] = "Модель для разбора данных не настроена. Материалы сохранены."
    except Exception as exc:  # noqa: BLE001 - the owner must see a message, never a silent spinner.
        db.rollback()
        _logger.warning("company_intake_job_failed %s", type(exc).__name__)
        job["error"] = "Не удалось подготовить профиль. Материалы сохранены; попробуйте ещё раз."
    finally:
        job["running"] = False
        db.close()


@router.get("/studio/new", response_class=HTMLResponse)
def new_company_form(request: Request, db: Session = Depends(get_db)):
    _require_operator(request)
    return HTMLResponse(_new_company_page(registry_hint=_identifier_hint(_service(db))),
                        headers={"Cache-Control": "no-store"})


async def _add_materials(service: CompanyIntakeService, product_id: UUID, session_id: UUID,
                         fields: dict, files: list[tuple[str, bytes]], *, text_reference: str) -> list[str]:
    """Add every kind of material the owner gave; one failed source never discards the others."""
    notes: list[str] = []
    if fields.get("text", "").strip():
        service.add_text(product_id, session_id, fields["text"].strip(), actor=ACTOR, reference=text_reference)
    identifier = fields.get("identifier", "").strip()
    if identifier:
        state = await service.add_identifier(product_id, session_id, identifier, actor=ACTOR)
        registry = [s for s in state["sources"] if s["source_type"] == "official_registry"]
        if registry and registry[-1]["status"] == "read":
            notes.append("Компания найдена в реестре.")
        elif registry and registry[-1]["note"]:
            notes.append(registry[-1]["note"])
    website = fields.get("link", "").strip() or fields.get("website", "").strip()
    if website:
        before = {s["id"] for s in service.get(product_id, session_id, actor=ACTOR)["sources"]}
        try:
            state = await service.add_link(product_id, session_id, website, actor=ACTOR)
        except IntakeError as exc:
            notes.append(str(exc))
        else:
            pages = [s for s in state["sources"] if s["source_type"] == "owned_website" and s["id"] not in before]
            readable = [s for s in pages if s["status"] != "unavailable"]
            if readable:
                notes.append(f"Прочитано страниц сайта: {len(readable)}.")
            notes.extend(dict.fromkeys(s["note"] for s in pages if s["note"] and s["status"] != "read"))
    for filename, data in files:
        source = await parse_file_async(filename, data, f"growth studio: файл {filename}"[:500])
        try:
            service.add_source(product_id, session_id, source, actor=ACTOR)
        except IntakeError as exc:
            notes.append(str(exc))
            break
        if source.status != "read" and source.note:
            notes.append(f"{filename}: {source.note}")
    return list(dict.fromkeys(notes))


@router.post("/studio/new")
async def new_company_submit(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    _require_operator(request)
    fields, files = await _read_form(request)
    name = " ".join(fields.get("name", "").split())
    about = fields.get("about", "").strip()
    website = normalize_site(fields.get("website", ""))
    identifier = fields.get("identifier", "").strip()
    service = _service(db)
    if not 2 <= len(name) <= 200 or not 30 <= len(about) <= 12000 or len(website) > 500 or len(identifier) > 40:
        return HTMLResponse(_new_company_page(fields, "Укажите название (2–200 символов) и описание компании (от 30 символов).",
                                              registry_hint=_identifier_hint(service)),
                            status_code=422, headers={"Cache-Control": "no-store"})
    product = Product(name=name, slug="company-" + uuid4().hex[:12], description=about[:10000])
    db.add(product)
    db.commit()
    try:
        session = service.start(product.id, actor=ACTOR)
        session_id = UUID(session["session_id"])
        service.add_text(product.id, session_id, about, actor=ACTOR, reference="growth studio: описание владельца")
        # Uploaded files are already here; the site, registry and draft follow in the background.
        notes = await _add_materials(service, product.id, session_id, {}, files, text_reference="")
        _start_job(background, product.id, session_id, website=website, identifier=identifier, build=True,
                   notes=notes)
    except IntakeError as exc:
        db.rollback()
        return RedirectResponse(f"/studio/products/{product.id}/profile?error={quote(str(exc)[:200])}", status_code=303)
    return RedirectResponse(f"/studio/products/{product.id}/profile", status_code=303)


def _editable_fields() -> list[tuple[str, str, str]]:
    """Profile fields the owner can type in directly: plain text and lists of lines."""
    rows = []
    for path, label in LABELS.items():
        section, name = path.split(".")
        annotation = str(getattr(CompanyProfile.model_fields[section].default_factory.model_fields[name], "annotation", ""))
        if annotation in {"str | None", "<class 'str'>"}:
            rows.append((path, label, "text"))
        elif annotation in {"list[str]", "list[str] | None"}:
            rows.append((path, label, "lines"))
    return rows


EDITABLE_FIELDS = _editable_fields()


def _current_value(data: dict, path: str):
    section, name = path.split(".")
    return (data.get(section) or {}).get(name)


def _confirmed_profile(settings, product: Product):
    result = load_company_profile(settings.products_root, product.slug)
    return result.profile if not result.validation_errors else None


def _profile_summary_html(base: str, profile: CompanyProfile | None) -> str:
    if profile is None:
        return f"""<section class="sg-surface sg-intake-section"><h2>Профиль компании</h2>
<p>Подтверждённого профиля пока нет. Соберите его из материалов или заполните вручную.</p>
<div class="sg-intake-actions"><a class="btn" href="{base}/profile/edit">Заполнить вручную</a></div></section>"""
    data = profile.model_dump(mode="json")
    rows = []
    for path, label in LABELS.items():
        value = _current_value(data, path)
        if value in (None, [], ""):
            continue
        rows.append(f"<dt>{esc(label)}</dt><dd>{esc(value_text(value))}</dd>")
    readiness = evaluate_readiness(profile)
    gates = " · ".join(f"{GATES[g]}: {'готово' if readiness[g]['ready'] else 'не хватает данных'}" for g in GATES)
    return f"""<section class="sg-surface sg-intake-section"><h2>Профиль компании</h2>
<dl class="sg-profile-list">{''.join(rows) or '<dt>Пока пусто</dt><dd>Заполните вручную или соберите из материалов.</dd>'}</dl>
<p class="sg-muted">{esc(gates)}</p>
<div class="sg-intake-actions"><a class="btn primary" href="{base}/profile/edit">Изменить данные</a></div></section>"""


def _company_settings_html(product: Product, *, can_delete: bool) -> str:
    base = f"/studio/products/{product.id}"
    delete_link = (f'<a class="btn danger" href="{base}/profile/delete">Удалить компанию</a>' if can_delete else "")
    return f"""<section class="sg-surface sg-intake-section"><h2>Компания</h2>
<form method="post" action="{base}/profile/rename" class="sg-intake-form" data-once>
  <label class="sg-field"><span>Название</span><input name="name" required minlength="2" maxlength="200" value="{esc(product.name)}"></label>
  <label class="sg-field"><span>Краткое описание</span><textarea name="description" rows="3" maxlength="10000">{esc(product.description or '')}</textarea></label>
  <div class="sg-intake-actions"><button class="btn">Сохранить название</button>{delete_link}</div></form></section>"""


def _edit_page_body(product: Product, profile: CompanyProfile | None, error: str | None) -> str:
    base = f"/studio/products/{product.id}"
    data = profile.model_dump(mode="json") if profile else {}
    fields = []
    for path, label, kind in EDITABLE_FIELDS:
        value = _current_value(data, path)
        if kind == "lines":
            text, hint = "\n".join(value or []), "<small>Каждый пункт с новой строки.</small>"
            control = f'<textarea name="f_{path}" rows="{max(2, min(8, len(value or []) + 1))}" maxlength="4000">{esc(text)}</textarea>'
        else:
            hint = ""
            control = f'<input name="f_{path}" maxlength="1000" value="{esc(value or "")}">'
        fields.append(f'<label class="sg-field"><span>{esc(label)}</span>{control}{hint}</label>')
    notice = f'<div class="sg-warning" role="alert">{esc(error)}</div>' if error else ""
    return f"""{notice}<form method="post" action="{base}/profile/edit" class="sg-surface sg-intake-section sg-intake-form" data-once>
<p class="sg-muted">Меняйте только нужное: пустое поле очищает значение. Услуги, цены и подтверждённые факты добавляйте через «Добавить материалы» на странице профиля.</p>
{''.join(fields)}
<div class="sg-intake-actions"><button class="btn primary" type="submit">Сохранить</button>
<a href="{base}/profile">Отмена</a></div></form>{ONCE_SCRIPT}"""


def _source_line(source: dict, base: str = "", removable: bool = False) -> str:
    label = SOURCE_LABELS.get(source.get("source_type"), "Материал")
    name = source.get("filename") or source.get("reference") or ""
    if name == "company identifier":
        name = "ИНН или название"
    status = source.get("status")
    mark = {"read": "", "truncated": " · прочитан частично", "unavailable": " · не прочитан",
            "unsupported": " · формат не читается"}.get(status, "")
    note = f'<small>{esc(source["note"])}</small>' if source.get("note") else ""
    remove = ""
    if removable and source.get("source_type") != "existing_profile":
        remove = (f'<form method="post" action="{base}/profile/remove" class="sg-inline-form">'
                  f'<input type="hidden" name="source_id" value="{esc(source.get("id", ""))}">'
                  f'<button class="btn small" aria-label="Убрать материал">Убрать</button></form>')
    return (f'<li class="sg-intake-source sg-intake-{esc(status or "read")}"><b>{esc(label)}</b>'
            f'<span>{esc(name)}{esc(mark)}</span>{note}{remove}</li>')


def _questions_html(base: str, review: dict | None) -> str:
    questions = (review or {}).get("questions") or []
    if not questions:
        return ""
    items = []
    for index, question in enumerate(questions):
        items.append(f"""<fieldset class="sg-intake-question">
  <legend>{esc(question['text'])}</legend>
  <input type="hidden" name="q{index}_field" value="{esc(question['field_path'])}">
  <label class="sg-field"><span class="sg-visually-hidden">Ваш ответ</span>
    <textarea name="q{index}_answer" rows="2" maxlength="4000" placeholder="Ответьте своими словами"></textarea></label>
  <label class="sg-check"><input type="checkbox" name="q{index}_unknown" value="1"> Не знаю — оставить пустым</label>
</fieldset>""")
    return f"""<section class="sg-surface sg-intake-section"><h2>Чего не хватает</h2>
<p class="sg-muted">Ответьте, что знаете. Агенты не будут придумывать недостающее.</p>
<form method="post" action="{base}/profile/answers" class="sg-intake-form" data-once>{''.join(items)}
  <div class="sg-intake-actions"><button class="btn primary">Ответить и пересобрать профиль</button></div></form></section>"""


def _working_html(job: dict) -> str:
    seconds = int(time.time() - job["started"])
    return f"""<section class="sg-surface sg-intake-section sg-intake-working" role="status" aria-live="polite">
<meta http-equiv="refresh" content="5">
<h2><span class="sg-intake-spinner" aria-hidden="true"></span>{esc(job['stage'])}…</h2>
<p>Обычно это 1–3 минуты. Страница обновится сама — её можно закрыть и вернуться позже.</p>
<p class="sg-muted">Идёт {seconds // 60} мин {seconds % 60:02d} с.</p></section>"""


def _profile_body(product: Product, state: dict | None, error: str | None, notice: str | None,
                  registry_hint: str, job: dict | None = None, profile: CompanyProfile | None = None,
                  can_delete: bool = False) -> str:
    base = f"/studio/products/{product.id}"
    parts = []
    running = bool(job and job.get("running"))
    if error:
        parts.append(f'<div class="sg-warning" role="alert">{esc(error)}</div>')
    if notice:
        parts.append(f'<div class="sg-intake-notice" role="status">{esc(notice)}</div>')
    if state is None:
        parts.append(_profile_summary_html(base, profile))
        parts.append(f"""<section class="sg-surface sg-intake-section"><h2>Дополнить из материалов</h2>
<p>Добавьте сайт, ИНН, файлы или сведения своими словами: агент предложит изменения, а вы проверите их.</p>
<form method="post" action="{base}/profile/start" class="sg-intake-actions"><button class="btn">Добавить материалы</button></form></section>""")
        parts.append(_company_settings_html(product, can_delete=can_delete))
        return "".join(parts)
    sources = "".join(_source_line(s, base, removable=not running) for s in state["sources"])
    if running:
        parts.append(_working_html(job))
    parts.append(f"""<section class="sg-surface sg-intake-section"><h2>Материалы · {len(state['sources'])}</h2>
<ul class="sg-intake-sources">{sources or '<li>Пока ничего не добавлено.</li>'}</ul></section>""")
    if running:
        return "".join(parts)  # No new material or rebuild while the current work is in progress.
    review = state.get("review")
    current = bool(review and state.get("review_current"))
    if current:
        parts.append(f"""<section class="sg-surface sg-intake-section"><h2>Черновик профиля</h2>
<div class="sg-intake-review">{esc(render_review(review))}</div>
<form method="post" action="{base}/profile/approve" class="sg-intake-actions"><input type="hidden" name="review_id" value="{esc(review['review_id'])}">
<button class="btn primary">Подтвердить профиль</button></form></section>""")
    else:
        stale = ("Материалы изменились после прошлого черновика — соберите профиль заново."
                 if review else "Агент изучит материалы и подготовит профиль.")
        parts.append(f"""<section class="sg-surface sg-intake-section"><h2>Черновик профиля</h2>
<p>{stale} Это занимает 1–3 минуты.</p>
<form method="post" action="{base}/profile/build" class="sg-intake-actions" data-once><button class="btn primary">Собрать профиль</button></form></section>""")
    # Questions come from the latest draft; answering adds evidence and rebuilds.
    parts.append(_questions_html(base, review))
    parts.append(f"""<section class="sg-surface sg-intake-section"><h2>Добавить материалы</h2>
<form method="post" action="{base}/profile/source" enctype="multipart/form-data" class="sg-intake-form" data-once>
  <label class="sg-field"><span>Сведения своими словами</span><textarea name="text" rows="4" maxlength="12000"></textarea></label>
  <label class="sg-field"><span>Сайт или страница</span><input name="link" type="text" inputmode="url" autocapitalize="none" autocorrect="off" spellcheck="false" maxlength="500" placeholder="nogotok.ru">
    <small>С главной прочитаем и внутренние страницы: услуги, цены, контакты.</small></label>
  <label class="sg-field"><span>ИНН или ОГРН</span><input name="identifier" inputmode="numeric" maxlength="40" placeholder="10 или 12 цифр">
    <small>{esc(registry_hint)}</small></label>
  {_file_field("Файлы")}
  <div class="sg-intake-actions"><button class="btn">Добавить</button></div></form></section>""")
    parts.append(f"""<section class="sg-surface sg-intake-section"><h2>Передумали?</h2>
<p class="sg-muted">Отмена закроет текущий сбор. Подтверждённый профиль останется как есть.</p>
<form method="post" action="{base}/profile/cancel" class="sg-intake-actions"><button class="btn">Отменить сбор данных</button></form></section>""")
    parts.append(_company_settings_html(product, can_delete=can_delete))
    parts.append(ONCE_SCRIPT)
    return "".join(parts)


def running_job(job: dict | None) -> bool:
    return bool(job and job.get("running"))


def _render_profile(product: Product, db: Session, error: str | None = None, status_code: int = 200,
                    notice: str | None = None, request: Request | None = None):
    service = _service(db)
    active = service.active(product.id, ACTOR)
    state = service.get(product.id, active.id, actor=ACTOR) if active else None
    job = _job(active.id) if active else None
    if job and not job.get("running"):
        # Show what the finished background work found or why it failed, once.
        _JOBS.pop(str(active.id), None)
        if job.get("error"):
            error = " ".join(filter(None, [error, job["error"]]))
        if job.get("notes"):
            notice = " ".join(filter(None, [notice, " ".join(dict.fromkeys(job["notes"]))]))[:600]
    page = studio_page_shell(product, active="profile", title="Профиль компании",
                             subtitle="Подтверждённые сведения, на которые опираются все агенты.",
                             body=_profile_body(product, state, error, notice, _identifier_hint(service), job,
                                                profile=_confirmed_profile(get_settings(), product),
                                                can_delete=not running_job(job) and (
                                                    request is None or getattr(request.state, "studio_product_ids", None) is None)))
    return HTMLResponse(page, status_code=status_code, headers={"Cache-Control": "no-store"})


@router.get("/studio/products/{product_id}/profile", response_class=HTMLResponse)
def company_profile(product_id: UUID, request: Request, error: str | None = None, notice: str | None = None,
                    db: Session = Depends(get_db)):
    return _render_profile(_owner_product(db, product_id), db, error[:300] if error else None,
                           notice=notice[:400] if notice else None, request=request)


def _form_page(product: Product, *, title: str, subtitle: str, body: str, status_code: int = 200) -> HTMLResponse:
    page = studio_page_shell(product, active="profile", title=title, subtitle=subtitle, body=body)
    return HTMLResponse(page, status_code=status_code, headers={"Cache-Control": "no-store"})


def _edit_page(product: Product, db: Session, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    return _form_page(product, title="Изменить данные компании",
                      subtitle="Правки сохраняются сразу и попадают в историю.",
                      body=_edit_page_body(product, _confirmed_profile(get_settings(), product), error),
                      status_code=status_code)


@router.get("/studio/products/{product_id}/profile/edit", response_class=HTMLResponse)
def company_profile_edit(product_id: UUID, db: Session = Depends(get_db)):
    return _edit_page(_owner_product(db, product_id), db)


def _delete_page(product: Product, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    base = f"/studio/products/{product.id}"
    notice = f'<div class="sg-warning" role="alert">{esc(error)}</div>' if error else ""
    body = f"""{notice}<form method="post" action="{base}/profile/delete" class="sg-surface sg-intake-section sg-intake-form sg-danger-zone" data-once>
<p>Будут безвозвратно удалены профиль, материалы, черновики, задачи, комментарии и готовые файлы компании «{esc(product.name)}».
Если по ней уже что-то опубликовано, публикации на площадках останутся — удалятся только записи о них здесь.</p>
<label class="sg-field"><span>Чтобы подтвердить, введите название компании</span>
<input name="confirm_name" required autocomplete="off" placeholder="{esc(product.name)}"></label>
<div class="sg-intake-actions"><button class="btn danger" type="submit">Удалить компанию навсегда</button>
<a href="{base}/profile">Не удалять</a></div></form>{ONCE_SCRIPT}"""
    return _form_page(product, title="Удалить компанию", subtitle="Это действие нельзя отменить.",
                      body=body, status_code=status_code)


@router.get("/studio/products/{product_id}/profile/delete", response_class=HTMLResponse)
def company_delete_page(product_id: UUID, request: Request, db: Session = Depends(get_db)):
    _require_operator(request)
    return _delete_page(_owner_product(db, product_id))


def _session_id(service: CompanyIntakeService, product: Product) -> UUID:
    active = service.active(product.id, ACTOR)
    if active is None:
        raise IntakeError("Сначала начните сбор данных.")
    return active.id


async def _apply_answers(service: CompanyIntakeService, product: Product, session_id: UUID, fields: dict) -> bool:
    answered = False
    for index in range(10):
        field_path = fields.get(f"q{index}_field")
        if not field_path:
            continue
        if fields.get(f"q{index}_unknown"):
            service.answer_unknown(product.id, session_id, field_path, actor=ACTOR)
            answered = True
        elif fields.get(f"q{index}_answer", "").strip():
            service.answer_question(product.id, session_id, field_path, fields[f"q{index}_answer"], actor=ACTOR)
            answered = True
    return answered


@router.post("/studio/products/{product_id}/profile/{action}")
async def company_profile_action(product_id: UUID, action: str, request: Request, background: BackgroundTasks,
                                 db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    fields, files = await _read_form(request)
    service = _service(db)
    notes: list[str] = []
    try:
        if action == "start":
            service.start(product.id, actor=ACTOR)
        elif action == "source":
            session_id = _session_id(service, product)
            if _job_running(session_id):
                raise IntakeError("Уже работаю над этой компанией. Подождите, страница обновится сама.")
            notes = await _add_materials(service, product.id, session_id,
                                         {"text": fields.get("text", "")}, files,
                                         text_reference="growth studio: сведения владельца")
            website, identifier = normalize_site(fields.get("link", "")), fields.get("identifier", "").strip()
            if website or identifier:
                _start_job(background, product.id, session_id, website=website, identifier=identifier, notes=notes)
                notes = []
        elif action == "answers":
            session_id = _session_id(service, product)
            if _job_running(session_id):
                raise IntakeError("Уже работаю над этой компанией. Подождите, страница обновится сама.")
            if not await _apply_answers(service, product, session_id, fields):
                raise IntakeError("Ответьте хотя бы на один вопрос или отметьте «Не знаю».")
            _start_job(background, product.id, session_id, build=True)
        elif action == "build":
            session_id = _session_id(service, product)
            _start_job(background, product.id, session_id, build=True)
        elif action == "approve":
            session_id = _session_id(service, product)
            service.approve(product.id, session_id, UUID(fields.get("review_id", "")), actor=ACTOR, confirmed=True)
        elif action == "remove":
            session_id = _session_id(service, product)
            if _job_running(session_id):
                raise IntakeError("Уже работаю над этой компанией. Подождите, страница обновится сама.")
            service.remove_source(product.id, session_id, fields.get("source_id", ""), actor=ACTOR)
            notes = ["Материал убран. Пересоберите профиль."]
        elif action == "cancel":
            session_id = _session_id(service, product)
            if _job_running(session_id):
                raise IntakeError("Уже работаю над этой компанией. Подождите, страница обновится сама.")
            service.cancel(product.id, session_id, actor=ACTOR)
            notes = ["Сбор данных отменён."]
        elif action == "rename":
            rename_company(db, product, name=fields.get("name", ""), description=fields.get("description", ""))
            notes = ["Название и описание сохранены."]
        elif action == "edit":
            return _save_profile_edit(product, service, db, fields)
        elif action == "delete":
            _require_operator(request)
            active = service.active(product.id, ACTOR)
            if active and _job_running(active.id):
                raise IntakeError("Идёт сбор данных. Дождитесь окончания и повторите.")
            if " ".join(fields.get("confirm_name", "").split()).casefold() != product.name.casefold():
                return _delete_page(product, "Название не совпало. Введите его точно так, как оно написано выше.", 422)
            if active:
                _JOBS.pop(str(active.id), None)
            delete_company(db, get_settings().products_root, product.id)
            return RedirectResponse("/studio?choose=1", status_code=303)
        else:
            raise HTTPException(404, "Неизвестное действие")
    except (IntakeError, LookupError) as exc:
        db.rollback()
        message = str(exc) if isinstance(exc, IntakeError) else "Сбор данных не найден."
        return _render_profile(product, db, message, status_code=409)
    except ValueError:
        db.rollback()
        return _render_profile(product, db, "Не удалось подготовить профиль. Материалы сохранены.", status_code=503)
    suffix = f"?notice={quote(' '.join(notes)[:400])}" if notes else ""
    return RedirectResponse(f"/studio/products/{product.id}/profile{suffix}", status_code=303)


def _save_profile_edit(product: Product, service: CompanyIntakeService, db: Session, fields: dict):
    profile = _confirmed_profile(get_settings(), product)
    current = profile.model_dump(mode="json") if profile else {}
    changes = {}
    for path, _label, kind in EDITABLE_FIELDS:
        if f"f_{path}" not in fields:
            continue
        raw = fields[f"f_{path}"]
        if kind == "lines":
            new = [line.strip() for line in raw.splitlines() if line.strip()]
            old = _current_value(current, path)
            if new != (old or []):
                changes[path] = new if new or old is not None else old
        else:
            new = raw.strip() or None
            if new != (_current_value(current, path) or None):
                changes[path] = new
    if not changes:
        return RedirectResponse(f"/studio/products/{product.id}/profile?notice={quote('Ничего не изменилось.')}", status_code=303)
    try:
        service.edit_profile(product.id, changes, actor=ACTOR)
    except IntakeError as exc:
        db.rollback()
        return _edit_page(product, db, str(exc), 409)
    return RedirectResponse(f"/studio/products/{product.id}/profile?notice={quote('Данные компании сохранены.')}",
                            status_code=303)
