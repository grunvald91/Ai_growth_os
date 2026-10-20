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
from app.core.config import get_settings
from app.db.session import get_db
from app.intake.contracts import IntakeError
from app.intake.registry import registry_from_settings
from app.intake.service import CompanyIntakeService
from app.intake.sources import FILE_HELP, MAX_FILE_BYTES, parse_file_async
from app.intake.telegram import render_review
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


def _source_line(source: dict) -> str:
    label = SOURCE_LABELS.get(source.get("source_type"), "Материал")
    name = source.get("filename") or source.get("reference") or ""
    if name == "company identifier":
        name = "ИНН или название"
    status = source.get("status")
    mark = {"read": "", "truncated": " · прочитан частично", "unavailable": " · не прочитан",
            "unsupported": " · формат не читается"}.get(status, "")
    note = f'<small>{esc(source["note"])}</small>' if source.get("note") else ""
    return (f'<li class="sg-intake-source sg-intake-{esc(status or "read")}"><b>{esc(label)}</b>'
            f'<span>{esc(name)}{esc(mark)}</span>{note}</li>')


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
                  registry_hint: str, job: dict | None = None) -> str:
    base = f"/studio/products/{product.id}"
    parts = []
    running = bool(job and job.get("running"))
    if error:
        parts.append(f'<div class="sg-warning" role="alert">{esc(error)}</div>')
    if notice:
        parts.append(f'<div class="sg-intake-notice" role="status">{esc(notice)}</div>')
    if state is None:
        parts.append(f"""<section class="sg-surface"><h2>Сбор данных о компании</h2>
<p>Чтобы агенты писали точно про этот бизнес, соберите и подтвердите его профиль.</p>
<form method="post" action="{base}/profile/start"><button class="btn primary">Начать сбор данных</button></form></section>""")
        return "".join(parts)
    sources = "".join(_source_line(s) for s in state["sources"])
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
    parts.append(ONCE_SCRIPT)
    return "".join(parts)


def _render_profile(product: Product, db: Session, error: str | None = None, status_code: int = 200,
                    notice: str | None = None):
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
                             body=_profile_body(product, state, error, notice, _identifier_hint(service), job))
    return HTMLResponse(page, status_code=status_code, headers={"Cache-Control": "no-store"})


@router.get("/studio/products/{product_id}/profile", response_class=HTMLResponse)
def company_profile(product_id: UUID, error: str | None = None, notice: str | None = None,
                    db: Session = Depends(get_db)):
    return _render_profile(_owner_product(db, product_id), db, error[:300] if error else None,
                           notice=notice[:400] if notice else None)


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
