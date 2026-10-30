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
from app.api.content_studio import _owner_product, more_links, studio_page_shell
from app.api.content_studio_brand import servgo_brand_html
from app.db.session import get_db
from app.models.domain import Artifact, Experiment

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
    base = f'/studio/products/{pid}' if pid else '/studio'
    links = [('Главная', base), ('Создать', base+'/create'), ('Задания', base+'/work'),
             ('Согласование', base+'/review'), ('Материалы', base+'/materials'),
             ('Кампании', base+'/campaigns'), ('SEO-завод', base+'/seo')]
    if pid:
        links += [(('Простое видео' if key=='whiteboard' else label), href)
                  for key, href, _, label in more_links(pid)
                  if key not in {'review', 'logout'}]
    else:
        links = [('Компании', '/studio?choose=1'), ('ИИ-команда', '/studio/team')]
    def link(label, href):
        active = ' aria-current="page"' if request.url.path == href.split('?')[0] else ''
        return f'<a href="{html.escape(href, quote=True)}"{active}>{html.escape(label)}</a>'
    nav = ''.join(link(*item) for item in links)
    mobile = ''.join(link(*item) for item in links[:3])
    chrome = (f'<div class="gv-layout"><aside class="gv-sidebar">{servgo_brand_html(href=base)}'
              f'<nav aria-label="Разделы Growth OS">{nav}</nav></aside><section class="gv-workspace">'
              f'<header class="gv-header"><span>{servgo_brand_html(compact=True, href=base)}</span>'
              f'<a href="{switch}">Старый дизайн</a>'
              '<button type="button" id="gv-theme" aria-label="Переключить тему">◐</button></header>')
    page = page.replace('</head>', '<link rel="stylesheet" href="/studio/assets/growth-live.css?v=1"></head>')
    page = re.sub(r'<body([^>]*)>', lambda m: '<body'+m[1]+' data-growth-design="v4">'+chrome, page, count=1)
    footer = ('</section></div><nav class="gv-mobile" aria-label="Быстрая навигация">'+mobile+
              '<details><summary>Ещё</summary><nav aria-label="Все разделы">'+nav+'</nav></details></nav>'
              '<script src="/studio/assets/growth-live.js?v=1"></script>')
    return page.replace('</body>', footer+'</body>')


class GrowthDesignMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        eligible = (path == '/intelligence' or path.startswith('/studio')) and not any(
            part in path for part in ('/assets/', '/login', '/logout', '/sso/', '/design-preview'))
        choice = request.query_params.get('design')
        new = (choice if choice in {'v4', 'classic'} else request.cookies.get(COOKIE)) == 'v4'
        if eligible and request.method in {'GET', 'POST'}:
            if response.status_code in {200, 422} and 'text/html' in response.headers.get('content-type', ''):
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
def campaigns_page(product_id: UUID, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    rows = list(db.scalars(select(Experiment).where(Experiment.product_id == product_id)
                          .order_by(Experiment.created_at.desc()).limit(100)))
    cards = ''.join(f'<article class="panel"><h2>{html.escape(row.name)}</h2>'
                    f'<p>{html.escape(row.status)} · {html.escape(row.channel or "Канал не задан")}</p>'
                    f'<p>{html.escape(row.hypothesis)}</p><p>Бюджет: '
                    f'{html.escape(str(row.budget)) if row.budget is not None else "не задан"}</p></article>'
                    for row in rows)
    base = f'/studio/products/{product_id}'
    return studio_page_shell(product, active='campaigns', title='Кампании',
        subtitle='Эксперименты и планы вашей компании.', body=
        f'<p><a class="btn primary" href="{base}?design=v4&amp;command=campaign">Спланировать кампанию с командой</a> '
        f'<a class="btn" href="{base}/plan">Контент-план</a> '
        '<a class="btn" href="https://servgo.ru/mail">Почтовые кампании</a></p>'+
        (cards or '<p>Маркетинговых экспериментов пока нет.</p>'))
