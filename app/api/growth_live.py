"""Opt-in v4 presentation over the existing authenticated Studio handlers.

No proxy calls or duplicate write APIs: forms, scripts, CSRF and permissions stay
with the handlers that already own each workflow.
"""
from __future__ import annotations

import html
import json
import httpx
import re
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.api.content_studio import MATERIAL_KINDS, _owner_product, _studio_form, studio_page_shell
from app.db.session import get_db
from app.core.config import get_settings
from app.models.domain import Artifact, Experiment, Task
from app.api.growth_campaigns import campaign_list_card
from app.services.campaign_readiness import load_resources
from app.api.seo_run_view import seo_runs
from app.api.seo_plan_board import seo_plan_board, visible_plan_run
from app.seo.control import SeoControlError, create_seo_run, change_seo_run
from app.seo.article_retry import retry_failed_article
from app.seo.activation import (is_enabled, set_enabled, editorial_preferences,
                                set_editorial_preferences)
from app.seo.visibility import VisibilityError, change_article_visibility
from app.seo.insights import company_seo_insights
from app.seo.search_metrics import company_search_metrics, inspect_google_article
from app.models.domain import ArtifactStatus, Publication, Product
from app.workflows.article_contracts import ArticleArtifact
from app.sso.models import StudioBusinessLink

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
    # A multi-company chooser must never borrow tenant scope from its first card.
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
            if new and request.method == 'GET' and response.status_code == 200 and 'text/html' in response.headers.get('content-type', ''):
                company = re.match(r'^/studio/products/([0-9a-f-]{36})(?:/|$)', path, re.I)
                if company:
                    response.set_cookie('studio_last_product', company[1], path='/studio',
                                        httponly=True, samesite='lax', secure=canonical or request.url.scheme == 'https',
                                        max_age=31536000)
        return response


@router.get('/studio/assets/growth-live.css')
def stylesheet():
    return Response((ASSETS/'growth-live.css').read_text(), media_type='text/css')


