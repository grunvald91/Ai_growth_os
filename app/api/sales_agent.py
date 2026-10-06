"""Owner-operated live ServGo settings and auditable manual capability changes."""
import hmac
import json
import secrets
from datetime import datetime, timezone
from html import escape
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.content_studio import studio_page_shell
from app.core.config import get_settings
from app.db.session import get_db
from app.models.domain import Approval, Artifact, ArtifactStatus, Product, ToolCall
from app.policies import PolicyEvaluator
from app.tools import ToolOperation
from app.tools.servgo import ServGoAdapter

router = APIRouter()
basic = HTTPBasic(auto_error=False)
CSRF_COOKIE = "growth_sales_csrf"
RULE_LABELS = {
    "opening": "Начало работы", "closing": "Конец работы", "slot_interval_minutes": "Шаг записи, мин",
    "break_start": "Начало перерыва", "break_end": "Конец перерыва", "booking_flow_order": "Порядок выбора",
    "prepayment_enabled": "Предоплата включена", "prepayment_percent": "Предоплата, %",
    "receipt_required": "Требуется чек", "legal_consent_required": "Юридические согласия",
    "cancellation_policy": "Условия отмены", "rental_deposit_policy": "Депозит аренды",
}


def rules_html(values):
    rows = []
    for key, value in values.items():
        if value is None:
            text = "Не задано"
        elif isinstance(value, bool):
            text = "Да" if value else "Нет"
        elif value == "resource_first":
            text = "Сначала ресурс"
        elif value == "service_first":
            text = "Сначала услуга"
        else:
            text = str(value)
        rows.append(f'<dt>{escape(RULE_LABELS.get(key, key))}</dt><dd>{escape(text)}</dd>')
    return '<dl>' + ''.join(rows) + '</dl>'


def owner(credentials: HTTPBasicCredentials | None = Depends(basic)):
    password = get_settings().servgo_sales_owner_password or ""
    if len(password) < 16:
        raise HTTPException(503, "Доступ владельца к настройкам ИИ не настроен")
    if (credentials is None or credentials.username != "owner"
        or not hmac.compare_digest(credentials.password.encode(), password.encode())):
        raise HTTPException(401, "Требуется вход владельца", headers={"WWW-Authenticate": 'Basic realm="Sales agent owner"'})
    return "local_owner"


def servgo_product(db, product_id):
    product = db.get(Product, product_id)
    if product is None or product.slug != "servgo":
        raise HTTPException(404, "Настройки доступны для проекта ServGo")
    return product


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = True
    booking_mode: Literal["client_confirmation", "operator_only"] = "client_confirmation"
    show_prices: bool = True
    recommend_specialists: bool = True
    check_availability: bool = True
    view_client_bookings: bool = True
    instructions: str = Field(default="", max_length=6000)
    prohibited_topics: str = Field(default="", max_length=2000)


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    policy: Policy
    approved: Literal[True]


