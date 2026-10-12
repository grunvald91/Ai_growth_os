"""Authenticated owner UI/API for a single approved narrated drawing job."""
from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import ValidationError

from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.api.content_studio import _owner_product, _studio_form, esc, studio_page_shell
from app.content.whiteboard.contracts import Contract, WhiteboardRequest
from app.content.whiteboard.jobs import AGENT, approve_job, cancel_job, digest, job_directory, owner_task, queue_job, records
from app.content.whiteboard.runtime import MESSAGES, ReadinessError, configured_models, preflight
from app.core.config import get_settings
from app.db.session import get_db
from app.models.domain import Task, TaskStatus
from pydantic import Field

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])


class CreateJob(Contract):
    topic: str = Field(min_length=8, max_length=400)
    max_api_calls: int = Field(default=13, ge=13, le=25)


class ApproveJob(Contract):
    request_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmed: bool


def job_state(db, product_id, task_id):
    task = owner_task(db, product_id, task_id)
    source, state, approval, payload = records(db, task)
    return {'task_id': str(task.id), 'status': task.status.value, 'request': payload.request.model_dump(mode='json'),
            'models': payload.models.model_dump(), 'max_api_calls': payload.max_api_calls,
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
        models = configured_models(settings)
    except ValueError:
        raise HTTPException(503, MESSAGES['configuration_missing']) from None
    request = WhiteboardRequest(product_id=product.id, topic=body.topic, voice=settings.whiteboard_voice)
    try:
        return queue_job(db, request, models, max_api_calls=body.max_api_calls)
    except ValueError:
        raise HTTPException(422, 'Проверьте тему и лимит: ключи и персональные данные нельзя включать в видео.') from None


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
        await preflight(settings, payload.models, payload.request.voice)
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
    names = {'waiting_approval': 'Ожидает подтверждения', 'queued': 'В очереди', 'running': 'Готовится',
             'completed': 'Готово к проверке', 'failed': 'Остановлено после ошибки', 'cancelled': 'Отменено'}
    stages = {'storyboard': 'Сценарий', 'image': 'Рисунок', 'regions': 'Области рисования', 'speech': 'Озвучка',
              'preflight': 'Проверка API', 'queued': 'Очередь', 'completed': 'Рендер завершён'}
    code = state.get('error')
    warning = error or MESSAGES.get(code)
    body = (f'<section class="sg-surface"><h2>{esc(state["request"]["topic"])}</h2>'
            f'<p role="status">{esc(names.get(state["status"], state["status"]))}</p>'
            f'<p>{esc(stages.get(state.get("stage"), "Подготовка задания"))} · '
            f'API-вызовы: {state["calls"]} из {state["max_api_calls"]}</p>')
    if warning:
        body += f'<p class="sg-warning" role="alert">{esc(warning)}</p>'
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
        body += '<p>Можно закрыть страницу. Worker продолжит работу; статус обновляется автоматически.</p>'
    if state['status'] == 'completed':
        body += (f'<video controls playsinline preload="metadata" poster="{base}/files/video.png" '
                 f'style="width:min(100%,360px);max-height:70vh" src="{base}/files/video.mp4"></video>'
                 '<p>Проверьте рисунки, факты, русский текст, озвучку и субтитры перед публикацией. Публикация выполняется отдельно.</p>'
                 '<div class="sg-form-bottom">' + ''.join(
                     f'<a class="btn secondary" href="{base}/files/{name}" download>{label}</a>'
                     for name, label in [('video.mp4', 'Скачать MP4'), ('video.srt', 'Субтитры'),
                                         ('video.png', 'Обложка'), ('video.json', 'Тайминг')]) + '</div>')
    body += f'<p><a href="/studio/products/{product_id}/whiteboard">К рисованным видео</a></p></section>'
    return body


@router.get('/studio/products/{product_id}/whiteboard', response_class=HTMLResponse)
async def studio_index(product_id: UUID, db: Session = Depends(get_db), settings=Depends(get_settings)):
    product = _owner_product(db, product_id)
    error = None
    try:
        models = configured_models(settings)
        await preflight(settings, models, settings.whiteboard_voice)
    except (ReadinessError, ValueError) as exc:
        error = MESSAGES.get(getattr(exc, 'code', 'configuration_missing'), MESSAGES['configuration_missing'])
    base = f'/studio/products/{product_id}/whiteboard'
    body = '<section class="sg-surface"><h2>Рисованное видео с озвучкой</h2><p>Три сцены, последовательное рисование, MP4, обложка и субтитры. Перед запуском вы подтвердите платные API-вызовы.</p>'
    if error:
        body += f'<p class="sg-warning" role="alert">{esc(error)} Черновик задания можно сохранить сейчас.</p>'
    body += (f'<form method="post" action="{base}" class="sg-form">'
             '<label class="sg-field"><span>Тема объяснения</span><textarea name="topic" minlength="8" maxlength="400" rows="3" required '
             'placeholder="Например: как клиент выбирает время без переписки"></textarea></label>'
             '<label class="sg-field"><span>Максимум платных API-вызовов</span><select name="max_api_calls">'
             '<option value="13">13 — три сцены по два шага</option><option value="19">19</option><option value="25">25</option></select>'
             '<small>Это лимит обращений к моделям; точная сумма списаний неизвестна.</small></label>'
             '<button class="btn primary">Сохранить и перейти к подтверждению</button></form></section>')
    tasks = list(db.scalars(select(Task).where(Task.product_id == product_id, Task.assigned_agent == AGENT)
                           .order_by(Task.created_at.desc(), Task.id.desc()).limit(20)))
    if tasks:
        body += '<section class="sg-surface"><h2>Задания</h2>' + ''.join(
            f'<p><a href="{base}/{task.id}">{esc(task.description)}</a> · {esc(task.status.value)}</p>'
            for task in tasks) + '</section>'
    return page(product, 'Рисованное видео', body)


@router.post('/studio/products/{product_id}/whiteboard')
async def studio_create(product_id: UUID, request: Request, db: Session = Depends(get_db), settings=Depends(get_settings)):
    product = _owner_product(db, product_id)
    fields = await _studio_form(request)
    try:
        body = CreateJob(topic=fields.get('topic', ''), max_api_calls=fields.get('max_api_calls', '13'))
    except ValueError:
        raise HTTPException(422, 'Тема: 8–400 символов. Лимит: 13–25 вызовов.') from None
    task = _queue(db, product, body, settings)
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


@router.get('/studio/products/{product_id}/whiteboard/{task_id}/files/{filename}')
def studio_file(product_id: UUID, task_id: UUID, filename: str, db: Session = Depends(get_db), settings=Depends(get_settings)):
    state = _load(db, product_id, task_id)
    types = {'video.mp4': 'video/mp4', 'video.srt': 'application/x-subrip', 'video.png': 'image/png', 'video.json': 'application/json'}
    if state['status'] != 'completed' or filename not in types:
        raise HTTPException(404, 'Файл не найден')
    target = job_directory(settings.products_root, product_id, task_id) / 'output' / filename
    if target.is_symlink() or target.parent.is_symlink() or not target.is_file():
        raise HTTPException(404, 'Файл не найден')
    return FileResponse(target, media_type=types[filename], headers={'Cache-Control': 'private, no-store'})
