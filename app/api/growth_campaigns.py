"""Progressive Studio campaign drafts; no launch, publication or spending side effects.

Existing ExperimentCreate strict API remains untouched. The database already
supports nullable planning fields, so UI drafts do not require fake budgets,
metrics, success conditions or internal codes.
"""
from __future__ import annotations

import html
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.content_studio import _owner_product, _studio_form, studio_page_shell
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.db.session import get_db
from app.models.domain import Experiment
from app.services.experiments import ExperimentService

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])

# Keep the established field keys and strict ExperimentCreate API contract.
FIELDS = [
    ("experiment_key", "Код эксперимента (EXP-...)"),
    ("name", "Название кампании"),
    ("hypothesis", "Цель и гипотеза"),
    ("audience", "Аудитория"),
    ("channel", "Канал"),
    ("action", "План действий"),
    ("target_metric", "Основной показатель"),
    ("attribution_method", "Как измеряем результат"),
    ("baseline_summary", "Исходные показатели"),
    ("observation_window_days", "Период наблюдения, дней"),
    ("time_budget_minutes", "Время команды, минут"),
    ("success_condition", "Условие успеха"),
    ("stop_condition", "Условие остановки"),
    ("inconclusive_condition", "Когда результат не определён"),
    ("budget", "Бюджет"),
    ("currency", "Валюта (RUB, USD)"),
]
LABELS = dict(FIELDS)
BASIC = ("name", "hypothesis", "audience", "channel")
NUMERIC = ("observation_window_days", "time_budget_minutes", "budget")
LONG_TEXT = ("hypothesis", "audience", "action", "attribution_method", "baseline_summary",
             "success_condition", "stop_condition", "inconclusive_condition")