@router.get("/studio/products/{product_id}/sales-agent", response_class=HTMLResponse)
def settings_page(product_id: UUID, request: Request, business_id: int | None = None,
                  actor=Depends(owner), db: Session = Depends(get_db)):
    product = servgo_product(db, product_id)
    csrf = secrets.token_urlsafe(32)
    try:
        adapter = ServGoAdapter()
        businesses = adapter.businesses()
        if not businesses:
            raise HTTPException(503, "В ServGo нет бизнесов, разрешённых для этой интеграции")
        selected = business_id if business_id is not None else businesses[0]["id"]
        if selected not in {b["id"] for b in businesses}:
            raise HTTPException(403, "Нет доступа к выбранному бизнесу")
        document = adapter.read(selected)
        policy = Policy.model_validate(document["policy"])
        options = "".join(f'<option value="{int(b["id"])}" {"selected" if b["id"] == selected else ""}>{escape(b["name"])}</option>' for b in businesses)
        toggles = "".join(f'<label style="display:block;margin:14px 0"><input type="checkbox" name="{name}" {"checked" if getattr(policy, name) else ""}> {label}</label>' for name, label in [
            ("enabled", "Включить ИИ-продавца"), ("show_prices", "Показывать цены"),
            ("recommend_specialists", "Подбирать специалистов и ресурсы"),
            ("check_availability", "Проверять свободные окна"),
            ("view_client_bookings", "Показывать клиенту его записи после входа")])
        rules = "".join(f'<li>{escape(item)}</li>' for item in document["always_forbidden"])
        coverage = "".join(f'<tr><td style="padding:8px">{escape(item["rule"])}</td><td style="padding:8px">{escape({"supported": "Поддерживается", "form_or_operator": "Форма / оператор", "booking_page_or_operator": "Страница записи / оператор"}.get(item["status"], item["status"]))}</td></tr>' for item in document["coverage"])
        body = f'''<section class="panel" style="padding:24px">
        <form method="get"><label>Бизнес <select name="business_id" onchange="this.form.submit()">{options}</select></label></form>
        <p>Настройки читаются из ServGo. Изменения применяются только после сохранения.</p>
        <form id="sales-settings" data-revision="{int(document['revision'])}" data-business="{int(selected)}" data-csrf="{csrf}">
        {toggles}<label>Создание записи <select name="booking_mode">
        <option value="client_confirmation" {"selected" if policy.booking_mode == "client_confirmation" else ""}>ИИ создаёт запись после подтверждения клиента</option>
        <option value="operator_only" {"selected" if policy.booking_mode == "operator_only" else ""}>Записывает только оператор</option></select></label>
        <p>Подтверждение — кнопка в карточке. Без цен или проверки окон ИИ не создаёт запись.</p>
        <label style="display:block">Инструкции компании<textarea name="instructions" maxlength="6000" rows="6" style="display:block;width:100%">{escape(policy.instructions)}</textarea></label>
        <label style="display:block;margin:16px 0">Темы, которых агент должен избегать<textarea name="prohibited_topics" maxlength="2000" rows="3" style="display:block;width:100%">{escape(policy.prohibited_topics)}</textarea></label>
        <label><input id="approve-sales" type="checkbox" required> Подтверждаю изменение прав ИИ для этого бизнеса</label>
        <p><button class="btn primary" type="submit">Сохранить и применить в ServGo</button></p>
        <p id="sales-status" role="status" aria-live="polite"></p></form></section>
        <section class="panel" style="padding:24px"><h2>Обязательные ограничения</h2><ul>{rules}</ul>
        <h2>Правила бронирования</h2>{rules_html(document['business_rules'])}
        <table><tbody>{coverage}</tbody></table><p>Диалоги для оператора: админка ServGo → Диалоги ИИ.</p></section>'''
        script = f'<script src="/studio/assets/sales-agent.js"></script>'
    except HTTPException as exc:
        body = f'<section class="panel" style="padding:24px"><p role="alert">{escape(str(exc.detail))}</p><p>Действующие настройки не загружены. Сохранение недоступно.</p></section>'
        script = ""
    response = HTMLResponse(studio_page_shell(product, active="sales-agent", title="ИИ-продавец",
        subtitle="Возможности и ограничения агента каждого бизнеса ServGo", body=body, dialogs=script))
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(CSRF_COOKIE, csrf, httponly=True, secure=request.url.scheme == "https", samesite="strict", max_age=3600)
    return response


