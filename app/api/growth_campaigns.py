"""Growth V4: safe company-scoped campaign draft editor and read-only details.

Launch uses the existing preparation service: no direct messages or publication.
Queued AI tasks can consume model budget. Stop/finalize need a worker-safe API.
"""
from __future__ import annotations

import html
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.content_studio import _owner_product, _studio_form, studio_page_shell
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.api.schemas import ExperimentCreate
from app.core.config import get_settings
from app.db.session import get_db
from app.models.domain import Experiment
from app.services.experiments import ExperimentService
from app.services.experiment_launch import ExperimentLaunchError, ExperimentLaunchService

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])
FIELDS = [
    ("experiment_key", "Код эксперимента (EXP-...)"),
    ("name", "Название"), ("hypothesis", "Гипотеза"), ("audience", "Аудитория"),
    ("channel", "Канал"), ("action", "План действий"),
    ("target_metric", "Основной показатель"),
    ("attribution_method", "Как измеряем результат"),
    ("baseline_summary", "Исходные показатели"),
    ("observation_window_days", "Период наблюдения, дней"),
    ("time_budget_minutes", "Время команды, минут"),
    ("success_condition", "Условие успеха"),
    ("stop_condition", "Условие остановки"),
    ("inconclusive_condition", "Когда результат не определён"),
    ("budget", "Бюджет"), ("currency", "Валюта (RUB, USD)"),
]
TEXT_FIELDS = {
    "hypothesis", "audience", "action", "attribution_method", "baseline_summary",
    "success_condition", "stop_condition", "inconclusive_condition",
}
INT_FIELDS = {"observation_window_days", "time_budget_minutes"}
BAD_FIELDS = (
    "Проверьте обязательные поля: код EXP-, положительные сроки, "
    "неотрицательный бюджет и трёхбуквенную валюту."
)


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def find_campaign(db: Session, product_id: UUID, experiment_id: UUID, *, lock=False) -> Experiment:
    query = select(Experiment).where(Experiment.id == experiment_id,
                                     Experiment.product_id == product_id)
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, "Кампания не найдена у выбранной компании")
    return row


def read_fields(values: dict) -> ExperimentCreate:
    normalized = {key: values.get(key, "") for key, _ in FIELDS}
    for key in INT_FIELDS:
        normalized[key] = int(normalized[key])
    return ExperimentCreate.model_validate(normalized)


# Owner-facing language is derived from persisted data, not from fabricated promises.
CAMPAIGN_STATUS = {
    "draft": "Планируется",
    "running": "Подготовка запущена",
    "completed": "Завершена",
    "stopped": "Остановлена",
    "cancelled": "Отменена",
    "paused": "Приостановлена",
}
OWNER_FACTS = (
    ("Что хотим улучшить", "target_metric"),
    ("Что сделаем", "action"),
    ("Что проверим", "hypothesis"),
    ("Какой результат считаем успешным", "success_condition"),
    ("Как измерим", "attribution_method"),
)
SUMMARY_KEYS = {"target_metric", "action", "success_condition"}
DETAIL_KEYS = {key for _, key in OWNER_FACTS} | {"name"}


def campaign_ready(row: Experiment) -> bool:
    """Use the same readiness contract as the actual launch service."""
    try:
        ExperimentLaunchService._validate_contract(row)
    except (ValueError, TypeError):
        return False
    return True


def campaign_status(row: Experiment) -> str:
    return CAMPAIGN_STATUS.get(row.status, "Статус уточняется")


def campaign_facts(row: Experiment, *, compact: bool = False) -> str:
    items = [(label, key) for label, key in OWNER_FACTS
             if not compact or key in SUMMARY_KEYS]
    return '<dl class="campaign-details">' + "".join(
        f'<div><dt>{esc(label)}</dt><dd>{esc(getattr(row, key) or "Пока не определено")}</dd></div>'
        for label, key in items
    ) + '</dl>'


def campaign_owner_step(row: Experiment) -> str:
    """A single truthful next step. External actions are not inferred."""
    if row.status == "draft":
        return ("Проверьте условия и запустите подготовку."
                if campaign_ready(row) else
                "Дополните условия кампании перед запуском.")
    if row.status == "running":
        return ("Пока ждём результат внутренней подготовки. Подключения и "
                "согласования для этой кампании ещё не отслеживаются автоматически.")
    if row.status == "completed":
        return "Посмотрите записанные результаты и выводы, если они уже доступны."
    if row.status in {"stopped", "cancelled", "paused"}:
        return "Новые действия здесь недоступны; сохранённые данные можно просмотреть."
    return "Проверьте сведения о кампании."


