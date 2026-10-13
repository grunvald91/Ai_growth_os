"""Authenticated owner UI/API for a single approved narrated drawing job."""
from __future__ import annotations

import json
import re
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import ValidationError

from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.api.content_studio import _owner_product, _studio_form, esc, studio_page_shell
from app.content.whiteboard.contracts import Contract, WhiteboardRequest
from app.content.whiteboard.jobs import AGENT, approve_job, cancel_job, digest, job_directory, owner_task, queue_job, records
from app.content.whiteboard.runtime import MESSAGES, ReadinessError, generation_profile, preflight, scoped_settings
from app.content.whiteboard.checkpoints import CheckpointError
from app.core.config import get_settings
from app.db.session import get_db
from app.models.domain import Task, TaskStatus
from pydantic import Field

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])

STATUS_NAMES = {'waiting_approval': 'Ожидает подтверждения', 'queued': 'В очереди', 'running': 'Готовится',
                'completed': 'Видео готово', 'failed': 'Остановлено после ошибки', 'cancelled': 'Отменено'}


class CreateJob(Contract):
    topic: str = Field(min_length=8, max_length=400)
    max_api_calls: int = Field(default=13, ge=13, le=25)
    profile: Literal['configured', 'flux_ru'] = 'configured'
    visual_review: bool = False


class ApproveJob(Contract):
    request_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmed: bool


def job_state(db, product_id, task_id):
    task = owner_task(db, product_id, task_id)
    source, state, approval, payload = records(db, task)
    return {'task_id': str(task.id), 'status': task.status.value, 'request': payload.request.model_dump(mode='json'),
            'models': payload.models.model_dump(), 'max_api_calls': payload.max_api_calls,
            'visual_review_enabled': payload.visual_review,
            'visual_repair_from': str(payload.visual_repair_from) if payload.visual_repair_from else None,
            'inherited_calls': payload.inherited_calls,
            'request_digest': digest(source.content), 'approval_status': approval.status,
            **json.loads(state.content)}


def _load(db, product_id, task_id):
    _owner_product(db, product_id)
    try:
        return job_state(db, product_id, task_id)
    except LookupError:
        raise HTTPException(404, 'Задание не найдено') from None
    except (ReadinessError, ValueError):
        raise HTTPException(409, MESSAGES['approval_invalid']) from None


def _queue(db, product, body, settings):
    try:
        models, voice = generation_profile(settings, body.profile)
    except ValueError:
        raise HTTPException(503, MESSAGES['configuration_missing']) from None
    request = WhiteboardRequest(product_id=product.id, topic=body.topic, voice=voice)
    try:
        return queue_job(db, request, models, max_api_calls=body.max_api_calls, visual_review=body.visual_review)
    except ValueError:
        raise HTTPException(422, 'Проверьте тему и лимит: с проверкой рисунков выберите 19 или 25 вызовов. Ключи и персональные данные нельзя включать в видео.') from None


async def _approve(db, product_id, task_id, body, actor, settings):
    state = _load(db, product_id, task_id)
    if state['status'] in {'queued', 'running', 'completed'} and state['approval_status'] == 'approved':
        # The persisted command remains idempotent, but still validates its digest.
        return approve_job(db, product_id, task_id, request_digest=body.request_digest,
                           confirmed=body.confirmed, decided_by=actor or 'local-owner')
    if not body.confirmed or body.request_digest != state['request_digest'] or state['status'] != 'waiting_approval':
        raise HTTPException(409, MESSAGES['approval_invalid'])
    task = owner_task(db, product_id, task_id)
    _, saved, _, payload = records(db, task)
    try:
        await preflight(scoped_settings(settings, payload.models, payload.request.voice), payload.models, payload.request.voice)
    except ReadinessError as exc:
        # Short transaction after network I/O. Do not overwrite cancellation.
        task = owner_task(db, product_id, task_id, lock=True)
        _, saved, _, _ = records(db, task, lock=True)
        if task.status == TaskStatus.WAITING_APPROVAL:
            value = json.loads(saved.content); value.update(error=exc.code, stage='waiting_approval')
            saved.content = json.dumps(value); db.commit()
        raise HTTPException(503, MESSAGES.get(exc.code, MESSAGES['openrouter_unavailable'])) from None
    try:
        return approve_job(db, product_id, task_id, request_digest=body.request_digest,
                           confirmed=body.confirmed, decided_by=actor or 'local-owner')
    except ReadinessError:
        raise HTTPException(409, MESSAGES['approval_invalid']) from None


