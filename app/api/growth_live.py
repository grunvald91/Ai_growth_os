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
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.api.content_studio import MATERIAL_KINDS, _owner_product, studio_page_shell
from app.db.session import get_db
from app.models.domain import Artifact, Experiment, Task
from app.api.growth_campaigns import campaign_list_card
from app.services.campaign_readiness import load_resources

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
    # The company chooser contains links to multiple businesses. Its first
    # card is not an active tenant, and must not become the V4 navigation scope.
    if not match and request.url.path != '/studio':
        match = re.search(r'/studio/products/([0-9a-fA-F-]{36})', page)
    pid = UUID(match[1]) if match else None
    from app.api.growth_v4 import render_v4
    return render_v4(page, request, pid, switch)


class GrowthDesignMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        # SSO landing and consent are protocol endpoints, not studio pages.
        is_sso = path == '/studio/sso' or path.startswith('/studio/sso/')
        eligible = (path == '/intelligence' or path.startswith('/studio')) and not is_sso and not any(
            part in path for part in ('/assets/', '/login', '/logout', '/design-preview'))
        from app.api.studio_sso import _canonical_pwa
        from app.core.config import get_settings
        setting_provider = request.app.dependency_overrides.get(get_settings, get_settings)
        canonical = _canonical_pwa(request, setting_provider())
        choice = request.query_params.get('design')
        preference = choice if choice in {'v4', 'classic'} else request.cookies.get(COOKIE)
        # Canonical ServGo starts on v4, while an explicit Classic choice
        # and the old Growth domain both retain their existing behavior.
        new = preference == 'v4' or (preference not in {'v4', 'classic'} and canonical)
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

@router.get('/studio/assets/create-studio.js')
def create_studio_javascript():
    return Response((ASSETS/'create-studio.js').read_text(), media_type='application/javascript',
                    headers={'Cache-Control': 'no-cache'})


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


@router.get('/studio/products/{product_id}/seo', response_class=HTMLResponse)
def seo_page(product_id: UUID, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    rows = list(db.scalars(select(Artifact).where(Artifact.product_id == product_id,
                      Artifact.kind.in_(['semantic_core', 'seo_content_plan', 'seo_factory_report'])).order_by(Artifact.created_at.desc()).limit(100)))
    cards = ''.join(f'<article class="panel"><h2>{html.escape(row.title)}</h2>'
                    f'<p>{html.escape(row.kind)} · {html.escape(row.status.value)}</p>'
                    f'{artifact_content(row.content)}</article>' for row in rows)
    base = f'/studio/products/{product_id}'
    return studio_page_shell(product, active='seo', title='SEO-завод',
        subtitle='Семантика, планы и отчёты из базы вашей компании.',
        body=f'<p><a class="btn primary" href="{base}?design=v4&amp;command=seo_factory">Запустить SEO-завод</a> '
             f'<a class="btn" href="{base}/materials?kind=article">Статьи</a></p>'+
             (cards or '<p>SEO-материалов пока нет. Запустите сбор через диспетчер.</p>'))


@router.get('/studio/products/{product_id}/campaigns', response_class=HTMLResponse)
def campaigns_page(product_id: UUID, page: int = 1, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    page = max(1, min(page, 1000000))
    page_size = 50
    rows = list(db.scalars(select(Experiment).where(Experiment.product_id == product_id)
                          .order_by(Experiment.created_at.desc(), Experiment.id.desc())
                          .offset((page - 1) * page_size).limit(page_size + 1)))
    has_next = len(rows) > page_size
    rows = rows[:page_size]
    resources = load_resources(db, rows)
    cards = ''.join(campaign_list_card(product_id, row, resources[row.id]) for row in rows)
    base = f'/studio/products/{product_id}'
    previous = (f'<a class="btn" href="{base}/campaigns?page={page - 1}">Предыдущая</a>'
                if page > 1 else '')
    following = (f'<a class="btn" href="{base}/campaigns?page={page + 1}">Следующая</a>'
                 if has_next else '')
    pager = (f'<nav aria-label="Страницы кампаний">{previous} '
             f'Страница {page} {following}</nav>' if previous or following else '')
    return studio_page_shell(product, active='campaigns', title='Кампании',
        subtitle='Для чего запускаем продвижение, что сделаем и чего ждём.', body=
        f'<p><a class="btn primary" href="{base}?design=v4&amp;command=campaign">Спланировать с ИИ</a> '
        f'<a class="btn" href="{base}/campaigns/new">Создать вручную</a></p>'
        '<details><summary>Другие инструменты продвижения</summary>'
        f'<p><a href="{base}/plan">Контент-план</a> · '
        f'<a href="{base}/channels">Публикации</a></p></details>'+
        (cards or '<p>На этой странице кампаний нет.</p>') + pager)



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
