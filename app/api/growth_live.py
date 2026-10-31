"""Opt-in v4 presentation over the existing authenticated Studio handlers.

No proxy calls or duplicate write APIs: forms, scripts, CSRF and permissions stay
with the handlers that already own each workflow.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.api.content_studio import MATERIAL_KINDS, _owner_product, studio_page_shell
from app.db.session import get_db
from app.models.domain import Artifact, Experiment, Task
from app.api.seo_run_view import seo_runs

ASSETS = Path(__file__).parent / 'assets'
router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])
COOKIE = 'growth_design'


def switch_url(request: Request, design: str) -> str:
    pairs = [(k, v) for k, v in request.query_params.multi_items() if k != 'design']
    return request.url.path + '?' + urlencode(pairs + [('design', design)])


def decorate(page: str, request: Request, new: bool) -> str:
    """Keep the original document and event targets intact inside the workspace."""
    switch = html.escape(switch_url(request, 'classic' if new else 'v4'), quote=True)
    if not new:
        return page.replace('</body>', f'<a class="gv-switch" href="{switch}">Новый дизайн →</a>'
                            '<style>.gv-switch{position:fixed;bottom:90px;right:16px;z-index:60;'
                            'padding:12px 18px;border-radius:24px;background:#215da9;color:white;'
                            'font:600 14px system-ui;box-shadow:0 3px 16px #1234}</style></body>')
    match = re.search(r'/studio/products/([0-9a-fA-F-]{36})(?:/|$)', request.url.path)
    if not match:
        match = re.search(r'/studio/products/([0-9a-fA-F-]{36})', page)
    pid = UUID(match[1]) if match else None
    from app.api.growth_v4 import render_v4
    return render_v4(page, request, pid, switch)


class GrowthDesignMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        # /studio/team owns a CSP-protected, fully functional standalone UI.
        # Rewrapping it with inline v4 styles/handlers breaks its strict CSP.
        # Theme it through external assets instead; keep its existing controls.
        eligible = (path == '/intelligence' or path.startswith('/studio')) and not (
            path == '/studio/team' or path.startswith('/studio/team/')
        ) and not any(
            part in path for part in ('/assets/', '/login', '/logout', '/sso/', '/design-preview'))
        choice = request.query_params.get('design')
        new = (choice if choice in {'v4', 'classic'} else request.cookies.get(COOKIE)) == 'v4'
        if eligible and request.method in {'GET', 'POST'}:
            if response.status_code in {200, 409, 422} and 'text/html' in response.headers.get('content-type', ''):
                body = b''.join([chunk async for chunk in response.body_iterator])
                page = body.decode('utf-8')
                if '</body>' in page:
                    body = decorate(page, request, new).encode('utf-8')
                headers = dict(response.headers)
                for stale in ('content-length', 'etag', 'last-modified'):
                    headers.pop(stale, None)
                if new:
                    headers['cache-control'] = 'private, no-store'
                else:
                    headers.setdefault('cache-control', 'no-store')
                headers['vary'] = ', '.join(filter(None, [headers.get('vary'), 'Cookie']))
                original = response
                response = Response(body, status_code=response.status_code, headers=headers, background=original.background)
                # Preserve multiple Set-Cookie headers set by the original handler.
                response.raw_headers = [(k,v) for k,v in response.raw_headers if k != b'set-cookie'] + [
                    (k,v) for k,v in original.raw_headers if k == b'set-cookie']
            if request.method == 'GET' and choice in {'v4', 'classic'} and response.status_code < 400:
                response.set_cookie(COOKIE, choice, httponly=True, samesite='lax',
                                    secure=request.url.scheme == 'https', max_age=31536000)
        return response


@router.get('/studio/assets/growth-live.css')
def stylesheet():
    return Response((ASSETS/'growth-live.css').read_text(), media_type='text/css')


@router.get('/studio/assets/growth-live.js')
def javascript():
    return Response((ASSETS/'growth-live.js').read_text(), media_type='application/javascript')


# Explicit allowlist: v4 can only serve these reviewed same-origin design resources.
@router.get('/studio/assets/servgo-air-tokens.css', include_in_schema=False)
def air_tokens():
    response = Response((ASSETS/'servgo-air-tokens.css').read_text(), media_type='text/css')
    response.headers['Cache-Control'] = 'public, max-age=300'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@router.get('/studio/assets/growth-servgo-air.css', include_in_schema=False)
def air_adapter():
    response = Response((ASSETS/'growth-servgo-air.css').read_text(), media_type='text/css')
    response.headers['Cache-Control'] = 'public, max-age=300'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@router.get('/studio/assets/servgo-air-theme.js', include_in_schema=False)
def air_theme_script():
    response = Response((ASSETS/'servgo-air-theme.js').read_text(), media_type='application/javascript')
    response.headers['Cache-Control'] = 'public, max-age=300'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def artifact_content(content: str) -> str:
    """Readable structured results; raw model/transport JSON is not a UI."""
    labels = {'seeds':'Исходные запросы', 'clusters':'Группы запросов', 'queries':'Запросы',
              'sources':'Источники', 'engine':'Поисковая система', 'requests':'Запросов к источнику',
              'failed_requests':'Ошибок источника', 'queries_found':'Найдено формулировок',
              'collected_at':'Дата сбора', 'topic':'Тема', 'intent':'Намерение', 'stage':'Этап',
              'articles':'Статьи', 'slots':'План', 'title':'Заголовок', 'status':'Статус',
              'name':'Название', 'query':'Запрос', 'summary':'Итог', 'error':'Ошибка'}
    def render(value):
        if isinstance(value, dict):
            return '<dl>'+''.join('<dt>'+html.escape(labels.get(k, k.replace('_',' ')))+
                '</dt><dd>'+render(v)+'</dd>' for k,v in value.items())+'</dl>'
        if isinstance(value, list):
            return '<ul>'+''.join('<li>'+render(v)+'</li>' for v in value)+'</ul>'
        return html.escape(str(value)) if value is not None else '—'
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        value = content
    return '<details class="gv-result"><summary>Подробнее</summary>'+render(value)+'</details>'


def seo_run_card(row: dict) -> str:
    def state(label: str, done: bool) -> str:
        return (f'<li>{html.escape(label)}: '
                f'<strong>{"Есть результат" if done else "Пока нет результата"}</strong></li>')
    error = (f'<p role="status">Последняя ошибка: {html.escape(row["last_error"])}</p>'
             if row["last_error"] else '')
    queued = (f'<p>Статей поставлено в очередь: <b>{row["articles_queued"]}</b></p>'
              if row["articles_queued"] is not None else
              '<p>Количество поставленных статей пока не подтверждено.</p>')
    attempts = (f'<p>Попыток выполнения: {row["attempts"]}</p>'
                if row["attempts"] else '<p>Попытки выполнения ещё не зарегистрированы.</p>')
    return (
        f'<article class="panel seo-run" data-seo-status="{html.escape(row["status"], quote=True)}">'
        f'<div class="seo-run-head"><h3>{html.escape(row["title"])}</h3>'
        f'<span class="seo-run-status">{html.escape(row["status_label"])}</span></div>'
        f'<p>Последнее обновление: {html.escape(row["updated_at"] or "неизвестно")}</p>'
        + attempts + queued + error
        + '<details class="seo-run-details"><summary>Этапы и источники данных</summary>'
        + '<ul>'
        + state('Семантическое ядро', row['semantic_ready'])
        + state('SEO-контент-план', row['plan_ready'])
        + '</ul>'
        + (f'<p>Источников в итоговом отчёте: {row["sources_confirmed"]}</p>'
           if row["sources_confirmed"] is not None else
           '<p>Количество источников пока не подтверждено.</p>')
        + '<p>Стоимость: нет подтверждённых данных о списании провайдера.</p>'
        + '</details></article>'
    )


@router.get('/studio/products/{product_id}/seo/status')
def seo_status(product_id: UUID, db: Session = Depends(get_db)):
    """Existing task state, retry attempts, stage results; never fake spend."""
    _owner_product(db, product_id)
    return JSONResponse({'runs': seo_runs(db, product_id)},
                        headers={'Cache-Control': 'private, no-store'})


@router.get('/studio/products/{product_id}/seo', response_class=HTMLResponse)
def seo_page(product_id: UUID, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    runs = seo_runs(db, product_id)
    rows = list(db.scalars(select(Artifact).where(Artifact.product_id == product_id,
                      Artifact.kind.in_(['semantic_core', 'seo_content_plan', 'seo_factory_report']))
                      .order_by(Artifact.created_at.desc()).limit(100)))
    cards = ''.join(f'<article class="panel"><h3>{html.escape(row.title)}</h3>'
                    f'<p>{html.escape(row.kind)} · {html.escape(row.status.value)}</p>'
                    f'{artifact_content(row.content)}</article>' for row in rows)
    base = f'/studio/products/{product_id}'
    run_cards = ''.join(seo_run_card(row) for row in runs)
    return studio_page_shell(product, active='seo', title='SEO-завод',
        subtitle='Реальные статусы запусков, результаты и ошибки из базы вашей компании.',
        body=f'<p><a class="btn primary" href="{base}?design=v4&amp;command=seo_factory">'
             'Запросить запуск через диспетчер</a> '
             f'<a class="btn" href="{base}/work">Все задания</a> '
             f'<a class="btn" href="{base}/materials?kind=article">Статьи</a></p>'
             '<h2>Запуски SEO-завода</h2>'
             + (run_cards or '<p>Запуски SEO-завода пока не зарегистрированы.</p>')
             + '<h2>Сохранённые материалы и отчёты</h2>'
             + (cards or '<p>Материалы пока не подготовлены.</p>'))


@router.get('/studio/products/{product_id}/campaigns', response_class=HTMLResponse)
def campaigns_page(product_id: UUID, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    rows = list(db.scalars(select(Experiment).where(Experiment.product_id == product_id)
                          .order_by(Experiment.created_at.desc()).limit(100)))
    base = f'/studio/products/{product_id}'
    def card(row: Experiment) -> str:
        status = 'Черновик' if row.status == 'draft' else row.status
        budget = (f'{html.escape(str(row.budget))} {html.escape(row.currency or "")}'
                  if row.budget is not None else 'не задан')
        edit = (f'<a class="btn" href="{base}/campaigns/{row.id}/edit">'
                'Продолжить настройку</a>' if row.status == 'draft' else '')
        return (
            f'<article class="panel campaign-item" data-campaign-status="{html.escape(row.status, quote=True)}">'
            f'<div class="campaign-item-head"><h2>{html.escape(row.name)}</h2>'
            f'<span class="campaign-item-status">{html.escape(status)}</span></div>'
            f'<p>{html.escape(row.hypothesis)}</p>'
            f'<p>Аудитория: {html.escape(row.audience or "ещё не задана")}</p>'
            f'<p>Канал: {html.escape(row.channel or "ещё не выбран")}</p>'
            f'<p>Плановый бюджет: {budget}</p>'
            + (f'<p>Основной показатель: {html.escape(row.target_metric)}</p>'
               if row.target_metric else '')
            + edit + '</article>'
        )
    cards = ''.join(card(row) for row in rows)
    return studio_page_shell(product, active='campaigns', title='Кампании',
        subtitle='Эксперименты и планы вашей компании.', body=
        f'<p><a class="btn primary" href="{base}/campaigns/new">Новая кампания</a></p>'
        f'<p><a class="btn primary" href="{base}?design=v4&amp;command=campaign">Спланировать кампанию с командой</a> '
        f'<a class="btn" href="{base}/plan">Контент-план</a> '
        '<a class="btn" href="https://servgo.ru/mail">Почтовые кампании</a></p>'+
        (cards or '<p>Маркетинговых экспериментов пока нет.</p>'))


@router.get('/studio/products/{product_id}/v4/data')
def v4_data(product_id: UUID, db: Session = Depends(get_db)):
    """Company-scoped records for the reviewed board/library components."""
    _owner_product(db, product_id)
    tasks = list(db.scalars(select(Task).where(Task.product_id == product_id)
                           .order_by(Task.created_at.desc()).limit(200)))
    materials = list(db.scalars(select(Artifact).where(Artifact.product_id == product_id, Artifact.kind.in_(MATERIAL_KINDS))
                               .order_by(Artifact.created_at.desc()).limit(200)))
    from fastapi.responses import JSONResponse
    return JSONResponse({'tasks': [dict(id=str(t.id),title=t.title,status=t.status.value,
            group=t.assigned_agent or 'ИИ-команда',detail=t.description or '') for t in tasks],
        'materials': [dict(id=str(a.id),title=a.title,kind=a.kind,status=a.status.value,
                          description='') for a in materials]},
        headers={'Cache-Control':'private, no-store'})