@router.post('/products/{product_id}/whiteboard/jobs', status_code=201)
def api_create(product_id: UUID, body: CreateJob, db: Session = Depends(get_db), settings=Depends(get_settings)):
    product = _owner_product(db, product_id)
    task = _queue(db, product, body, settings)
    return job_state(db, product_id, task.id)


@router.get('/products/{product_id}/whiteboard/jobs')
def api_list(product_id: UUID, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    tasks = db.scalars(select(Task).where(Task.product_id == product_id, Task.assigned_agent == AGENT)
                      .order_by(Task.created_at.desc(), Task.id.desc()).limit(20))
    return [_load(db, product_id, task.id) for task in tasks]


@router.get('/products/{product_id}/whiteboard/jobs/{task_id}')
def api_job(product_id: UUID, task_id: UUID, db: Session = Depends(get_db)):
    return _load(db, product_id, task_id)


@router.post('/products/{product_id}/whiteboard/jobs/{task_id}/approve')
async def api_approve(product_id: UUID, task_id: UUID, body: ApproveJob, db: Session = Depends(get_db),
                      actor=Depends(require_studio_auth), settings=Depends(get_settings)):
    await _approve(db, product_id, task_id, body, actor, settings)
    return _load(db, product_id, task_id)


@router.post('/products/{product_id}/whiteboard/jobs/{task_id}/cancel')
def api_cancel(product_id: UUID, task_id: UUID, db: Session = Depends(get_db)):
    _load(db, product_id, task_id)
    cancel_job(db, product_id, task_id)
    return _load(db, product_id, task_id)


def page(product, title, body, *, status=200, refresh=False):
    headers = {'Cache-Control': 'no-store'}
    if refresh:
        headers['Refresh'] = '8'
    return HTMLResponse(studio_page_shell(product, active='create', title=title,
        subtitle='Рисованное объяснение с русской озвучкой · 30 секунд · 9:16', body=body), status_code=status, headers=headers)


def job_html(state, product_id, *, error=None):
    base = f'/studio/products/{product_id}/whiteboard/{state["task_id"]}'
    stages = {'storyboard': 'Сценарий', 'image': 'Рисунок', 'regions': 'Области рисования', 'speech': 'Озвучка',
              'visual_review': 'Критик проверяет рисунки', 'visual_decision': 'Автор разбирает замечания',
              'image_revision': 'Перерисовка выбранной сцены',
              'audio_reuse': 'Использование исходной озвучки без генерации',
              'preflight': 'Проверка доступа к моделям', 'queued': 'Очередь',
              'rendering': 'Сборка MP4 из рисунков и озвучки', 'completed': 'Видео собрано'}
    code = state.get('error')
    warning = error or MESSAGES.get(code)
    body = (f'<section class="sg-surface"><h2>{esc(state["request"]["topic"])}</h2>'
            f'<p role="status">{esc(STATUS_NAMES.get(state["status"], state["status"]))}</p>'
            f'<p>{esc(stages.get(state.get("stage"), "Подготовка задания"))} · '
            f'API-вызовы: {state["calls"]} из {state["max_api_calls"]}</p>')
    scene = state.get('scene_index')
    beat = state.get('beat_index')
    if isinstance(scene, int) and 0 <= scene < 3:
        body += f'<p>Сцена {scene + 1} из 3'
        if isinstance(beat, int) and 0 <= beat < 6:
            body += f' · реплика {beat + 1}'
        body += '</p>'
    total, done = state.get('progress_total'), state.get('progress_done', 0)
    if isinstance(total, int) and isinstance(done, int) and 0 <= done <= total <= 100 and total:
        body += (f'<p>Завершено этапов: {done} из {total}</p>'
                 f'<progress aria-label="Завершённые этапы создания видео" value="{done}" max="{total}" style="width:100%"></progress>')
    if state.get('failed_stage') in stages:
        body += f'<p>Ошибка на этапе: {esc(stages[state["failed_stage"]])}</p>'
    if warning:
        body += f'<p class="sg-warning" role="alert">{esc(warning)}</p>'
    if state.get('visual_review_enabled'):
        body += '<p>Один проход визуального критика. Автор выбирает существенные исправления; каждая выбранная сцена перерисовывается максимум один раз.</p>'
    if state.get('visual_repair_from'):
        body += ('<p><strong>Переделка рисунков с исходной озвучкой.</strong> Сценарий, голос и аудиофайлы сохраняются. '
                 'Новая озвучка запрещена. Раскрытие рисунков привязано к длительности исходных реплик.</p>'
                 f'<p>Из исходного задания перенесено вызовов: {state["inherited_calls"]}. '
                 f'На визуальную правку осталось не более {state["max_api_calls"] - state["inherited_calls"]} вызовов в общем лимите.</p>')
    review = state.get('visual_review_result')
    if isinstance(review, dict):
        body += '<details><summary>Замечания критика и решение автора</summary>'
        decisions = {d['issue_id']: d for d in review.get('author_decision', {}).get('decisions', [])}
        issues = review.get('review', {}).get('issues', [])
        if not issues:
            body += '<p>Критик не сообщил замечаний. Это не гарантия фактической точности рисунков.</p>'
        for issue in issues:
            choice = decisions.get(issue['id'], {})
            label = 'Выбрано для исправления' if choice.get('accept') else 'Не принято к исправлению'
            body += (f'<p><strong>Сцена {issue["scene_index"] + 1}: {label}</strong><br>'
                     f'{esc(issue["evidence"])}<br>Предложение: {esc(issue["correction"])}<br>'
                     f'Решение автора: {esc(choice.get("reason", ""))}</p>')
        body += '<p>Второго прохода критика после перерисовки нет. Перед публикацией проверьте итоговые рисунки.</p></details>'
    if state['status'] == 'waiting_approval':
        model_names = ' · '.join(esc(value) for value in state['models'].values())
        body += (f'<details><summary>Модели и голос</summary><p>{model_names}</p>'
                 f'<p>Голос: {esc(state["request"]["voice"])}</p></details>'
                 '<p>Генерация платная. Точная стоимость в долларах пока неизвестна. '
                 'Лимит ниже ограничивает число вызовов, а не сумму списаний. Автоматического повтора нет.</p>'
                 f'<form method="post" action="{base}/approve" class="sg-form">'
                 f'<input type="hidden" name="request_digest" value="{state["request_digest"]}">'
                 f'<label class="sg-field"><span><input type="checkbox" name="confirmed" value="yes" required> '
                 f'Разрешаю платную генерацию этого видео: до {state["max_api_calls"]} API-вызовов</span></label>'
                 '<button class="btn primary" type="submit">Подтвердить и сгенерировать</button></form>')
    if state['status'] in {'queued', 'running', 'waiting_approval'}:
        body += (f'<form method="post" action="{base}/cancel"><button class="btn secondary">Остановить задание</button></form>')
    if state['status'] in {'queued', 'running'}:
        body += '<p>Можно закрыть страницу. Генерация продолжится; статус обновляется автоматически. Задание останется в списке ваших анимаций.</p>'
    if state['status'] == 'completed':
        body += (f'<video controls playsinline preload="metadata" poster="{base}/files/video.png" '
                 f'style="width:min(100%,360px);max-height:70vh" src="{base}/files/video.mp4">'
                 f'<track kind="subtitles" srclang="ru" label="Русский" src="{base}/files/video.vtt">'
                 '</video>'
                 '<p>Проверьте рисунки, факты, русский текст, озвучку и субтитры перед публикацией. Публикация выполняется отдельно.</p>'
                 '<div class="sg-form-bottom">' + ''.join(
                     f'<a class="btn secondary" href="{base}/files/{name}?download=true" download>{label}</a>'
                     for name, label in [('video.mp4', 'Скачать MP4'), ('video.srt', 'Субтитры'),
                                         ('video.png', 'Обложка'), ('video.json', 'Тайминг')]) + '</div>')
        if not state.get('visual_repair_from'):
            body += (f'<form method="post" action="{base}/repair" class="sg-form">'
                     '<button class="btn secondary">Переделать рисунки, сохранить озвучку</button>'
                     '<small>Сначала создаётся запрос на проверку и исправление рисунков. Платные вызовы начнутся только после подтверждения; сценарий и звук не генерируются заново.</small></form>')
    body += f'<p><a href="/studio/products/{product_id}/whiteboard">К рисованным видео</a></p></section>'
    return body


def creation_form(product_id, *, fields=None, error=None):
    fields = fields or {}
    base = f'/studio/products/{product_id}/whiteboard'
    profile = fields.get('profile', 'flux_ru')
    maximum = str(fields.get('max_api_calls', '19'))
    visual_review = fields.get('visual_review', 'once')
    body = '<section class="sg-surface"><h2>Создать анимацию с озвучкой</h2><p>Вертикальное видео на 30 секунд: три сцены, русская озвучка, MP4, обложка и субтитры. Перед запуском вы подтвердите платную генерацию. Доступ к моделям проверяется перед платным запуском.</p>'
    if error:
        body += f'<p class="sg-warning" role="alert">{esc(error)}</p>'
    body += (f'<form method="post" action="{base}" class="sg-form">'
             '<label class="sg-field"><span>Что объяснить в видео?</span><textarea name="topic" minlength="8" maxlength="400" rows="3" required '
             f'placeholder="Например: почему пилот добавляет тягу при посадке на авианосец">{esc(fields.get("topic", ""))}</textarea></label>'
             '<details><summary>Настройки генерации</summary>'
             '<label class="sg-field"><span>Вариант генерации</span><select name="profile">' + ''.join(
                 f'<option value="{value}"' + (' selected' if profile == value else '') + f'>{label}</option>'
                 for value, label in [('flux_ru', 'FLUX — рисунки, Grok — русская озвучка'), ('configured', 'Модели из настроек сервера')]) +
             '</select><small>Выбор сохраняется только для этого задания. Модели и голос можно проверить перед подтверждением.</small></label>'
             '<label class="sg-field"><span>Проверка рисунков</span><select name="visual_review">' + ''.join(
                 f'<option value="{value}"' + (' selected' if visual_review == value else '') + f'>{label}</option>'
                 for value, label in [('once', 'Критик один раз, автор выбирает исправления'), ('off', 'Без критика')]) +
             '</select><small>Проверяются механика, расположение деталей, соответствие озвучке и читаемость. Для трёх сцен по две реплики: до 18 вызовов с исправлениями; выберите лимит 19 или 25.</small></label>'
             '<label class="sg-field"><span>Максимум платных API-вызовов</span><select name="max_api_calls">' + ''.join(
                 f'<option value="{value}"' + (' selected' if maximum == str(value) else '') + f'>{label}</option>'
                 for value, label in [(13, '13 — без проверки рисунков'), (19, '19 — с проверкой рисунков'), (25, '25')]) +
             '</select><small>Это лимит обращений к моделям; точная сумма списаний неизвестна.</small></label></details>'
             '<button class="btn primary">Сохранить и перейти к подтверждению</button></form></section>')
    return body


def history_html(states, product_id):
    base = f'/studio/products/{product_id}/whiteboard'
    body = '<section class="sg-surface"><h2>Ваши анимации</h2><div id="whiteboard-jobs" aria-live="polite">'
    for state in states:
        url = f'{base}/{state["task_id"]}'
        body += (f'<article><p><a href="{url}">{esc(state["request"]["topic"])}</a></p>'
                 f'<p>{esc(STATUS_NAMES.get(state["status"], state["status"]))}</p>')
        if state['status'] == 'completed':
            body += (f'<p><a class="btn primary" href="{url}">Посмотреть видео</a> '
                     f'<a class="btn secondary" href="{url}/files/video.mp4?download=true" download>Скачать MP4</a></p>')
        body += '</article>'
    if not states:
        body += '<p>Здесь появятся ваши задания. К каждому можно вернуться после закрытия страницы.</p>'
    body += '</div></section>'
    # Poll only read-only job status; leave the user's draft form untouched.
    body += '''<script>
    (() => {
      const root = document.getElementById('whiteboard-jobs');
      const base = ''' + json.dumps(base) + ''';
      const endpoint = ''' + json.dumps(f'/products/{product_id}/whiteboard/jobs') + ''';
      const names = ''' + json.dumps(STATUS_NAMES, ensure_ascii=False) + ''';
      async function refresh() {
        if (document.hidden) return;
        try {
          const response = await fetch(endpoint, {credentials:'same-origin', cache:'no-store'});
          if (!response.ok) return;
          const states = await response.json();
          if (!Array.isArray(states) || !states.length) return;
          const fragment = document.createDocumentFragment();
          for (const state of states) {
            if (!/^[0-9a-f-]{36}$/.test(state.task_id)) continue;
            const article = document.createElement('article');
            const title = document.createElement('p');
            const link = document.createElement('a');
            link.href = base + '/' + state.task_id;
            link.textContent = state.request.topic;
            title.append(link); article.append(title);
            const status = document.createElement('p');
            status.textContent = names[state.status] || 'Обновление статуса';
            article.append(status);
            if (state.status === 'completed') {
              const actions = document.createElement('p');
              const view = document.createElement('a');
              view.href = link.href; view.className = 'btn primary'; view.textContent = 'Посмотреть видео';
              const download = document.createElement('a');
              download.href = link.href + '/files/video.mp4?download=true';
              download.className = 'btn secondary'; download.textContent = 'Скачать MP4'; download.download = 'video.mp4';
              actions.append(view, document.createTextNode(' '), download); article.append(actions);
            }
            fragment.append(article);
          }
          root.replaceChildren(fragment);
        } catch (_) {}
      }
      setInterval(refresh, 8000);
      document.addEventListener('visibilitychange', refresh);
    })();
    </script>'''
    return body


@router.get('/studio/products/{product_id}/whiteboard', response_class=HTMLResponse)
async def studio_index(product_id: UUID, db: Session = Depends(get_db), settings=Depends(get_settings)):
    product = _owner_product(db, product_id)
    error = None
    try:
        generation_profile(settings, 'flux_ru')
        if not settings.openrouter_api_key or not settings.xai_api_key:
            error = MESSAGES['configuration_missing']
    except ValueError:
        error = MESSAGES['configuration_missing']
    body = creation_form(product_id, error=error)
    tasks = list(db.scalars(select(Task).where(Task.product_id == product_id, Task.assigned_agent == AGENT)
                           .order_by(Task.created_at.desc(), Task.id.desc()).limit(20)))
    body += history_html([_load(db, product_id, task.id) for task in tasks], product_id)
    return page(product, 'Рисованное видео', body)


@router.post('/studio/products/{product_id}/whiteboard')
async def studio_create(product_id: UUID, request: Request, db: Session = Depends(get_db), settings=Depends(get_settings)):
    product = _owner_product(db, product_id)
    fields = await _studio_form(request)
    try:
        body = CreateJob(topic=fields.get('topic', ''), max_api_calls=fields.get('max_api_calls', '13'),
                         profile=fields.get('profile', 'configured'), visual_review=fields.get('visual_review') == 'once')
    except ValueError:
        return page(product, 'Создать анимацию', creation_form(product_id, fields=fields,
                    error='Тема: 8–400 символов. Лимит: 13–25 вызовов. Выберите вариант генерации.'), status=422)
    try:
        task = _queue(db, product, body, settings)
    except HTTPException as exc:
        return page(product, 'Создать анимацию', creation_form(product_id, fields=fields, error=exc.detail), status=exc.status_code)
    return RedirectResponse(f'/studio/products/{product_id}/whiteboard/{task.id}', status_code=303)


@router.get('/studio/products/{product_id}/whiteboard/{task_id}', response_class=HTMLResponse)
def studio_job(product_id: UUID, task_id: UUID, db: Session = Depends(get_db)):
    state = _load(db, product_id, task_id)
    return page(_owner_product(db, product_id), 'Генерация видео', job_html(state, product_id),
                refresh=state['status'] in {'queued', 'running'})


@router.post('/studio/products/{product_id}/whiteboard/{task_id}/approve')
async def studio_approve(product_id: UUID, task_id: UUID, request: Request, db: Session = Depends(get_db),
                         actor=Depends(require_studio_auth), settings=Depends(get_settings)):
    fields = await _studio_form(request)
    try:
        body = ApproveJob(request_digest=fields.get('request_digest', ''), confirmed=fields.get('confirmed') == 'yes')
        await _approve(db, product_id, task_id, body, actor, settings)
    except ValidationError:
        raise HTTPException(422, MESSAGES['approval_invalid']) from None
    except HTTPException as exc:
        if exc.status_code == 503:
            state = _load(db, product_id, task_id)
            return page(_owner_product(db, product_id), 'Генерация видео',
                        job_html(state, product_id, error=exc.detail), status=503)
        raise
    return RedirectResponse(f'/studio/products/{product_id}/whiteboard/{task_id}', status_code=303)


@router.post('/studio/products/{product_id}/whiteboard/{task_id}/cancel')
def studio_cancel(product_id: UUID, task_id: UUID, db: Session = Depends(get_db)):
    _load(db, product_id, task_id)
    cancel_job(db, product_id, task_id)
    return RedirectResponse(f'/studio/products/{product_id}/whiteboard/{task_id}', status_code=303)


@router.post('/studio/products/{product_id}/whiteboard/{task_id}/repair')
def studio_repair(product_id: UUID, task_id: UUID, db: Session = Depends(get_db), settings=Depends(get_settings)):
    state = _load(db, product_id, task_id)
    from app.content.whiteboard.repair import queue_visual_repair
    try:
        task = queue_visual_repair(db, settings, product_id, task_id)
    except (ReadinessError, ValueError, CheckpointError) as exc:
        code = getattr(exc, 'code', 'repair_source_invalid')
        return page(_owner_product(db, product_id), 'Визуальная правка',
            job_html(state, product_id, error=MESSAGES[code]), status=409)
    return RedirectResponse(f'/studio/products/{product_id}/whiteboard/{task.id}', status_code=303)


@router.get('/studio/products/{product_id}/whiteboard/{task_id}/files/{filename}')
def studio_file(product_id: UUID, task_id: UUID, filename: str, download: bool = False, db: Session = Depends(get_db), settings=Depends(get_settings)):
    state = _load(db, product_id, task_id)
    types = {'video.mp4': 'video/mp4', 'video.srt': 'application/x-subrip', 'video.png': 'image/png', 'video.json': 'application/json', 'video.vtt': 'text/vtt'}
    if state['status'] != 'completed' or filename not in types:
        raise HTTPException(404, 'Файл не найден')
    target = job_directory(settings.products_root, product_id, task_id) / 'output' / ('video.srt' if filename == 'video.vtt' else filename)
    if target.is_symlink() or target.parent.is_symlink() or not target.is_file():
        raise HTTPException(404, 'Файл не найден')
    if filename == 'video.vtt':
        if target.stat().st_size > 128_000:
            raise HTTPException(404, 'Файл не найден')
        text = re.sub(r'(?m)^(\d{2}:\d{2}:\d{2}),(\d{3})( --> \d{2}:\d{2}:\d{2}),(\d{3})$',
                      r'\1.\2\3.\4', target.read_text(encoding='utf-8'))
        headers = {'Cache-Control': 'private, no-store'}
        if download:
            headers['Content-Disposition'] = 'attachment; filename="video.vtt"'
        return Response('WEBVTT\n\n' + text, media_type='text/vtt', headers=headers)
    return FileResponse(target, media_type=types[filename], filename=filename if download else None,
                        headers={'Cache-Control': 'private, no-store'})