def campaign_list_card(product_id: UUID, row: Experiment) -> str:
    base = f"/studio/products/{product_id}/campaigns/{row.id}"
    action = ("Дополнить план" if not campaign_ready(row) else "Проверить и запустить") if row.status == "draft" else (
        "Посмотреть ход подготовки" if row.status == "running" else "Открыть кампанию"
    )
    destination = f"{base}/edit" if row.status == "draft" and not campaign_ready(row) else base
    return (
        f'<article class="panel"><h2>{esc(row.name)}</h2>'
        f'<p><strong>{esc(campaign_status(row))}</strong></p>'
        + campaign_facts(row, compact=True)
        + f'<p><strong>От вас:</strong> {esc(campaign_owner_step(row))}</p>'
          f'<a class="btn primary" href="{esc(destination)}">{esc(action)}</a>'
          '</article>'
    )


def form_page(product, values, error="", *, row=None):
    base = f"/studio/products/{product.id}/campaigns"
    path = f"{base}/{row.id}/edit" if row else base + "/new"
    back = f"{base}/{row.id}" if row else base
    controls = []
    for key, label in FIELDS:
        value = esc(values.get(key, ""))
        if key in TEXT_FIELDS:
            control = f'<textarea name="{key}" rows="3" required>{value}</textarea>'
        else:
            field_type = "number" if key in INT_FIELDS or key == "budget" else "text"
            limits = ' min="1" step="1"' if key in INT_FIELDS else (
                ' min="0" step="0.0001"' if key == "budget" else ""
            )
            control = f'<input name="{key}" type="{field_type}" value="{value}"{limits} required>'
        controls.append(f'<label class="field">{esc(label)}{control}</label>')
    warning = f'<p role="alert">{esc(error)}</p>' if error else ""
    page = (
        f'<p><a href="{back}">← К кампаниям</a></p>{warning}'
        f'<form class="surface form-card" action="{path}" method="post">'
        + "".join(controls) +
        '<p>Сохраняется только черновик — без расходов, рассылок и публикаций.</p>'
        '<button class="btn blue" type="submit">Сохранить черновик</button></form>'
    )
    return studio_page_shell(
        product, active="campaigns",
        title="Редактировать кампанию" if row else "Новая кампания",
        subtitle="Цель, гипотеза, аудитория, бюджет и измеримый результат.",
        body=page,
    )


def campaign_page(product, row: Experiment, error: str = "", *, metrics=()):
    base = f"/studio/products/{product.id}/campaigns"
    detailed = "".join(
        f'<div><dt>{esc(label)}</dt><dd>{esc(getattr(row, key))}</dd></div>'
        for key, label in FIELDS if key not in DETAIL_KEYS
    )
    technical = (
        '<details><summary>Подробнее о плане и условиях</summary>'
        f'<dl class="campaign-details">{detailed}</dl></details>'
    )
    if row.status == "draft":
        if campaign_ready(row):
            owner_action = (
                f'<form action="{base}/{row.id}/launch" method="post">'
                '<button class="btn primary" type="submit">Запустить подготовку</button>'
                '<p>Запуск поставит задачу ИИ-команде и начнёт период наблюдения. '
                'Возможны расходы на ИИ. Рассылки и публикации не запускаются.</p></form>'
            )
        else:
            owner_action = (
                f'<a class="btn primary" href="{base}/{row.id}/edit">Дополнить план</a>'
            )
    elif row.status == "running":
        owner_action = (
            f'<p><a class="btn" href="/studio/products/{product.id}/work">Задания команды</a> '
            f'<a class="btn" href="/studio/products/{product.id}/channels">Подключения каналов</a> '
            f'<a class="btn" href="/studio/products/{product.id}/review">Материалы на проверку</a></p>'
        )
    else:
        owner_action = ""

    results = ''.join(
        f'<li><strong>{esc(item.name)}:</strong> {esc(item.value)} {esc(item.unit)}'
        f' <small>({esc(item.source)}, {esc(item.observed_at.date())})</small></li>'
        for item in metrics
    )
    results_section = (
        '<section class="surface form-card"><h2>Фактические результаты</h2>'
        + (f'<ul>{results}</ul>' if results else
           '<p>Измерений пока нет — оценить эффективность ещё нельзя.</p>')
        + (f'<p><strong>Записанный вывод:</strong> {esc(row.conclusion)}</p>'
           if row.conclusion else '')
        + '</section>'
    )
    warning = f'<p role="alert">{esc(error)}</p>' if error else ""
    page = (
        f'<p><a href="{base}">← Все кампании</a></p>'
        f'<section class="surface form-card"><h2>{esc(row.name)}</h2>'
        f'<p><strong>{esc(campaign_status(row))}</strong></p>'
        + warning + campaign_facts(row)
        + f'<h3>Что требуется от вас</h3><p>{esc(campaign_owner_step(row))}</p>'
        + owner_action + technical + '</section>'
        + results_section
    )
    return studio_page_shell(product, active="campaigns", title="Маркетинговая кампания",
                             subtitle="Цель, действия и фактический результат — без технических деталей.",
                             body=page)


