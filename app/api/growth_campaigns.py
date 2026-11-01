"""Growth V4: safe company-scoped campaign draft editor and read-only details.

No actions on this page can launch tasks, publish, send mail, or spend money.
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
from app.db.session import get_db
from app.models.domain import Experiment
from app.services.experiments import ExperimentService

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


def campaign_page(product, row: Experiment):
    base = f"/studio/products/{product.id}/campaigns"
    fields = "".join(
        f'<div><dt>{esc(label)}</dt><dd>{esc(getattr(row, key))}</dd></div>'
        for key, label in FIELDS
    )
    edit = (
        f'<p><a class="btn primary" href="{base}/{row.id}/edit">Редактировать черновик</a></p>'
        if row.status == "draft"
        else '<p>Изменение запущенной или завершённой кампании здесь недоступно.</p>'
    )
    conclusion = f'<p><b>Заключение:</b> {esc(row.conclusion)}</p>' if row.conclusion else ""
    page = (
        f'<p><a href="{base}">← Все кампании</a></p>'
        f'<section class="surface form-card"><h2>{esc(row.name)}</h2>'
        f'<p>Статус: <b>{esc(row.status)}</b></p>'
        f'<dl class="campaign-details">{fields}</dl>{conclusion}{edit}</section>'
        '<p>Данные из базы. Изменение статуса, запуск заданий и рассылок '
        'этой страницей не выполняются.</p>'
    )
    return studio_page_shell(product, active="campaigns", title="Кампания",
                             subtitle="Данные выбранной компании.", body=page)


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
    return campaign_page(product, find_campaign(db, product_id, experiment_id))


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