@router.get('/studio/assets/growth-daily.css')
def daily_stylesheet():
    return Response((ASSETS/'growth-daily.css').read_text(), media_type='text/css',
                    headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'})


@router.get('/studio/assets/growth-daily.js')
def daily_javascript():
    return Response((ASSETS/'growth-daily.js').read_text(), media_type='application/javascript',
                    headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'})


@router.get('/studio/assets/growth-live.js')
def javascript():
    return Response((ASSETS/'growth-live.js').read_text(), media_type='application/javascript')

@router.get('/studio/assets/voice-glow.js')
def voice_glow_javascript():
    return Response((ASSETS/'voice-glow.js').read_text(), media_type='application/javascript',
                    headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'})


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


def seo_run_card(row: dict, product_id: UUID | None = None, *, blog_base: str | None = None, enabled: bool = True) -> str:
    def state(label: str, done: bool) -> str:
        return (f'<li>{html.escape(label)}: '
                f'<strong>{"Есть результат" if done else "Пока нет результата"}</strong></li>')
    stage_names = {
        "starting": "Запуск обработчика",
        "prepare_queries": "Подготовка поисковых запросов",
        "collect_suggestions": "Получение реальных поисковых подсказок",
        "build_plan": "Создание SEO-плана",
        "queue_articles": "Постановка статей в очередь",
    }
    error_names = {
        "search_no_suggestions": "Поисковики не дали подсказок для выбранных формулировок",
        "search_invalid_responses": "Поисковые источники вернули некорректный формат",
        "search_upstream_failures": "Не удалось получить подсказки от поисковых источников",
        "model_unusable_response": "Модель не вернула пригодный ответ",
        "plan_validation_failed": "SEO-план от ИИ не прошёл проверку соответствия реальным поисковым данным",
        "provider_http_error": "Ошибка провайдера ИИ",
        "upstream_timeout": "Превышено время ожидания",
        "network_failure": "Сетевая ошибка",
        "invalid_data_or_configuration": "Некорректные данные или настройка",
        "internal_exception": "Внутренняя ошибка",
    }
    current_stage = row.get("current_stage") or row.get("last_error_stage")
    stage = (f'<p>Этап: {html.escape(stage_names.get(current_stage, current_stage))}</p>'
             if current_stage else '')
    code = row.get("last_error_code")
    error_caption = error_names.get(code, code or row.get("last_error") or "")
    plan_issue_names = {
        "unknown_cluster": "указана несуществующая группа запросов",
        "cluster_stage_intent_mismatch": "не совпадают этап воронки и намерение запроса",
        "query_not_in_collected_clusters": "ключевой запрос не найден среди собранных подсказок",
        "duplicate_or_previously_written_query": "повторена тема, которая уже запланирована или написана",
        "no_fresh_clusters": "новых тем в поисковой семантике не осталось",
        "empty_company_profile": "не заполнен профиль компании",
        "other_plan_validation": "ошибка соответствия плана проверенным данным",
    }
    plan_issue = row.get("last_error_plan_issue")
    if plan_issue and row.get("status") == "failed":
        error_caption += ": " + plan_issue_names.get(plan_issue, plan_issue)
    error = (f'<p role="status">Последняя ошибка: {html.escape(error_caption)}</p>'
             if row.get("last_error") and row.get("status") == "failed" else '')
    search_progress = ''.join(
        '<li>' + html.escape(str(engine)) + ': '
        + 'запросов ' + html.escape(str(info.get("requests", 0)))
        + ', подсказок ' + html.escape(str(info.get("queries_found", 0)))
        + ', пустых ответов ' + html.escape(str(info.get("empty", 0)))
        + ', ошибок ' + html.escape(str(info.get("failed", 0)))
        + ', некорректных ответов ' + html.escape(str(info.get("invalid", 0)))
        + '</li>'
        for engine, info in (row.get("source_progress") or {}).items()
    )
    search_progress = ('<details><summary>Поисковые источники</summary><ul>'
                       + search_progress + '</ul></details>') if search_progress else ''
    local_status = row.get("local_market") or {}
    local_labels = {
        "collected": "собраны сведения об организациях",
        "unavailable": "источник временно недоступен",
        "skipped": "поиск пропущен: недостаточно сведений о компании",
    }
    local_state = local_status.get("status")
    local_text = local_labels.get(local_state, "")
    if local_status.get("reason"):
        local_text += " · " + str(local_status["reason"])[:180]
    local_progress = (
        '<p>Яндекс Карты · локальное SEO: ' + html.escape(local_text) + '</p>'
        if local_text else ""
    )
    queued = (f'<p>Статей поставлено в очередь: <b>{row["articles_queued"]}</b></p>'
              if row["articles_queued"] is not None else
              '<p>Количество поставленных статей пока не подтверждено.</p>')
    deferred = row.get("articles_deferred")
    if isinstance(deferred, int) and deferred > 0:
        queued += (f'<p>Осталось в SEO-плане без генерации: <b>{deferred}</b>. '
                   'Следующая партия запускается отдельно.</p>')
    attempts = (f'<p>Попыток выполнения: {row["attempts"]}</p>'
                if row["attempts"] else '<p>Попытки выполнения ещё не зарегистрированы.</p>')
    base = f'/studio/products/{product_id}/seo' if product_id else ''
    actions = ''
    if base and enabled and row["status"] in {"queued", "failed", "cancelled"}:
        operation = "cancel" if row["status"] == "queued" else "retry"
        caption = "Отменить из очереди" if operation == "cancel" else "Повторить запуск"
        actions = (
            f'<form method="post" action="{base}/{row["id"]}/{operation}" class="seo-run-actions">'
            f'<button class="btn secondary" type="submit">{caption}</button></form>'
        )
    article_labels = {
        "queued": "Ожидает генерации", "running": "Создаётся",
        "completed": "Обработка завершена", "failed": "Ошибка создания",
        "cancelled": "Отменено",
    }
    saved_labels = {
        "draft": "Черновик", "awaiting_approval": "На согласовании",
        "approved": "Согласована", "published": "Опубликована",
        "rejected": "Отклонена",
    }
    critic_labels = {
        "no_material_issues": "существенных замечаний нет",
        "revised": "замечания учтены, статья доработана",
        "all_rejected": "замечания отклонены автором",
        "rejected_domain_revision": "доработка не прошла проверку",
        "review_unavailable": "проверка не завершена",
        "revision_unavailable": "доработка не завершена",
        "revision_pending": "ожидает доработки",
        "revision_cancelled": "доработка прервана",
        "skipped_incomplete_context": "проверка пропущена: неполный контекст",
        "skipped_incomplete_original": "проверка пропущена: ответ оборван",
        "skipped_size_limit": "проверка пропущена: ограничение размера",
        "skipped_invalid_original": "проверка пропущена: неверный формат",
        "missing": "нет подтверждённого результата",
    }
    article_cards = []
    if base:
        for article in row.get("articles", []):
            if article.get("published_confirmed") or article.get("publication_path"):
                status = "Опубликована"
            elif article.get("task_status") == "failed":
                status = "Ошибка создания"
            else:
                status = (saved_labels.get(article["article_status"], article["article_status"])
                          if article["article_status"] else
                          article_labels.get(article["task_status"], article["task_status"]))
            target = (f'/studio/products/{product_id}/materials/{article["article_id"]}'
                      if article["article_id"] else
                      f'/studio/products/{product_id}/tasks/{article["task_id"]}')
            extra = ''
            pub = article.get("publication_path")
            if pub and pub.startswith("/blog/"):
                if blog_base:
                    extra = (f'<a href="{html.escape(blog_base + pub, quote=True)}"'
                             ' target="_blank" rel="noopener noreferrer">Посмотреть в блоге</a>')
                else:
                    extra = '<span>Запись о публикации есть; адрес сайта этой компании не подключён</span>'
            critic = ''
            article_id = article.get("article_id")
            if article_id:
                verdict = article.get("critic_status", "missing")
                label = critic_labels.get(verdict, "статус требует проверки")
                critic = (f'<p class="seo-critic-result">Критик: '
                          f'{html.escape(label)}</p>')
                findings = article.get("critic_findings") or []
                total = article.get("critic_findings_total", len(findings))
                if findings:
                    issues = ''.join(f'<li>{html.escape(str(issue))}</li>' for issue in findings)
                    critic += (f'<details class="seo-critic-findings"><summary>'
                               f'Замечания критика ({total})</summary><ul>{issues}</ul></details>')
                if article.get("approval_reason") and article.get("article_status") == "awaiting_approval":
                    critic += ('<p class="seo-critic-reason">Причина согласования: ' +
                               html.escape(article["approval_reason"][:600]) + '</p>')
                if article.get("article_status") == "awaiting_approval":
                    critic += (f'<a class="btn secondary" href="{html.escape(target, quote=True)}">'
                               'Открыть статью и проверить</a>')
                    critic += '<p>Статья не прошла автоматическую проверку. Причина указана выше.</p>'
            scheduled = article.get("scheduled_at")
            scheduled_note = (
                '<span>Плановая дата: ' + html.escape(str(scheduled))
                + ' (' + html.escape(row.get("timezone_name") or "Europe/Moscow") + ')</span>'
                if scheduled else ''
            )
            article_cards.append(
                '<li class="seo-article-item">'
                f'<div class="seo-article-copy"><a href="{html.escape(target, quote=True)}">'
                f'{html.escape(article["title"])}</a>'
                f'<span>{html.escape(status or "Статус неизвестен")}</span>'
                + scheduled_note + critic + extra + '</div></li>'
            )
    article_list = (
        f'<details class="seo-run-articles"><summary>Статьи ({len(article_cards)})</summary>'
        '<ul>' + ''.join(article_cards) + '</ul></details>'
        if article_cards else ''
    )
    # A collapsed article list must not hide the release calendar from owners.
    scheduled_articles = sorted(
        (article for article in row.get("articles", []) if article.get("scheduled_at")),
        key=lambda article: article.get("scheduled_iso") or "",
    )
    if scheduled_articles:
        preview_items = []
        for article in scheduled_articles[:5]:
            if article.get("published_confirmed") or article.get("publication_path"):
                current_status = "Опубликована"
            elif article.get("task_status") == "failed":
                current_status = "Ошибка создания"
            elif article.get("article_status") == "awaiting_approval":
                current_status = "Ждёт проверки"
            elif article.get("task_status") == "queued":
                current_status = "Ожидает генерации"
            elif article.get("task_status") == "running":
                current_status = "Создаётся"
            else:
                current_status = "В обработке"
            preview_items.append(
                '<li><strong>' + html.escape(str(article.get("title") or "Статья"))
                + '</strong><span>' + html.escape(str(article["scheduled_at"]))
                + ' · ' + html.escape(current_status) + '</span></li>'
            )
        remainder = (f'<p>Ещё статей: {len(scheduled_articles) - 5}. '
                     'Полный список находится ниже.</p>'
                     if len(scheduled_articles) > 5 else '')
        schedule_preview = (
            '<section class="seo-schedule-preview" aria-label="Расписание SEO-статей">'
            '<h4>Плановые даты SEO-статей</h4><p>Часовой пояс: '
            + html.escape(row.get("timezone_name") or "Europe/Moscow")
            + '. Это время начала подготовки; выход статьи зависит от генерации и проверки.</p>'
            + '<ul>' + ''.join(preview_items) + '</ul>' + remainder + '</section>'
        )
    else:
        schedule_preview = (
            '<p class="seo-schedule-empty">Плановые даты статей пока не сформированы. '
            'Проверьте состояние SEO-запуска и постановку статей в очередь.</p>'
        )
    return (
        f'<article class="panel seo-run" data-seo-status="{html.escape(row["status"], quote=True)}">'
        f'<div class="seo-run-head"><h3>{html.escape(row["title"])}</h3>'
        f'<span class="seo-run-status">{html.escape(row["status_label"])}</span></div>'
        f'<p>Последнее обновление: {html.escape(row["updated_at"] or "неизвестно")}</p>'
        + stage + attempts + queued + error + search_progress + local_progress
        + '<details class="seo-run-details"><summary>Этапы и источники данных</summary>'
        + '<ul>'
        + state('Семантическое ядро', row['semantic_ready'])
        + state('SEO-контент-план', row['plan_ready'])
        + '</ul>'
        + (f'<p>Источников в итоговом отчёте: {row["sources_confirmed"]}</p>'
           if row["sources_confirmed"] is not None else
           '<p>Количество источников пока не подтверждено.</p>')
        + '<p>Стоимость: нет подтверждённых данных о списании провайдера.</p>'
        + '</details>'
        + schedule_preview
        + article_list
        + actions
        + ('<p class="seo-run-hint">Запущенное выполнение нельзя прервать этой кнопкой.</p>'
           if row['status'] == 'running' else '')
        + '</article>'
    )



@router.get('/studio/products/{product_id}/seo/queue', response_class=HTMLResponse)
def seo_queue_confirm(product_id: UUID, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    if not is_enabled(db, product_id):
        return RedirectResponse(f'/studio/products/{product_id}/seo', status_code=303)
    base = f'/studio/products/{product_id}/seo'
    body = (
        '<section class="panel seo-queue-confirm">'
        '<h2>Новый запуск SEO-завода</h2>'
        '<p>Завод подготовит поисковые запросы, SEO-план и задания на статьи. '
        'Обработка после постановки в очередь может использовать платные модели. '
        'Выберите план на неделю или месяц; все уместные темы '
        'попадут в очередь без искусственного лимита числа статей. '
        'Статьи с положительным заключением ИИ-критика и без ошибок проверки '
        'автоматически публикуются в блоге компании. '
        'Только материалы с замечаниями остаются на проверке. '
        'Отмена запуска в очереди не удаляет созданные статьи и не отменяет '
        'уже поставленные отдельно задания на статьи.</p>'
        f'<form method="post" action="{base}/queue">'
        '<label class="field" for="seo-focus">Тема или направление (необязательно)</label>'
        '<input id="seo-focus" name="focus" maxlength="300"'
        ' placeholder="Например, онлайн-запись для салонов красоты">'
        '<label class="field" for="seo-horizon">Период контент-плана</label>'
        '<select id="seo-horizon" name="horizon_days">'
        '<option value="7">На 7 дней</option>'
        '<option value="30">На месяц (30 дней)</option>'
        '</select>'
        '<div class="seo-run-actions"><button class="btn primary" type="submit">'
        'Подтвердить постановку в очередь</button>'
        f'<a class="btn" href="{base}">Вернуться без запуска</a></div></form>'
        '</section>'
    )
    return studio_page_shell(product, active='seo', title='Новый запуск SEO-завода',
                             subtitle='Подтвердите создание задания.', body=body)


@router.post('/studio/products/{product_id}/seo/activation')
async def seo_activation(product_id: UUID, request: Request, db: Session = Depends(get_db)):
    """Explicit owner action. Never enable SEO while creating or viewing a company."""
    _owner_product(db, product_id)
    if getattr(request.state, 'studio_sso_role', None) not in (None, 'owner', 'co_owner'):
        raise HTTPException(403, 'Подключать SEO-завод может только владелец бизнеса')
    form = await _studio_form(request)
    action = form.get('action')
    if action not in ('enable', 'disable'):
        raise HTTPException(422, 'Некорректное действие')
    set_enabled(db, product_id, enabled=action == 'enable',
                actor=getattr(request.state, 'studio_login', 'studio_owner'))
    return RedirectResponse(f'/studio/products/{product_id}/seo', status_code=303)


@router.post('/studio/products/{product_id}/seo/preferences')
async def seo_preferences_submit(product_id: UUID, request: Request, db: Session = Depends(get_db)):
    """Change only the authenticated owner's company preferences; never opt in implicitly."""
    _owner_product(db, product_id)
    if getattr(request.state, 'studio_sso_role', None) not in (None, 'owner', 'co_owner'):
        raise HTTPException(403, 'Настройки SEO доступны владельцу компании')
    values = await _studio_form(request)
    try:
        timezone_name = str(values.get('timezone_name', ''))
        interval = int(values.get('autorun_interval_days', ''))
        horizon = int(values.get('autorun_horizon_days', ''))
        published = int(values.get('publications_per_day', ''))
        articles = int(values.get('articles_per_day', ''))
        set_editorial_preferences(
            db, product_id, timezone_name=timezone_name,
            autorun_interval_days=interval, autorun_horizon_days=horizon,
            publications_per_day=published, articles_per_day=articles,
            actor=getattr(request.state, 'studio_login', 'studio_owner'))
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc) if str(exc) else 'Проверьте настройки расписания') from None
    return RedirectResponse(f'/studio/products/{product_id}/seo', status_code=303)


@router.post('/studio/products/{product_id}/seo/queue')
async def seo_queue_submit(product_id: UUID, request: Request,
                           db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    values = await _studio_form(request)
    focus = values.get('focus', '')
    if not isinstance(focus, str) or len(focus) > 300:
        raise HTTPException(422, 'Уточните тему SEO-завода')
    horizon = values.get("horizon_days", "7")
    if not isinstance(horizon, str) or horizon not in ("7", "30"):
        raise HTTPException(422, 'Период SEO-плана должен быть 7 или 30 дней')
    try:
        create_seo_run(db, product_id=product_id, focus=focus, horizon_days=int(horizon))
    except LookupError:
        raise HTTPException(404, 'Компания не найдена') from None
    except SeoControlError as exc:
        raise HTTPException(422, str(exc)) from None
    return RedirectResponse(f'/studio/products/{product_id}/seo', status_code=303)


@router.post('/studio/products/{product_id}/seo/{task_id}/cancel')
def seo_cancel_queued(product_id: UUID, task_id: UUID, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    try:
        change_seo_run(db, product_id=product_id, task_id=task_id, action='cancel')
    except LookupError:
        raise HTTPException(404, 'Запуск SEO-завода не найден') from None
    except SeoControlError as exc:
        raise HTTPException(409, str(exc)) from None
    return RedirectResponse(f'/studio/products/{product_id}/seo', status_code=303)


@router.post('/studio/products/{product_id}/seo/{task_id}/retry')
def seo_retry_run(product_id: UUID, task_id: UUID, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    try:
        change_seo_run(db, product_id=product_id, task_id=task_id, action='retry')
    except LookupError:
        raise HTTPException(404, 'Запуск SEO-завода не найден') from None
    except SeoControlError as exc:
        raise HTTPException(409, str(exc)) from None
    return RedirectResponse(f'/studio/products/{product_id}/seo', status_code=303)


@router.post('/studio/products/{product_id}/seo/articles/{task_id}/retry')
def seo_article_retry(product_id: UUID, task_id: UUID, request: Request,
                      db: Session = Depends(get_db)):
    """An explicit owner click; never restart the whole SEO factory."""
    _owner_product(db, product_id)
    if getattr(request.state, 'studio_sso_role', None) not in (None, 'owner', 'co_owner'):
        raise HTTPException(403, 'Повторять SEO-статьи может только владелец компании')
    try:
        retry_failed_article(db, product_id=product_id, task_id=task_id,
                             actor=getattr(request.state, 'studio_login', 'studio_owner'))
    except LookupError:
        raise HTTPException(404, 'SEO-статья не найдена') from None
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    return RedirectResponse(f'/studio/products/{product_id}/seo', status_code=303)


@router.post('/studio/products/{product_id}/seo/articles/{article_id}/{action}')
def seo_article_visibility(product_id: UUID, article_id: UUID, action: str,
                           request: Request, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    if getattr(request.state, 'studio_sso_role', None) not in (None, 'owner', 'co_owner'):
        raise HTTPException(403, 'Управлять видимостью статьи может только владелец бизнеса')
    try:
        change_article_visibility(
            db, product_id=product_id, article_id=article_id, action=action,
            actor=getattr(request.state, 'studio_login', 'studio_owner'))
    except LookupError:
        raise HTTPException(404, 'Статья не найдена') from None
    except VisibilityError as exc:
        raise HTTPException(409, str(exc)) from None
    return RedirectResponse(f'/studio/products/{product_id}/seo', status_code=303)


def seo_published_articles(db: Session, product_id: UUID, enabled: bool) -> str:
    """A compact owner view of public and hidden pages, never of other companies."""
    published = list(db.execute(
        select(Publication, Artifact).join(Artifact, Publication.artifact_id == Artifact.id)
        .where(Publication.product_id == product_id, Artifact.product_id == product_id,
               Publication.channel == "own_site",
               Publication.status.in_(["published", "unpublished"]),
               Artifact.kind == "article")
        .order_by(Publication.published_at.desc(), Publication.id.desc()).limit(100)
    ))
    if not published:
        return '<p>Опубликованных или скрытых статей пока нет.</p>'
    business_id = db.scalar(select(StudioBusinessLink.servgo_business_id).where(
        StudioBusinessLink.product_id == product_id).limit(1))
    items = []
    for publication, article in published:
        try:
            title = ArticleArtifact.model_validate_json(article.content).reader_article.title
        except (TypeError, ValueError, AttributeError):
            title = article.title
        public_url = None
        slug = (publication.external_reference or "").removeprefix("/blog/")
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            if business_id is not None:
                public_url = f'https://servgo.ru/business/{business_id}/blog/{slug}'
            elif db.scalar(select(Product.slug).where(Product.id == product_id)) == "servgo":
                public_url = f'https://servgo.ru/blog/{slug}'
            else:
                # A standalone Growth product may not have a BookingBot public route yet.
                public_url = None
        visible = publication.status == "published" and article.status == ArtifactStatus.PUBLISHED
        action = 'hide' if visible else 'restore'
        button = 'Снять с публикации' if visible else 'Вернуть в блог'
        links = (f'<a href="{html.escape(public_url, quote=True)}" '
                 'target="_blank" rel="noopener noreferrer">Открыть статью</a>'
                 if visible and public_url else
                 '<span>Ссылка на публичную страницу появится после подключения бизнеса.</span>'
                 if visible else '<span>Страница скрыта от публичного блога и sitemap.</span>')
        items.append(
            '<li><strong>' + html.escape(title) + '</strong> '
            + ('<span>Опубликована</span>' if visible else '<span>Скрыта</span>')
            + ' · ' + links
            + f'<form method="post" action="/studio/products/{product_id}/seo/articles/{article.id}/{action}">'
            + f'<button class="btn secondary" type="submit"{"" if visible or enabled else " disabled"}>'
            + button + '</button></form></li>'
        )
    return '<ul class="seo-article-visibility">' + ''.join(items) + '</ul>'


@router.post('/studio/products/{product_id}/seo/inspect', response_class=HTMLResponse)
async def seo_inspect_published_url(product_id: UUID, request: Request,
                                    db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    form = await _studio_form(request)
    url = form.get("url", "")
    if not isinstance(url, str) or len(url) > 500:
        raise HTTPException(422, 'Укажите адрес опубликованной статьи')
    async with httpx.AsyncClient(follow_redirects=False) as client:
        try:
            data = await inspect_google_article(db, product_id, url=url, client=client)
        except LookupError:
            raise HTTPException(404, 'Статья этого бизнеса не найдена') from None
    status = data["status"]
    if status != "connected":
        message = ('Google Search Console ещё не подключён.' if status == 'not_configured'
                   else 'Google временно не вернул данные об индексировании.')
    else:
        result = data["indexing"]
        names = {"verdict": "Вердикт Google", "coverageState": "Состояние в индексе",
                 "lastCrawlTime": "Последний известный обход",
                 "robotsTxtState": "robots.txt", "indexingState": "Разрешение индексации",
                 "pageFetchState": "Загрузка страницы",
                 "googleCanonical": "Canonical Google",
                 "userCanonical": "Canonical страницы"}
        message = '<ul>' + ''.join('<li><strong>' + label + '</strong>: '
                 + html.escape(str(result[key])) + '</li>'
                 for key, label in names.items() if result.get(key) is not None) + '</ul>'
    body = ('<p><a href="/studio/products/' + str(product_id)
            + '/seo/search-metrics">← К поисковой аналитике</a></p>'
            + '<p>Проверена только эта опубликованная страница: '
            + html.escape(url) + '</p>' + message
            + '<p>Это состояние версии в индексе Google, '
              'а не проверка доступности сайта в реальном времени. '
              'Цитирование в AI-ответах не подтверждается.</p>')
    return studio_page_shell(product, active='seo', title='Индексирование Google',
                             subtitle='Проверка конкретной статьи', body=body)


@router.get('/studio/products/{product_id}/seo/search-metrics.json')
async def seo_search_metrics_api(product_id: UUID, days: int = 28,
                                 db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    if not 1 <= days <= 90:
        raise HTTPException(422, 'Период: от 1 до 90 дней')
    return JSONResponse(await company_search_metrics(db, product_id, days=days),
                        headers={'Cache-Control': 'private, no-store'})


@router.get('/studio/products/{product_id}/seo/search-metrics', response_class=HTMLResponse)
async def seo_search_metrics_page(product_id: UUID, days: int = 28,
                                  db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    if not 1 <= days <= 90:
        raise HTTPException(422, 'Период: от 1 до 90 дней')
    metrics = await company_search_metrics(db, product_id, days=days)
    names = [('google_search_console', 'Google Search Console'),
             ('yandex_webmaster', 'Яндекс Вебмастер')]
    cards = []
    for source, name in names:
        data = metrics[source]
        if data['status'] != 'connected':
            labels = {'not_configured': 'Источник пока не подключён',
                      'no_public_articles': 'Нет опубликованных статей с доступными URL',
                      'provider_unavailable': 'Не удалось получить данные провайдера'}
            cards.append('<section class="panel"><h3>' + name + '</h3><p>'
                         + html.escape(labels.get(data['status'], 'Данные недоступны'))
                         + '</p></section>')
            continue
        entries = []
        for item in data['pages']:
            url = str(item['url'])
            entries.append('<li><a href="' + html.escape(url, quote=True)
                           + '" rel="noopener noreferrer" target="_blank">'
                           + html.escape(url.removeprefix('https://servgo.ru'))
                           + '</a> · клики: ' + str(item['clicks'])
                           + ', показы: ' + str(item['impressions'])
                           + '<form method="post" action="/studio/products/'
                           + str(product_id) + '/seo/inspect">'
                           + '<input type="hidden" name="url" value="'
                           + html.escape(url, quote=True) + '">'
                           + '<button type="submit" class="btn">Проверить индекс Google</button>'
                           + '</form></li>')
        cards.append('<section class="panel"><h3>' + name + '</h3>'
                     + '<p>Данные за ' + str(data['period_days'])
                     + ' дн. Только опубликованные статьи этого бизнеса.</p>'
                     + '<p>Клики по доступным строкам: <strong>' + str(data['clicks'])
                     + '</strong>; показы: <strong>' + str(data['impressions'])
                     + '</strong>.</p>'
                     + '<p>API может возвращать неполную выборку страниц — '
                       'это не гарантированные общие показатели.</p>'
                     + '<ul>' + ''.join(entries) + '</ul></section>')
    content = ('<p><a href="/studio/products/' + str(product_id)
               + '/seo">← SEO-завод</a></p>'
               + '<p>Опубликованных адресов: '
               + str(metrics['published_url_count']) + '.</p>'
               + ''.join(cards)
               + '<section class="panel"><h3>AI-ответы</h3>'
                 '<p>Точное количество цитирований в Bing Copilot и ChatGPT '
                 'неизвестно: подтверждённый источник данных пока не подключён.</p>'
                 '</section>')
    return studio_page_shell(product, active='seo', title='Поиск и AI-ответы',
                             subtitle='Проверенные показатели по публичным статьям компании',
                             body=content)


@router.get('/studio/products/{product_id}/seo/insights')
def seo_insights_endpoint(product_id: UUID, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    return JSONResponse(company_seo_insights(db, product_id),
                        headers={'Cache-Control': 'private, no-store'})


def seo_insights_html(db: Session, product_id: UUID) -> str:
    report = company_seo_insights(db, product_id)
    return (
        '<details class="seo-search" aria-label="Поиск и AI-ответы">'
        '<summary>Поиск и AI-ответы <span>Статистика</span></summary>'
        '<div class="seo-insights-body">'
        f'<p>Опубликовано статей: {report["published_articles"]}. '
        f'Скрыто: {report["hidden_articles"]}.</p>'
        '<p>Google Search Console, Яндекс Вебмастер и Bing AI Performance '
        'не предоставляют подтверждённых данных. '
        'Показы, клики, позиции и цитирования пока неизвестны.</p>'
        '<p>Публикация статьи сама по себе не подтверждает индексацию.</p>'
        f'<a class="btn secondary" href="/studio/products/{product_id}/seo/search-metrics">'
        'Открыть поисковые показатели</a></div></details>'
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
    enabled = is_enabled(db, product_id)
    platform = get_settings()
    prefs = editorial_preferences(db, product_id,
        default_interval_days=platform.seo_autorun_interval_days,
        default_publications_per_day=platform.seo_publications_per_day,
        default_articles_per_day=platform.seo_articles_per_day)
    runs = seo_runs(db, product_id)
    report = company_seo_insights(db, product_id)
    rows = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id,
        Artifact.kind.in_(['semantic_core', 'seo_content_plan', 'seo_factory_report'])
    ).order_by(Artifact.created_at.desc()).limit(100)))
    base = f'/studio/products/{product_id}'
    # Only the central ServGo product uses the /blog route.
    blog_base = 'https://servgo.ru' if product.slug == 'servgo' else None
    business_id = db.scalar(select(StudioBusinessLink.servgo_business_id).where(
        StudioBusinessLink.product_id == product_id).limit(1))
    if product.slug == 'servgo':
        destination = ('<strong>Место публикации: блог ServGo</strong> · '
                       '<a href="https://servgo.ru/blog">Открыть блог</a>')
    elif business_id is not None:
        destination = (f'Место публикации: персональный блог компании на ServGo · '
                       f'<a href="https://servgo.ru/business/{business_id}/blog">Открыть блог</a>')
    else:
        destination = ('Место публикации: персональный блог компании на ServGo. '
                       'Ссылка появится после привязки бизнеса.')
    autorun_enabled = (
        platform.seo_autorun_enabled or
        (product.slug == "servgo" and platform.seo_servgo_autorun_enabled)
    )
    autorun_note = (
        "Автоматическое обновление SEO-плана на сервере включено."
        if autorun_enabled else
        "Автоматическое обновление SEO-плана на сервере отключено. "
        "Ручной запуск доступен независимо от этого режима."
    )
    activation_form = (
        '<section class="panel seo-activation" aria-label="Подключение SEO-завода">'
        '<div class="seo-status-head"><h2>SEO-завод компании</h2>'
        + ('<span class="seo-status-badge is-on">Подключён</span>' if enabled
           else '<span class="seo-status-badge">Выключен</span>')
        + '</div>'
        + ('<p>SEO-завод подключён. Запустите подготовку плана.</p>' if enabled
           else '<p>Для этой компании генерация статей отключена.</p>')
        + '<p class="seo-autorun-note">' + html.escape(autorun_note) + '</p>'
        + (f'<form method="post" action="{base}/seo/activation">'
           f'<input type="hidden" name="action" value="{"disable" if enabled else "enable"}">'
           f'<button class="btn {"danger" if enabled else "primary"}" type="submit">'
           + ('Отключить SEO-завод' if enabled else 'Подключить SEO-завод')
           + '</button></form>')
        + '<details class="seo-help"><summary>Как работает подключение</summary>'
          '<p>Подключение само по себе не запускает платную генерацию. '
          'План создаётся на 7 или 30 дней, частота работы и число публикаций задаются для этой компании. '
          'Перед публикацией текст проходит проверку ИИ-критиком. '
          'При отключении отменяются ожидающие задания, '
          'уже опубликованные статьи остаются.</p></details>'
        + '</section>'
    )
    # The settings panel stays collapsed on mobile; saving preferences alone
    # does not activate paid generation. The global operator flag remains OFF
    # until canary acceptance.
    preferences_form = (
        f'<details class="seo-help"><summary>Расписание и частота публикаций</summary>'
        f'<form method="post" action="{base}/seo/preferences" class="seo-preferences">'
        '<label class="field">Часовой пояс IANA '
        f'<input name="timezone_name" maxlength="64" required value="{html.escape(prefs.timezone_name, quote=True)}"'
        ' placeholder="Europe/Moscow"></label>'
        '<label class="field">Через сколько дней обновлять SEO-план '
        f'<input type="number" name="autorun_interval_days" min="1" max="90" required value="{prefs.autorun_interval_days}"></label>'
        '<label class="field">Горизонт автоматического плана '
        f'<select name="autorun_horizon_days">'
        f'<option value="7" {"selected" if prefs.autorun_horizon_days == 7 else ""}>7 дней</option>'
        f'<option value="30" {"selected" if prefs.autorun_horizon_days == 30 else ""}>30 дней</option>'
        '</select></label>'
        '<label class="field">Публикаций в день '
        f'<input type="number" name="publications_per_day" min="1" max="30" required value="{prefs.publications_per_day}"></label>'
        '<label class="field">Генераций статей в день '
        f'<input type="number" name="articles_per_day" min="1" max="30" required value="{prefs.articles_per_day}"></label>'
        '<p>Сохранение расписания не включает SEO-завод. '
        'Глобальный автозапуск включается только после сквозной проверки.</p>'
        '<button class="btn primary" type="submit">Сохранить расписание</button></form></details>'
    )
    available_actions = (
        f'<nav class="seo-action-row" aria-label="Управление SEO-заводом">'
        f'<a class="btn primary" href="{base}/seo/queue">Новый запуск</a>'
        f'<a class="btn secondary" href="{base}?design=v4&amp;command=seo_factory">'
        'Через диспетчера</a>'
        f'<a class="btn secondary" href="{base}/work">Задания</a></nav>'
        if enabled else ''
    )
    visible_run, older_visible = visible_plan_run(runs)
    historical_note = (
        '<p class="seo-schedule-empty" role="status">'
        'Новый SEO-запуск ещё не создал план или завершился ошибкой. '
        'Ниже показан последний сформированный SEO-план; '
        'состояние нового запуска находится в истории.</p>'
        if older_visible else ''
    )
    run_cards = ''.join(seo_run_card(row, product_id, blog_base=blog_base, enabled=enabled)
                        for row in runs)
    saved_cards = ''.join(
        f'<article class="seo-saved-item"><h3>{html.escape(row.title)}</h3>'
        f'<p>{html.escape(row.kind)} · {html.escape(row.status.value)}</p>'
        f'{artifact_content(row.content)}</article>' for row in rows
    )
    content = (
        activation_form
        + historical_note
        + seo_plan_board(visible_run, product_id,
                         autorun=autorun_enabled, enabled=enabled)
        + preferences_form + available_actions
        + '<p class="seo-publish-destination">' + destination + '</p>'
        + '<div class="seo-dash-stats" aria-label="SEO-статистика">'
          f'<div><span>Запусков</span><strong>{len(runs)}</strong></div>'
          f'<div><span>Опубликовано</span><strong>{report["published_articles"]}</strong></div>'
          f'<div><span>Скрыто</span><strong>{report["hidden_articles"]}</strong></div></div>'
        + '<section class="seo-section"><h2>Запуски</h2>'
        + (run_cards or '<p>Запусков пока нет.</p>') + '</section>'
        + '<section class="seo-section"><h2>Статьи в блоге</h2>'
        + seo_published_articles(db, product_id, enabled) + '</section>'
        + seo_insights_html(db, product_id)
        + '<details class="seo-saved"><summary>Материалы и отчёты'
          f'<span>{len(rows)}</span></summary>'
          '<div class="seo-saved-body">'
        + (saved_cards or '<p>Материалы пока не подготовлены.</p>')
        + '</div></details>'
    )
    return studio_page_shell(
        product, active='seo', title='SEO-завод',
        subtitle='Статьи, публикации и результаты вашей компании.',
        body=content,
    )


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
    # Keep stored task titles as immutable worker/deduplication keys. Build a
    # company-scoped map of the original requests for owner-facing article topics.
    from app.api.content_studio_activity import task_title, task_public_description
    requests_by_task = {}
    if tasks:
        requests = db.scalars(select(Artifact).where(
            Artifact.product_id == product_id,
            Artifact.task_id.in_([task.id for task in tasks]),
            Artifact.kind.in_(("content_request", "creative_request", "edit_request")),
        ).order_by(Artifact.created_at.desc(), Artifact.id.desc()))
        for request in requests:
            requests_by_task.setdefault(request.task_id, request)
    materials = list(db.scalars(select(Artifact).where(Artifact.product_id == product_id, Artifact.kind.in_(MATERIAL_KINDS))
                               .order_by(Artifact.created_at.desc()).limit(200)))
    from fastapi.responses import JSONResponse
    return JSONResponse({'tasks': [dict(
            id=str(t.id),title=task_title(t, requests_by_task.get(t.id)),status=t.status.value,
            group=t.assigned_agent or 'ИИ-команда',detail=task_public_description(t)) for t in tasks],
        'materials': [dict(id=str(a.id),title=a.title,kind=a.kind,status=a.status.value,
                          description='') for a in materials]},
        headers={'Cache-Control':'private, no-store'})