def campaign_metrics(db: Session, product_id: UUID, experiment_id: UUID):
    """Only measurements belonging to both this company and this campaign."""
    from app.models.domain import Metric
    return list(db.scalars(
        select(Metric).where(Metric.product_id == product_id,
                             Metric.experiment_id == experiment_id)
        .order_by(Metric.observed_at.desc()).limit(12)
    ))


@router.get("/studio/products/{product_id}/campaigns/new", response_class=HTMLResponse)
def new_campaign(product_id: UUID, db: Session = Depends(get_db)):
    return form_page(_owner_product(db, product_id), {"currency": "RUB"})


@router.post("/studio/products/{product_id}/campaigns/new")
async def save_campaign(product_id: UUID, request: Request, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    values = await _studio_form(request)
    try:
        model = read_fields(values)
    except (ValueError, TypeError, ValidationError):
        return HTMLResponse(form_page(product, values, BAD_FIELDS), status_code=422)
    try:
        ExperimentService(db).create(product_id=product_id, **model.model_dump())
        db.commit()
    except IntegrityError:
        db.rollback()
        return HTMLResponse(form_page(product, values, "Код эксперимента уже существует."),
                            status_code=409)
    return RedirectResponse(f"/studio/products/{product_id}/campaigns", status_code=303)


@router.get("/studio/products/{product_id}/campaigns/{experiment_id}", response_class=HTMLResponse)
def detail_campaign(product_id: UUID, experiment_id: UUID, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    return campaign_page(product, find_campaign(db, product_id, experiment_id),
                         metrics=campaign_metrics(db, product_id, experiment_id))


@router.get("/studio/products/{product_id}/campaigns/{experiment_id}/edit",
            response_class=HTMLResponse)
def edit_campaign(product_id: UUID, experiment_id: UUID, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    row = find_campaign(db, product_id, experiment_id)
    if row.status != "draft":
        raise HTTPException(409, "Редактировать можно только черновик")
    return form_page(product, {key: getattr(row, key) for key, _ in FIELDS}, row=row)


@router.post("/studio/products/{product_id}/campaigns/{experiment_id}/edit")
async def update_campaign(
    product_id: UUID, experiment_id: UUID, request: Request,
    db: Session = Depends(get_db),
):
    product = _owner_product(db, product_id)
    # The PostgreSQL row lock guards against a simultaneous lifecycle change.
    row = find_campaign(db, product_id, experiment_id, lock=True)
    if row.status != "draft":
        raise HTTPException(409, "Редактировать можно только черновик")
    values = await _studio_form(request)
    try:
        model = read_fields(values)
    except (ValueError, TypeError, ValidationError):
        return HTMLResponse(form_page(product, values, BAD_FIELDS, row=row),
                            status_code=422)
    # Whitelist only form fields, preserving goal_id and secondary_signals.
    editable = {key: getattr(model, key) for key, _ in FIELDS}
    try:
        ExperimentService(db).update(row.id, **editable)
        db.commit()
    except IntegrityError:
        db.rollback()
        return HTMLResponse(form_page(product, values, "Код эксперимента уже существует.",
                                      row=row), status_code=409)
    return RedirectResponse(f"/studio/products/{product_id}/campaigns/{experiment_id}",
                            status_code=303)


@router.post("/studio/products/{product_id}/campaigns/{experiment_id}/launch")
def launch_campaign(
    product_id: UUID, experiment_id: UUID, request: Request,
    db: Session = Depends(get_db), settings=Depends(get_settings),
):
    product = _owner_product(db, product_id)
    # The existing service owns validation, state, decision, task and commit.
    # Its locked SELECT applies the company filter *before* any side effects.
    try:
        ExperimentLaunchService(db, settings.products_root).launch(
            experiment_id,
            product_id=product_id,
            actor=f"studio:{getattr(request.state, 'studio_login', 'operator')}",
        )
    except LookupError:
        db.rollback()
        raise HTTPException(404, "Кампания не найдена у выбранной компании") from None
    except ExperimentLaunchError as exc:
        db.rollback()
        row = find_campaign(db, product_id, experiment_id)
        return HTMLResponse(
            campaign_page(product, row, f"Запуск недоступен: {exc}",
                          metrics=campaign_metrics(db, product_id, experiment_id)),
            status_code=409,
        )
    return RedirectResponse(
        f"/studio/products/{product_id}/campaigns/{experiment_id}",
        status_code=303,
    )