@router.put("/studio/products/{product_id}/sales-agent/businesses/{business_id}")
def save_settings(product_id: UUID, business_id: int, body: Change, request: Request,
                  actor=Depends(owner), db: Session = Depends(get_db)):
    servgo_product(db, product_id)
    csrf = request.cookies.get(CSRF_COOKIE, "")
    if len(csrf) < 32 or not hmac.compare_digest(csrf, request.headers.get("X-CSRF-Token", "")):
        raise HTTPException(403, "Обновите страницу настроек перед сохранением")
    origin = request.headers.get("Origin")
    if origin and urlsplit(origin).netloc != request.url.netloc:
        raise HTTPException(403, "Запрос пришёл с другого сайта")
    adapter = ServGoAdapter()
    if business_id not in {b["id"] for b in adapter.businesses()}:
        raise HTTPException(403, "Нет доступа к выбранному бизнесу")
    before = adapter.read(business_id)
    if before["revision"] != body.expected_revision:
        raise HTTPException(409, "Настройки уже изменены. Обновите страницу")
    data = body.model_dump(exclude={"approved"})
    decision = PolicyEvaluator().evaluate(ToolOperation.CHANGE_ACCESS)
    # Explicit authenticated click approves exactly this version, never a model claim.
    version = (db.scalar(select(func.max(Artifact.version)).where(Artifact.product_id == product_id,
        Artifact.kind == f"servgo_sales_policy:{business_id}")) or 0) + 1
    artifact = Artifact(product_id=product_id, kind=f"servgo_sales_policy:{business_id}",
        title=f"Права ИИ ServGo: бизнес {business_id}", version=version, status=ArtifactStatus.APPROVED,
        content=json.dumps({"before": before, "requested": data}, ensure_ascii=False))
    db.add(artifact); db.flush()
    approval = Approval(product_id=product_id, artifact_id=artifact.id, action_type=decision.operation.value,
        risk_level=decision.risk_level.value, status="approved", requested_by=actor, decided_by=actor,
        decided_at=datetime.now(timezone.utc), reason="Владелец подтвердил форму изменения прав ИИ")
    db.add(approval); db.flush()
    tool_call = ToolCall(product_id=product_id, approval_id=approval.id, tool_name="servgo.sales_policy.update",
        operation=decision.operation.value, request_data={"business_id": business_id, **data}, status="requested")
    db.add(tool_call); db.commit()
    try:
        result = adapter.update(business_id, data)
    except HTTPException as exc:
        tool_call.status = "failed"; tool_call.error = str(exc.detail)
        tool_call.executed_at = datetime.now(timezone.utc); db.commit()
        raise
    tool_call.status = "succeeded"; tool_call.response_data = result
    tool_call.executed_at = datetime.now(timezone.utc); db.commit()
    return result


@router.get("/studio/assets/sales-agent.js")
def script():
    from fastapi import Response
    return Response(SALES_JS, media_type="application/javascript")


SALES_JS = r'''
const form = document.getElementById('sales-settings');
if (form) form.addEventListener('submit', async event => {
  event.preventDefault();
  const status = document.getElementById('sales-status');
  const button = form.querySelector('button[type="submit"]');
  if (button.disabled || !document.getElementById('approve-sales').checked) return;
  button.disabled = true; status.textContent = 'Сохраняем настройки…';
  const policy = {};
  for (const name of ['enabled','show_prices','recommend_specialists','check_availability','view_client_bookings']) policy[name] = form.elements[name].checked;
  for (const name of ['booking_mode','instructions','prohibited_topics']) policy[name] = form.elements[name].value;
  try {
    const response = await fetch(location.pathname + '/businesses/' + form.dataset.business, {
      method: 'PUT', credentials: 'same-origin', headers: {'Content-Type':'application/json','X-CSRF-Token':form.dataset.csrf},
      body: JSON.stringify({expected_revision:Number(form.dataset.revision),policy,approved:true})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Не удалось сохранить настройки');
    form.dataset.revision = String(result.revision);
    document.getElementById('approve-sales').checked = false;
    status.textContent = 'Настройки применены в ServGo.';
  } catch (error) { status.textContent = error.message || 'Ошибка связи. Обновите страницу перед повторной попыткой.'; }
  finally { button.disabled = false; }
});
'''
