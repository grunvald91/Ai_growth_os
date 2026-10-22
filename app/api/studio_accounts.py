"""Account management page: administrators create logins for Growth Studio."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.content_studio import _owner_product, _studio_form, esc, studio_page_shell
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.core.config import get_settings
from app.core.studio_accounts import (AccountError, create_account, delete_account, load_accounts,
                                      reset_password)
from app.db.session import get_db
from app.models.domain import Product
from app.sso import links as sso_links

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])


def _require_admin(request: Request) -> None:
    if getattr(request.state, "studio_product_ids", None) is not None:
        raise HTTPException(403, "Управлять аккаунтами может только администратор")


def _operator_logins(settings) -> set[str]:
    names = {settings.studio_admin_username} if settings.studio_admin_username else set()
    if settings.studio_htpasswd_file:
        try:
            names |= {line.split(":", 1)[0] for line in settings.studio_htpasswd_file.read_text().splitlines()
                      if line and not line.startswith("#")}
        except OSError:
            pass
    return names


def accounts_html(product_id, accounts, products, operators, *, actor=None, notice=None, error=None, fields=None):
    fields = fields or {}
    names = {str(p.id): p.name for p in products}
    base = f"/studio/products/{product_id}/accounts"
    body = ""
    result = ""
    if notice:
        result = (f'<section class="sg-surface sg-notice" role="status"><h2>{esc(notice[0])}</h2>'
                 f'<p>Логин: <b>{esc(notice[1])}</b><br>Пароль: <code class="sg-secret">{esc(notice[2])}</code></p>'
                 '<p>Скопируйте пароль сейчас: он показывается один раз и нигде не хранится в открытом виде. '
                 'Вход — на странице <b>growth.servgo.ru/studio/login</b>. Логин можно вводить с любой буквы.</p></section>')
    rows = ""
    for name in sorted(operators):
        rows += (f'<tr><td><b>{esc(name)}</b></td><td>Владелец (основной вход)</td><td>Все компании</td>'
                 '<td>Меняется на сервере</td></tr>')
    for name, account in sorted(accounts.items()):
        if account.get("role") == "admin":
            role, scope = "Администратор", "Все компании"
        else:
            role = "Компания"
            scope = ", ".join(names.get(pid, "удалённая компания") for pid in account.get("product_ids", []))
        actions = (f'<form method="post" action="{base}/{esc(name)}/password#account-result" class="sg-inline-actions">'
                   '<button class="btn secondary">Новый пароль</button></form>')
        if name != actor:
            actions += (f'<form method="post" action="{base}/{esc(name)}/delete" class="sg-inline-actions" '
                        f'onsubmit="return confirm(\'Удалить аккаунт {esc(name)}? Вход по нему сразу перестанет работать.\')">'
                        '<button class="btn danger">Удалить</button></form>')
        rows += (f'<tr><td><b>{esc(name)}</b><br><small>{esc(account.get("created_at", ""))[:10]}</small></td>'
                 f'<td>{role}</td><td>{esc(scope)}</td><td>{actions}</td></tr>')
    body += ('<section class="sg-surface"><div class="sg-section-head"><div><span class="sg-overline">ДОСТУП</span>'
             '<h2>Аккаунты Growth</h2><p>Администратор видит все компании и управляет аккаунтами. '
             'Аккаунт компании видит только выбранные компании и не может открыть бизнес-разведку и настройки.</p></div></div>'
             '<div class="sg-table-wrap"><table class="sg-table"><thead><tr><th>Логин</th><th>Роль</th><th>Доступ</th>'
             f'<th></th></tr></thead><tbody>{rows}</tbody></table></div></section>')
    role = fields.get("role", "company")
    # The company being viewed is ticked by default: an account without a company is rejected.
    submitted = "username" in fields  # After a POST keep exactly what the owner chose.
    checks = "".join(
        f'<label class="sg-check"><input type="checkbox" name="company_{p.id}" value="on"'
        f'{" checked" if fields.get(f"company_{p.id}") == "on" or (not submitted and str(p.id) == str(product_id)) else ""}> {esc(p.name)}</label>'
        for p in products)
    # The error sits next to the form and the page scrolls there: on a phone the top is off screen.
    alert = f'<p class="sg-warning sg-form-error" role="alert">{esc(error)}</p>' if error else ""
    # The result (new password) and the form share one anchor, so the page lands on them after a POST.
    body += (f'<div id="account-result">{result}<section class="sg-surface"><h2>Новый аккаунт</h2>{alert}'
             f'<form method="post" action="{base}#account-result" class="sg-form">'
             '<label class="sg-field"><span>Логин</span><input name="username" required minlength="3" maxlength="40" '
             f'pattern="[a-zA-Z0-9][a-zA-Z0-9._-]{{2,39}}" autocomplete="off" value="{esc(fields.get("username", ""))}" '
             'placeholder="например, marketing"><small>Латиница, цифры, точка, дефис, подчёркивание</small></label>'
             '<label class="sg-field"><span>Пароль <em>· необязательно</em></span><input name="password" type="password" '
             'minlength="10" maxlength="200" autocomplete="new-password"><small>Оставьте пустым — сгенерируем надёжный '
             'и покажем один раз</small></label>'
             '<label class="sg-field"><span>Роль</span><select name="role">'
             f'<option value="company"{" selected" if role == "company" else ""}>Компания — только выбранные компании</option>'
             f'<option value="admin"{" selected" if role == "admin" else ""}>Администратор — все компании и аккаунты</option>'
             '</select></label>'
             f'<fieldset class="sg-field"><legend>Компании (для роли «Компания»)</legend>{checks}</fieldset>'
             '<button class="btn primary">Создать аккаунт</button></form></section></div>')
    return body


def connections_html(product_id, links, products, *, error=None, fields=None):
    """ServGo businesses that open a studio company through the «Маркетинг» button in the ServGo admin."""
    fields = fields or {}
    names = {str(p.id): p.name for p in products}
    base = f"/studio/products/{product_id}/connections"
    rows = "".join(
        '<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 0;'
        'border-top:1px solid var(--line,#edf2f8)"><div style="min-width:0"><b>№' + str(link.servgo_business_id) + '</b>'
        f'<div style="overflow-wrap:anywhere">{esc(names.get(str(link.product_id), "удалённая компания"))}</div></div>'
        f'<form method="post" action="{base}/{link.id}/delete" class="sg-inline-actions" '
        f'onsubmit="return confirm(\'Отключить заведение №{link.servgo_business_id}? Вход из ServGo перестанет работать.\')">'
        '<button class="btn danger">Отключить</button></form></div>' for link in links)
    table = rows or '<p>Пока ничего не подключено.</p>'
    options = "".join(f'<option value="{p.id}"{" selected" if fields.get("product_id") == str(p.id) else ""}>{esc(p.name)}</option>'
                      for p in products)
    alert = f'<p class="sg-warning sg-form-error" role="alert">{esc(error)}</p>' if error else ""
    return ('<section class="sg-surface" id="connections"><div class="sg-section-head"><div><span class="sg-overline">ВХОД ИЗ SERVGO</span>'
            '<h2>Заведения ServGo</h2><p>Кнопка «Маркетинг» в админке ServGo откроет эту компанию без пароля. '
            'Номер заведения владелец видит в ServGo: Настройки → Маркетинг. Доступ проверяется в ServGo при каждом открытии страницы.</p></div></div>'
            f'{table}{alert}<form method="post" action="{base}" class="sg-form">'
            '<label class="sg-field"><span>Номер заведения в ServGo</span><input name="servgo_business_id" required inputmode="numeric" '
            f'pattern="[0-9]+" maxlength="9" autocomplete="off" value="{esc(fields.get("servgo_business_id", ""))}"></label>'
            f'<label class="sg-field"><span>Компания в студии</span><select name="product_id">{options}</select></label>'
            '<button class="btn primary">Подключить</button></form></section>')


def _render(request, db, product, settings, *, status=200, **kwargs):
    products = list(db.scalars(select(Product).order_by(Product.name).limit(200)))
    try:
        accounts = load_accounts(settings)
    except (OSError, ValueError):
        raise HTTPException(503, "Studio accounts are not configured correctly") from None
    connection_error = kwargs.pop("connection_error", None)
    connection_fields = kwargs.pop("connection_fields", None)
    body = accounts_html(product.id, accounts, products, _operator_logins(settings),
                         actor=getattr(request.state, "studio_actor", None), **kwargs)
    body += connections_html(product.id, sso_links.list_links(db), products, error=connection_error,
                             fields=connection_fields)
    return HTMLResponse(studio_page_shell(product, active="accounts", title="Аккаунты",
                        subtitle="Кто и к каким компаниям имеет доступ в Growth.", body=body),
                        status_code=status, headers={"Cache-Control": "no-store"})


@router.get("/studio/products/{product_id}/accounts", response_class=HTMLResponse)
def accounts_page(product_id: UUID, request: Request, db: Session = Depends(get_db),
                  settings=Depends(get_settings), actor=Depends(require_studio_auth)):
    _require_admin(request)
    request.state.studio_actor = actor
    return _render(request, db, _owner_product(db, product_id), settings)


@router.post("/studio/products/{product_id}/accounts", response_class=HTMLResponse)
async def accounts_create(product_id: UUID, request: Request, db: Session = Depends(get_db),
                          settings=Depends(get_settings), actor=Depends(require_studio_auth)):
    _require_admin(request)
    request.state.studio_actor = actor
    product = _owner_product(db, product_id)
    fields = await _studio_form(request)
    known = {str(p.id) for p in db.scalars(select(Product))}
    chosen = [key[len("company_"):] for key, value in fields.items() if key.startswith("company_") and value == "on"]
    try:
        if any(value not in known for value in chosen):
            raise AccountError("Выбрана несуществующая компания")
        password = create_account(settings, username=fields.get("username", ""), password=fields.get("password") or None,
                                  role=fields.get("role", ""), product_ids=chosen, created_by=actor,
                                  reserved=_operator_logins(settings))
    except AccountError as exc:
        return _render(request, db, product, settings, status=422, error=str(exc),
                       fields={k: v for k, v in fields.items() if k != "password"})
    return _render(request, db, product, settings, notice=("Аккаунт создан", fields["username"].strip().lower(), password))


@router.post("/studio/products/{product_id}/accounts/{username}/password", response_class=HTMLResponse)
def accounts_password(product_id: UUID, username: str, request: Request, db: Session = Depends(get_db),
                      settings=Depends(get_settings), actor=Depends(require_studio_auth)):
    _require_admin(request)
    request.state.studio_actor = actor
    product = _owner_product(db, product_id)
    try:
        password = reset_password(settings, username)
    except AccountError as exc:
        return _render(request, db, product, settings, status=404, error=str(exc))
    return _render(request, db, product, settings, notice=("Пароль изменён", username, password))


@router.post("/studio/products/{product_id}/accounts/{username}/delete", response_class=HTMLResponse)
def accounts_delete(product_id: UUID, username: str, request: Request, db: Session = Depends(get_db),
                    settings=Depends(get_settings), actor=Depends(require_studio_auth)):
    _require_admin(request)
    request.state.studio_actor = actor
    product = _owner_product(db, product_id)
    try:
        delete_account(settings, username, actor=actor)
    except AccountError as exc:
        return _render(request, db, product, settings, status=409, error=str(exc))
    return _render(request, db, product, settings)


@router.post("/studio/products/{product_id}/connections", response_class=HTMLResponse)
async def connections_create(product_id: UUID, request: Request, db: Session = Depends(get_db),
                             settings=Depends(get_settings), actor=Depends(require_studio_auth)):
    _require_admin(request)
    request.state.studio_actor = actor
    product = _owner_product(db, product_id)
    fields = await _studio_form(request)
    try:
        sso_links.connect(db, fields.get("servgo_business_id", ""), fields.get("product_id", ""))
    except sso_links.LinkError as exc:
        return _render(request, db, product, settings, status=422, connection_error=str(exc), connection_fields=fields)
    return RedirectResponse(f"/studio/products/{product.id}/accounts#connections", status_code=303)


@router.post("/studio/products/{product_id}/connections/{link_id}/delete", response_class=HTMLResponse)
def connections_delete(product_id: UUID, link_id: str, request: Request, db: Session = Depends(get_db),
                       settings=Depends(get_settings), actor=Depends(require_studio_auth)):
    _require_admin(request)
    request.state.studio_actor = actor
    product = _owner_product(db, product_id)
    try:
        sso_links.disconnect(db, link_id)
    except sso_links.LinkError as exc:
        return _render(request, db, product, settings, status=404, connection_error=str(exc))
    return RedirectResponse(f"/studio/products/{product.id}/accounts#connections", status_code=303)
