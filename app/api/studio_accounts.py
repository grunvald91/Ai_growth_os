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


# --- Explicitly link an existing Growth operator to a signed-in ServGo user. ---
# The password is verified once, never copied into BookingBot and never stored
# with the binding. Both identities must be proved in the same browser.
def _live_servgo_identity(request: Request, settings, db: Session) -> int | None:
    from app.sso import access, tickets
    from app.api.content_studio_auth import _sso_link_current
    if not tickets.configured(settings):
        return None
    session = tickets.read_session(settings, request.cookies.get(tickets.COOKIE))
    if session is None or not _sso_link_current(db, session):
        return None
    access.forget(session.user_id, session.business_id)
    if access.servgo_role(settings, session.user_id, session.business_id) is None:
        return None
    return session.user_id


def _identity_html(*, connected: bool = False, linked: bool = False, error: str = "",
                   status: int = 200) -> HTMLResponse:
    notice = ('<p class="sg-link-success" role="status">Профили объединены. '
              'Вы можете открывать Growth из ServGo с правами существующего администратора.</p>'
              if linked else "")
    alert = f'<p role="alert" class="sg-link-error">{esc(error)}</p>' if error else ""
    form = (
        '<form method="post" action="/studio/identity" class="sg-link-form">'
        '<label>Логин Growth <input type="text" name="username" autocomplete="username" '
        'required maxlength="80" placeholder="Существующий логин"></label>'
        '<label>Пароль Growth <input type="password" name="password" autocomplete="current-password" '
        'required maxlength="200" placeholder="Текущий пароль"></label>'
        '<button type="submit">Подтвердить и объединить</button></form>'
        if connected and not linked else (
            '<p class="sg-link-hint">Сначала откройте Growth кнопкой «Маркетинг» из '
            '<a href="https://servgo.ru/admin">админки ServGo</a>, '
            'чтобы подтвердить вашу учётную запись ServGo.</p>' if not connected else ''
        )
    )
    if connected and linked:
        form = ('<form method="post" action="/studio/identity/disconnect" class="sg-link-form">'
                '<button type="submit" class="sg-link-revoke">Отвязать профиль Growth</button></form>')
    content = ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
               '<meta name="viewport" content="width=device-width,initial-scale=1">'
               '<title>Единый профиль · ServGo</title>'
               '<style>'
               '.sg-link-page{--link-ink:#192a3b;--link-muted:#5c6c7f;--link-bg:#fff;--link-border:#dce3ef;'
               'width:min(100% - 28px,510px);margin:24px auto;padding:24px;border:1px solid var(--link-border);'
               'border-radius:18px;background:var(--link-bg);color:var(--link-ink);font:inherit}'
               '.sg-link-page h1{font-size:25px;line-height:1.2;margin:0 0 10px;color:var(--link-ink)}'
               '.sg-link-page p{line-height:1.55;color:var(--link-muted);font-size:14px}'
               '.sg-link-page a{color:#315edf}'
               '.sg-link-form{display:grid;gap:14px;margin-top:22px}'
               '.sg-link-form label{display:grid;gap:6px;font-weight:600;font-size:13px}'
               '.sg-link-form input{box-sizing:border-box;width:100%;min-height:46px;'
               'padding:12px;border:1px solid var(--link-border);border-radius:12px;font:inherit;'
               'background:var(--link-bg);color:var(--link-ink)}'
               '.sg-link-form button{background:#315edf;color:#fff;min-height:46px;border:0;'
               'border-radius:12px;font:600 14px inherit;cursor:pointer}'
               '.sg-link-page .sg-link-error{color:#a72839}'
               '.sg-link-page .sg-link-success{color:#21683c}'
               'body[data-growth-design="v4"] .sg-link-page{--link-ink:var(--s-text);--link-muted:var(--s-muted);'
               '--link-bg:var(--s-surface);--link-border:var(--s-line-2);margin:14px auto}'
               'body[data-growth-design="v4"] .sg-link-page a{color:var(--s-accent)}'
               'body[data-growth-design="v4"] .sg-link-form button{background:var(--s-accent);color:var(--s-accent-ink)}'
               '</style></head><body><main class="sg-link-page">'
               '<h1>Объединить профили</h1>'
               '<p>Войдите с вашим существующим логином Growth один раз. '
               'Пароль проверяется на сервере Growth и не передаётся BookingBot. '
               'После привязки права старого профиля будут доступны при входе через ServGo. '
               'Смена пароля Growth автоматически отменит привязку.</p>'
               + notice + alert + form +
               '<p><a href="/studio?choose=1">← К компаниям</a></p>'
               '</main></body></html>')
    return HTMLResponse(content, status_code=status, headers={"Cache-Control": "private, no-store"})


@router.get("/studio/identity", response_class=HTMLResponse)
def studio_identity(request: Request, db: Session = Depends(get_db),
                    settings=Depends(get_settings), linked: bool = False):
    uid = _live_servgo_identity(request, settings, db)
    connected = uid is not None
    if connected:
        from app.core.studio_operator_links import linked_operator
        from app.api.content_studio_auth import _load_users
        linked = linked_operator(settings, uid, _load_users(settings)) is not None
    else:
        linked = False
    return _identity_html(connected=connected, linked=linked)


@router.post("/studio/identity", response_class=HTMLResponse)
async def studio_identity_connect(request: Request, db: Session = Depends(get_db),
                                  settings=Depends(get_settings)):
    uid = _live_servgo_identity(request, settings, db)
    if uid is None:
        return _identity_html(error="Нет подтверждённого входа ServGo. Откройте Growth из админки.",
                              status=403)
    fields = await _studio_form(request)
    login, password = fields.get("username", ""), fields.get("password", "")
    from app.api.content_studio_auth import _load_users, check_credentials, credential_fingerprint
    from app.core.studio_operator_links import bind_operator
    from app.core.studio_sessions import clear_failures, normalize_login, record_failure, too_many_failures

    client = request.headers.get("x-real-ip") or (request.client.host if request.client else "")
    keys = ("link:" + normalize_login(login), "ip:" + client)
    if any(too_many_failures(key) for key in keys):
        return _identity_html(connected=True, status=429,
                              error="Слишком много попыток входа. Попробуйте позже.")
    users = _load_users(settings)
    verified = check_credentials(settings, users, login, password) if login and password else None
    if verified is None or verified[1] is not None:
        for key in keys:
            record_failure(key)
        return _identity_html(connected=True, status=401,
                              error="Логин или пароль не подошёл либо аккаунт не имеет прав администратора Growth.")
    name, _ = verified
    current = credential_fingerprint(settings, users, name)
    if current is None or current[1] is not None:
        return _identity_html(connected=True, status=403, error="Нет прав администратора Growth.")
    bind_operator(settings, uid, name, current[0])
    for key in keys:
        clear_failures(key)
    return RedirectResponse("/studio/identity?linked=1", status_code=303)


@router.post("/studio/identity/disconnect")
def studio_identity_disconnect(request: Request, db: Session = Depends(get_db),
                               settings=Depends(get_settings)):
    uid = _live_servgo_identity(request, settings, db)
    if uid is None:
        return _identity_html(status=403, error="Вход ServGo не подтверждён.")
    from app.core.studio_operator_links import unbind_operator
    unbind_operator(settings, uid)
    return RedirectResponse("/studio/identity", status_code=303)
