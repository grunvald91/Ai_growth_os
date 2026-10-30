"""Studio forms for the existing experiment draft contract."""
import html
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.content_studio import _owner_product, _studio_form, studio_page_shell
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.api.schemas import ExperimentCreate
from app.db.session import get_db
from app.models.domain import Experiment
from app.services.experiments import ExperimentService

router=APIRouter(dependencies=[Depends(require_studio_auth),Depends(require_studio_csrf)])
FIELDS=[('experiment_key','Код эксперимента (EXP-...)'),('name','Название'),('hypothesis','Гипотеза'),
('audience','Аудитория'),('channel','Канал'),('action','План действий'),('target_metric','Основной показатель'),
('attribution_method','Как измеряем результат'),('baseline_summary','Исходные показатели'),
('observation_window_days','Период наблюдения, дней'),('time_budget_minutes','Время команды, минут'),
('success_condition','Условие успеха'),('stop_condition','Условие остановки'),
('inconclusive_condition','Когда результат не определён'),('budget','Бюджет'),('currency','Валюта (RUB, USD)')]


def form_page(product, values, error=''):
    fields=''.join(f'<label class="field">{label}<input name="{key}" value="{html.escape(str(values.get(key,"")),quote=True)}" required></label>' for key,label in FIELDS)
    body=(f'<p role="alert">{html.escape(error)}</p>' if error else '')+f'<form method="post" class="surface form-card">{fields}<p>Сохраняется черновик. Расходы и публикации не запускаются.</p><button type="submit" class="btn blue">Сохранить черновик</button></form>'
    return studio_page_shell(product,active='campaigns',title='Черновик кампании',subtitle='Цель, гипотеза и измеримый результат.',body=body)


@router.get('/studio/products/{product_id}/campaigns/new',response_class=HTMLResponse)
def new_campaign(product_id: UUID, db: Session=Depends(get_db)):
    return form_page(_owner_product(db,product_id),{'currency':'RUB'})


@router.post('/studio/products/{product_id}/campaigns/new')
async def save_campaign(product_id: UUID,request: Request,db: Session=Depends(get_db)):
    product=_owner_product(db,product_id)
    values=await _studio_form(request)
    fields={key:values.get(key,'') for key,_ in FIELDS}
    try:
        for key in ('observation_window_days','time_budget_minutes'):
            fields[key]=int(fields[key])
        model=ExperimentCreate.model_validate(fields)
    except (ValidationError,ValueError):
        return HTMLResponse(form_page(product,values,'Проверьте обязательные поля: код EXP-, положительные сроки, неотрицательный бюджет и трёхбуквенную валюту.'),status_code=422)
    try:
        ExperimentService(db).create(product_id=product_id,**model.model_dump())
        db.commit()
    except IntegrityError:
        db.rollback()
        return HTMLResponse(form_page(product,values,'Эксперимент с таким кодом уже существует.'),status_code=409)
    return RedirectResponse(f'/studio/products/{product_id}/campaigns',status_code=303)