class CampaignDraftForm(BaseModel):
    """Only name and goal are mandatory until a draft is ready for planning."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=300)
    hypothesis: str = Field(min_length=1, max_length=4000)
    audience: str | None = Field(default=None, max_length=2000)
    channel: str | None = Field(default=None, max_length=100)
    experiment_key: str | None = Field(
        default=None, min_length=5, max_length=100, pattern=r"^EXP-[A-Z0-9-]+$")
    action: str | None = Field(default=None, max_length=4000)
    target_metric: str | None = Field(default=None, max_length=100)
    attribution_method: str | None = Field(default=None, max_length=4000)
    baseline_summary: str | None = Field(default=None, max_length=4000)
    observation_window_days: int | None = Field(default=None, ge=1, le=180)
    time_budget_minutes: int | None = Field(default=None, ge=1, le=100000)
    success_condition: str | None = Field(default=None, max_length=4000)
    stop_condition: str | None = Field(default=None, max_length=4000)
    inconclusive_condition: str | None = Field(default=None, max_length=4000)
    budget: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    currency: str | None = Field(default=None, min_length=3, max_length=3,
                                 pattern=r"^[A-Z]{3}$")


def parse_campaign_draft(values: dict[str, str]) -> dict:
    """Return only actual user-supplied fields; blank advanced values are NULL."""
    raw = {key: values.get(key, "").strip() for key, _ in FIELDS}
    supplied = {key: value if value else None for key, value in raw.items()}
    if supplied["budget"] is not None and supplied["currency"] is None:
        raise ValueError("Укажите валюту, если ввели бюджет.")
    # Extra fields cannot turn a draft into a live campaign.
    model = CampaignDraftForm.model_validate(supplied)
    return model.model_dump(exclude_none=True)


def _field(key: str, values: dict, *, required: bool = False) -> str:
    label = html.escape(LABELS[key])
    raw = values.get(key)
    value = "" if raw is None else str(raw)
    attrs = ' required' if required else ""
    if key in NUMERIC:
        lower = "0" if key == "budget" else "1"
        upper = " max=\"180\"" if key == "observation_window_days" else (
            " max=\"100000\"" if key == "time_budget_minutes" else "")
        step = "0.01" if key == "budget" else "1"
        control = (f'<input type="number" min="{lower}"{upper} step="{step}"'
                   f' inputmode="decimal" name="{key}" id="campaign-{key}"'
                   f' value="{html.escape(value, quote=True)}"{attrs}>')
    elif key in LONG_TEXT:
        control = (f'<textarea name="{key}" id="campaign-{key}" rows="3"'
                   f' maxlength="{4000 if key != "audience" else 2000}"{attrs}>'
                   f'{html.escape(value)}</textarea>')
    else:
        max_length = (300 if key == "name" else 100 if key in
                      ("experiment_key", "channel", "target_metric") else 3 if key == "currency" else 4000)
        control = (f'<input name="{key}" id="campaign-{key}"'
                   f' maxlength="{max_length}"'
                   f' value="{html.escape(value, quote=True)}"{attrs}>')
    return f'<label class="field" for="campaign-{key}">{label}{control}</label>'


def form_page(product, values: dict, error: str = "", experiment: Experiment | None = None) -> str:
    required = ("name", "hypothesis")
    essential = "".join(_field(key, values, required=key in required) for key in BASIC)
    optional = "".join(_field(key, values) for key, _ in FIELDS if key not in BASIC)
    advanced_open = " open" if (error and any(values.get(key) for key in LABELS if key not in BASIC)) else ""
    base = f"/studio/products/{product.id}/campaigns"
    edit = experiment is not None
    action = f'{base}/{experiment.id}/edit' if edit else f'{base}/new'
    body = (
        (f'<p role="alert" class="v4-error">{html.escape(error)}</p>' if error else "")
        + f'<form method="post" action="{action}" class="surface form-card sg-campaign-form">'
        + '<section class="campaign-essential"><h2>Основа кампании</h2>'
        + '<p>Начните с идеи. Остальные параметры можно заполнить позже.</p>'
        + essential + '</section>'
        + f'<details class="campaign-optional"{advanced_open}>'
        + '<summary>Дополнительные настройки (необязательно)</summary>'
        + '<p>Заполняйте только известные показатели. Неизвестные значения останутся пустыми.</p>'
        + optional + '</details>'
        + '<p class="campaign-save-note">Сохраняется только черновик. '
        + 'Расходы, рассылки и публикации не запускаются.</p>'
        + '<div class="campaign-actions"><button type="submit" class="btn blue">'
        + ('Сохранить изменения' if edit else 'Создать черновик')
        + f'</button><a class="btn" href="{base}">К списку кампаний</a></div></form>'
    )
    return studio_page_shell(product, active="campaigns",
        title="Редактирование черновика" if edit else "Новая кампания",
        subtitle="Сначала цель. Настройки и показатели — когда они известны.",
        body=body)


def _draft_values(experiment: Experiment) -> dict:
    return {key: getattr(experiment, key, None) for key, _ in FIELDS}


def _existing_draft(db: Session, product_id: UUID, experiment_id: UUID) -> Experiment:
    record = db.get(Experiment, experiment_id)
    if record is None or record.product_id != product_id:
        raise HTTPException(404, "Кампания не найдена")
    if record.status != "draft":
        raise HTTPException(409, "Изменять можно только черновик кампании")
    return record


def _invalid_message(values: dict, error: ValidationError | ValueError) -> str:
    if isinstance(error, ValidationError):
        keys = [str(item["loc"][0]) for item in error.errors() if item["loc"]]
        fields = ", ".join(LABELS.get(key, key) for key in dict.fromkeys(keys))
        return "Проверьте заполнение: " + (fields or "поля кампании")
    return str(error)


@router.get("/studio/products/{product_id}/campaigns/new", response_class=HTMLResponse)
def new_campaign(product_id: UUID, db: Session = Depends(get_db)):
    return form_page(_owner_product(db, product_id), {})


@router.post("/studio/products/{product_id}/campaigns/new")
async def save_campaign(product_id: UUID, request: Request, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    values = await _studio_form(request)
    try:
        fields = parse_campaign_draft(values)
    except (ValidationError, ValueError) as error:
        return HTMLResponse(form_page(product, values, _invalid_message(values, error)),
                            status_code=422)
    try:
        ExperimentService(db).create(product_id=product_id, **fields)
        db.commit()
    except IntegrityError:
        db.rollback()
        return HTMLResponse(form_page(product, values, "Кампания с таким кодом уже существует."),
                            status_code=409)
    return RedirectResponse(f"/studio/products/{product_id}/campaigns", status_code=303)


@router.get("/studio/products/{product_id}/campaigns/{experiment_id}/edit",
            response_class=HTMLResponse)
def edit_campaign(product_id: UUID, experiment_id: UUID, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    record = _existing_draft(db, product_id, experiment_id)
    return form_page(product, _draft_values(record), experiment=record)


@router.post("/studio/products/{product_id}/campaigns/{experiment_id}/edit")
async def update_campaign(product_id: UUID, experiment_id: UUID,
                          request: Request, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    record = _existing_draft(db, product_id, experiment_id)
    values = await _studio_form(request)
    try:
        fields = parse_campaign_draft(values)
    except (ValidationError, ValueError) as error:
        return HTMLResponse(form_page(product, values, _invalid_message(values, error), record),
                            status_code=422)
    # All 16 form keys are submitted, and blank optional fields clear old values.
    clearing = {key: None for key, _ in FIELDS if key not in BASIC and not values.get(key, "").strip()}
    try:
        ExperimentService(db).update(record.id, **fields, **clearing)
        db.commit()
    except IntegrityError:
        db.rollback()
        return HTMLResponse(form_page(product, values, "Кампания с таким кодом уже существует.", record),
                            status_code=409)
    return RedirectResponse(f"/studio/products/{product_id}/campaigns", status_code=303)
