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
from app.content.whiteboard.contracts import SCENES_FOR_SECONDS, Contract, WhiteboardRequest
from app.content.whiteboard.science import requires_science_review
from app.content.whiteboard.topics import suggest_company_video_topics, suggest_company_create_topics
from app.content.whiteboard.selector_ui import topic_picker_html
from app.content.whiteboard.image_recovery import IMAGE_RECOVERY_RESERVE
from app.content.whiteboard.jobs import (AGENT, MAX_API_CALLS, TOP_UP_STEP, approve_job, call_limit, can_resume, can_top_up, cancel_job,
                                         digest, job_directory, owner_task, queue_job, records, required_calls, resume_job)
from app.content.whiteboard.runtime import (MESSAGES, ReadinessError, generation_profile, preflight, scoped_settings,
                                            DEFAULT_IMAGE_MODEL, GPT_IMAGE_MODEL, CLAUDEHUB_IMAGE_MODELS)
from app.content.whiteboard.checkpoints import CheckpointError
from app.content.durations import duration_notice, task_durations
from app.core.config import get_settings
from app.db.session import get_db
from app.models.domain import Task, TaskStatus
from pydantic import Field, model_validator

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])

STATUS_NAMES = {'waiting_approval': 'Ожидает подтверждения', 'queued': 'В очереди', 'running': 'Готовится',
                'completed': 'Видео готово', 'failed': 'Остановлено после ошибки', 'cancelled': 'Отменено'}


class CreateJob(Contract):
    topic: str = Field(min_length=8, max_length=400)
    # About 30 seconds (3 scenes) or about a minute (6 scenes).
    duration_seconds: Literal[30, 60] = 30
    # What the video ends with. Empty: a call to subscribe to the channel and like.
    call_to_action: str = Field(default='', max_length=200)
    # Empty: exactly as many calls as this video needs.
    max_api_calls: int | None = Field(default=None, ge=13, le=MAX_API_CALLS)
    # An optional extra five calls are included in the *upfront* approval, never added later.
    recovery_budget: bool = False
    profile: Literal['configured', 'gemini_ru'] = 'gemini_ru'
    image_model: Literal['gemini-3.1-flash-image', 'gpt-image-2.5-sunburst'] = 'gemini-3.1-flash-image'
    visual_review: bool = False
    source_script: str = Field(default='', max_length=12000)
    background_notes: str = Field(default='', max_length=6000)
    reference_materials: str = Field(default='', max_length=6000)
    science_mode: Literal['general', 'auto', 'scientific'] = 'auto'

    @model_validator(mode='after')
    def call_to_action_not_tiny(self):
        if self.call_to_action and len(self.call_to_action) < 6:
            raise ValueError('The call to action is too short')
        return self


class ApproveJob(Contract):
    request_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmed: bool


def job_state(db, product_id, task_id):
    task = owner_task(db, product_id, task_id)
    source, state, approval, payload = records(db, task)
    return {'task_id': str(task.id), 'status': task.status.value, 'request': payload.request.model_dump(mode='json'),
            'models': payload.models.model_dump(), 'max_api_calls': payload.max_api_calls,
            'visual_review_enabled': payload.visual_review,
            'image_provider': payload.image_provider,
            'image_recovery': payload.image_recovery,
            'image_recovery_reserve': IMAGE_RECOVERY_RESERVE if payload.image_recovery else 0,
            'visual_repair_from': str(payload.visual_repair_from) if payload.visual_repair_from else None,
            'inherited_calls': payload.inherited_calls,
            'repair_regenerate_images': payload.repair_regenerate_images,
            'request_digest': digest(source.content), 'approval_status': approval.status,
            'resumable': can_resume(task.status, json.loads(state.content), payload),
            'top_up': can_top_up(task.status, json.loads(state.content), payload),
            'call_limit_total': call_limit(json.loads(state.content), payload),
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
        models, voice = generation_profile(settings, body.profile, body.image_model)
    except ValueError:
        raise HTTPException(503, MESSAGES['configuration_missing']) from None
    request = WhiteboardRequest(product_id=product.id, topic=body.topic, voice=voice,
                                duration_seconds=body.duration_seconds, scenes=SCENES_FOR_SECONDS[body.duration_seconds],
                                call_to_action=body.call_to_action,
                                source_script=body.source_script, background_notes=body.background_notes,
                                reference_materials=body.reference_materials, science_mode=body.science_mode,
                                science_policy_version=1)
    needed = required_calls(request, visual_review=body.visual_review)
    if body.max_api_calls is not None and body.max_api_calls < needed:
        raise HTTPException(422, f'Для этого видео нужно не меньше {needed} вызовов. Выберите лимит {needed} или больше, либо минимальный автоматический лимит.')
    limit = body.max_api_calls or min(MAX_API_CALLS, needed + (5 if body.recovery_budget else 0))
    try:
        return queue_job(db, request, models, max_api_calls=limit,
                         visual_review=body.visual_review, image_provider='claudehub')
    except ValueError:
        raise HTTPException(422, 'Проверьте тему, призыв и лимит вызовов. Ключи и персональные данные нельзя включать в видео.') from None


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
        await preflight(scoped_settings(settings, payload.models, payload.request.voice),
                        payload.models, payload.request.voice,
                        **({'skip_speech': True} if payload.repair_regenerate_images else {}))
    except ReadinessError as exc:
        if payload.image_recovery and exc.code in {'claudehub_unavailable', 'claudehub_image_unavailable'}:
            # The worker owns the persisted outage clock; the owner must not
            # have to repeat approval while the chosen image model is offline.
            try:
                return approve_job(db, product_id, task_id, request_digest=body.request_digest,
                                   confirmed=body.confirmed, decided_by=actor or 'local-owner')
            except ReadinessError:
                raise HTTPException(409, MESSAGES['approval_invalid']) from None
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
# The Studio page polls this alias: nginx only forwards /studio/* to the app.
@router.get('/studio/products/{product_id}/whiteboard/jobs.json')
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
        subtitle='Готовый вертикальный MP4 с озвучкой · 30 секунд или минута', body=body), status_code=status, headers=headers)


def approval_card(state, product_id, error=None):
    """Readable approval sheet; the paid-consent contract stays unchanged."""
    base = f'/studio/products/{product_id}/whiteboard/{state["task_id"]}'
    scenes = state['request'].get('scenes', 3)
    duration = 'Около минуты' if scenes == 6 else 'Около 30 секунд'
    models = ''.join(f'<li>{esc(value)}</li>' for value in state['models'].values())
    warning = f'<p class="sg-warning" role="alert">{esc(error)}</p>' if error else ''
    cta = state['request'].get('call_to_action')
    cta_html = f'<p class="wb-cta"><strong>Призыв в конце:</strong> {esc(cta)}</p>' if cta else ''
    inputs = state['request']
    source_description = (
        f'<p>Исходный сценарий: {len(inputs.get("source_script", ""))} символов; '
        f'дополнительные материалы: {len(inputs.get("background_notes", ""))} символов. '
        f'Тип проверки: {esc(inputs.get("science_mode", "general"))}.</p>'
    )
    review = ('<p>Один проход визуального критика. Автор выбирает существенные исправления; '
              'каждая выбранная сцена перерисовывается максимум один раз.</p>' if state.get('visual_review_enabled') else '')
    recovery = (' До 105 дополнительных потенциально платных попыток при сбоях изображений; '
                'резервная модель только после 24 часов непрерывной недоступности основной.' if state.get('image_recovery') else '')
    provider = (f'<p>Новые рисунки и перерисовки: {esc(state["models"]["image"])} через API ClaudeHub. '
                f'{recovery}</p>' if state.get('image_provider') == 'claudehub' else '')
    # Repair requests have additional cost and audio-reuse disclosures in the
    # regular renderer; retain that path rather than omit those conditions.
    return (f'<section class="sg-surface wb-job wb-approval"><header class="wb-heading">'
            '<span class="chip amber">Ожидает подтверждения</span><p class="eyebrow">Простое видео</p>'
            f'<h2>{esc(state["request"]["topic"])}</h2></header>{warning}'
            '<div class="wb-facts"><div><span>Длительность</span>'
            f'<strong>{duration}</strong></div><div><span>Сценарий</span><strong>{scenes} сцен</strong></div>'
            f'<div><span>Лимит генерации</span><strong>До {state["max_api_calls"]} API-вызовов</strong></div></div>{cta_html}'
            '<section class="wb-cost"><h3>Платная генерация</h3><p>Точная стоимость в долларах пока неизвестна. '
            'Ограничено число API-вызовов, а не сумма списаний. '
            'После сбоя генерация автоматически продолжится только в подтверждённом лимите.</p></section>'
            '<details class="wb-details"><summary>Как будет создано видео</summary>'
            f'{source_description}{review}{provider}<h3>Модели и голос</h3><ul>{models}</ul>'
            f'<p>Голос: {esc(state["request"]["voice"])}</p></details>'
            f'<footer class="wb-actions"><form method="post" action="{base}/approve" class="sg-form">'
            f'<input type="hidden" name="request_digest" value="{esc(state["request_digest"])}">'
            '<label class="wb-consent"><input type="checkbox" name="confirmed" value="yes" required>'
            f'<span>Разрешаю платную генерацию этого видео: до {state["max_api_calls"]} API-вызовов.{recovery}</span></label>'
            '<button class="btn primary" type="submit">Подтвердить и сгенерировать</button></form>'
            f'<form method="post" action="{base}/cancel"><button class="btn secondary" type="submit">Остановить задание</button></form>'
            f'<a class="wb-back" href="/studio/products/{product_id}/whiteboard">К простым видео →</a></footer></section>')


def job_html(state, product_id, *, error=None):
    if state['status'] == 'waiting_approval' and not state.get('visual_repair_from'):
        return approval_card(state, product_id, error or MESSAGES.get(state.get('error')))
    base = f'/studio/products/{product_id}/whiteboard/{state["task_id"]}'
    stages = {'storyboard': 'Сценарий', 'science_review': 'Проверка научных утверждений', 'image': 'Рисунок', 'regions': 'Области рисования', 'speech': 'Озвучка',
              'visual_review': 'Критик проверяет рисунки', 'visual_decision': 'Автор разбирает замечания',
              'image_revision': 'Перерисовка выбранной сцены',
              'audio_reuse': 'Использование исходной озвучки без генерации',
              'preflight': 'Проверка доступа к моделям', 'queued': 'Очередь',
              'rendering': 'Сборка MP4 из рисунков и озвучки', 'completed': 'Видео собрано',
              'retry_wait': 'Пауза перед автоматическим продолжением'}
    code = state.get('error')
    warning = error or MESSAGES.get(code)
    completed = state['status'] == 'completed'
    repair_label = 'Перерисовка изображений' if state.get('visual_repair_from') else 'Простое видео'
    body = (f'<section class="sg-surface wb-job{" wb-result" if completed else ""}">'
            f'<header class="wb-job-head"><p class="wb-eyebrow">{repair_label}</p>'
            f'<h2>{esc(state["request"]["topic"])}</h2>'
            f'<p role="status" class="wb-status{" wb-status-ready" if completed else ""}">'
            f'{esc(STATUS_NAMES.get(state["status"], state["status"]))}</p></header>')
    # A rejected repair POST must not hide its error inside collapsed technical details.
    if completed and warning:
        body += f'<p class="sg-warning" role="alert">{esc(warning)}</p>'
    if completed:
        # The actual playable file must be the focal point, not the log or API metadata.
        length_label = '1 мин · 6 сцен' if state['request'].get('scenes', 3) == 6 else '30 сек · 3 сцены'
        file_icons = {'video.mp4': '↓', 'video.srt': '≡', 'video.png': '▣', 'video.json': '◷'}
        body += (f'<div class="wb-result-meta" aria-label="Формат ролика">'
                 f'<span>{length_label}</span><span>9:16</span><span>Русский голос · MP4</span></div>')
        body += (f'<div class="wb-result-player"><video controls playsinline preload="metadata" '
                 f'aria-label="Готовое видео: {esc(state["request"]["topic"])}" '
                 f'poster="{base}/files/video.png">'
                 f'<source src="{base}/files/video.mp4" type="video/mp4">'
                 f'<track kind="subtitles" srclang="ru" label="Русский" src="{base}/files/video.vtt">'
                 'Ваш браузер не поддерживает воспроизведение MP4.</video></div>'
                 '<p class="wb-result-notice">Перед публикацией проверьте рисунки, факты, озвучку и субтитры.</p>'
                 '<nav class="wb-downloads" aria-label="Скачать материалы видео">' + ''.join(
                     f'<a class="btn {"primary" if name == "video.mp4" else "secondary"}" '
                     f'href="{base}/files/{name}?download=true" download><span class="wb-file-icon" aria-hidden="true">'
                     f'{file_icons[name]}</span>{label}</a>'
                     for name, label in [('video.mp4', 'Скачать MP4'), ('video.srt', 'Субтитры'),
                                         ('video.png', 'Обложка'), ('video.json', 'Тайминг')]
                 ) + '</nav>'
                 '<details class="wb-result-technical"><summary>Подробности создания</summary>')
    body += (f'<p class="wb-stage-line">{esc(stages.get(state.get("stage"), "Подготовка задания"))} · '
             f'API-вызовы: {state["calls"]} из {state.get("call_limit_total", state["max_api_calls"])}</p>')
    attempts_started = state.get('attempts_started')
    if type(attempts_started) is int and attempts_started > state['calls']:
        body += (f'<p class="wb-attempt-line">Фактически начатых API-попыток: {attempts_started} '
                 f'из {state.get("call_limit_total", state["max_api_calls"])}. '
                 'Ответы HTTP 502 могут не учитываться в старом счётчике; '
                 'фактическое списание определяет провайдер.</p>')
    scene = state.get('scene_index')
    beat = state.get('beat_index')
    scene_total = state['request'].get('scenes', 3)
    custom_call = state['request'].get('call_to_action')
    body += ('<p>' + ('Около минуты, 6 сцен' if scene_total == 6 else 'Около 30 секунд, 3 сцены')
             + (' · призыв в конце: ' + esc(custom_call) if custom_call else '') + '</p>')
    if isinstance(scene, int) and 0 <= scene < scene_total:
        body += f'<p>Сцена {scene + 1} из {scene_total}'
        if isinstance(beat, int) and 0 <= beat < 6:
            body += f' · реплика {beat + 1}'
        body += '</p>'
    total, done = state.get('progress_total'), state.get('progress_done', 0)
    if isinstance(total, int) and isinstance(done, int) and 0 <= done <= total <= 100 and total:
        body += (f'<p>Завершено этапов: {done} из {total}</p>'
                 f'<progress aria-label="Завершённые этапы создания видео" value="{done}" max="{total}" style="width:100%"></progress>')
    if state.get('failed_stage') in stages:
        body += f'<p>Ошибка на этапе: {esc(stages[state["failed_stage"]])}</p>'
    if warning and not completed:
        body += f'<p class="sg-warning" role="alert">{esc(warning)}</p>'
    if state['status'] == 'queued' and state.get('retry_at'):
        reason = MESSAGES.get(state.get('last_error'), 'временный сбой')
        provider_wait = (state.get('last_error') in {'openrouter_unavailable', 'claudehub_unavailable'}
                         and state.get('auto_resumes', 0) > state.get('auto_resume_limit', 0))
        if state.get('image_outage_since'):
            body += (f'<p class="sg-warning" role="status"><strong>Ожидаем восстановления модели.</strong><br>'
                     f'Начало сбоя: {esc(state["image_outage_since"][:16].replace("T", " "))} UTC. '
                     f'Следующая проверка: {esc(state["retry_at"][:16].replace("T", " "))} UTC. '
                     f'Модель: {esc(state.get("active_image_model", state["models"]["image"]))}. '
                     'Готовые этапы сохранены; резервная модель только после 24 часов.</p>')
        elif provider_wait:
            body += (f'<p class="sg-warning" role="status"><strong>Ожидаем восстановления доступа к моделям.</strong><br>'
                     f'Следующая автоматическая проверка не раньше {esc(state["retry_at"][:16].replace("T", " "))} UTC. '
                     'Проверка не выполняет платных вызовов; готовые этапы сохранены.</p>')
        else:
            body += (f'<p class="sg-warning" role="status"><strong>Автоматическое продолжение {state.get("auto_resumes", 1)} '
                     f'из {state.get("auto_resume_limit", state.get("auto_resumes", 1))}.</strong><br>Причина остановки: {esc(reason)} '
                     f'Запуск не раньше {esc(state["retry_at"][:16].replace("T", " "))} UTC.<br>'
                     'Готовые этапы повторно не оплачиваются; вызовы расходуются только из подтверждённого лимита.</p>')
    elif state['status'] == 'failed' and state.get('auto_resumes') and not state.get('top_up'):
        body += (f'<p>Автоматических продолжений было: {state["auto_resumes"]}. '
                 'Сбой повторяется; ошибки сохранены в журнале ниже для разбора.</p>')
    if state.get('top_up'):
        body += (f'<form method="post" action="{base}/resume" class="sg-form">'
                 '<p>Подтверждённые API-вызовы закончились, видео не доделано. Готовые этапы сохранены и повторно не оплачиваются.</p>'
                 '<label class="sg-field"><span>Добавить вызовов</span><select name="extra_calls">'
                 + ''.join(f'<option value="{n}"{" selected" if n == 5 else ""}>{n}</option>' for n in (3, 5, TOP_UP_STEP))
                 + '</select></label>'
                 '<label class="sg-field"><span><input type="checkbox" name="confirmed" value="yes" required> '
                 'Разрешаю дополнительные платные вызовы, чтобы доделать это видео</span></label>'
                 '<button class="btn primary" type="submit">Добавить вызовы и доделать видео</button></form>')
    elif state.get('resumable'):
        left = state.get('call_limit_total', state['max_api_calls']) - state['calls']
        body += (f'<form method="post" action="{base}/resume" class="sg-form">'
                 '<p>Готовые этапы (сценарий, готовые рисунки и озвучка) сохранены и повторно не оплачиваются. '
                 'Заново будет вызван только остановившийся этап и следующие за ним.</p>'
                 f'<label class="sg-field"><span><input type="checkbox" name="confirmed" value="yes" required> '
                 f'Разрешаю продолжить платную генерацию: осталось не более {left} API-вызовов из подтверждённого лимита</span></label>'
                 '<button class="btn primary" type="submit">Продолжить с места остановки</button></form>')
    errors = state.get('errors') or []
    if errors:
        body += f'<details><summary>Журнал ошибок ({len(errors)})</summary><ol>'
        for entry in reversed(errors[-20:]):
            stage = stages.get(entry.get('stage'), entry.get('stage') or '—')
            extra = ', '.join(f'{k}: {entry[k]}' for k in ('exception_type', 'http_status') if entry.get(k))
            body += (f'<li>{esc(str(entry.get("at", ""))[:19].replace("T", " "))} UTC · {esc(stage)} · '
                     f'{esc(entry.get("code", ""))} · вызовов {esc(str(entry.get("calls", "")))}'
                     + (f' · {esc(extra)}' if extra else '') + '</li>')
        body += '</ol></details>'
    science = state.get('science_review_result')
    if isinstance(science, dict):
        summary = esc(science.get('summary', ''))
        label = 'Научная проверка пройдена' if science.get('status') == 'supported' else 'Научные факты требуют исправления'
        body += f'<p><strong>{label}</strong>: {summary}</p>'
        sources = science.get('source_links') or []
        if sources:
            body += '<details><summary>Источники научной проверки</summary><ul>'
            for item in sources[:5]:
                url = str(item.get('url', ''))
                if url.startswith('https://'):
                    body += f'<li><a href="{esc(url)}" rel="noopener noreferrer" target="_blank">{esc(url)}</a></li>'
            body += '</ul></details>'
    if state.get('visual_review_enabled'):
        body += '<p>Один проход визуального критика. Автор выбирает существенные исправления; каждая выбранная сцена перерисовывается максимум один раз.</p>'
    if state.get('image_provider') == 'claudehub':
        policy = ('Резервная модель разрешена только после 24 часов недоступности основной.'
                  if state.get('image_recovery') else 'Автоматической замены модели нет для этого разрешения.')
        body += (f'<p>Основная модель рисунков: {esc(state["models"]["image"])} через API ClaudeHub. {policy}</p>')
        if state.get('active_image_model'):
            body += f'<p>Текущая резервная модель: {esc(state["active_image_model"])}.</p>'
    if state.get('visual_repair_from'):
        source_link = f'/studio/products/{product_id}/whiteboard/{state["visual_repair_from"]}'
        body += ('<p><strong>Перерисовка без новой озвучки.</strong> Новые изображения создаются отдельно. '
                 'Сценарий и оригинальные MP3 сохраняются побайтно. Новая озвучка запрещена.</p>'
                 f'<p><a href="{source_link}">Исходный ролик</a> · '
                 f'В исходном задании было {state["inherited_calls"]} API-вызовов.</p>')
        if state.get('repair_regenerate_images'):
            body += (f'<p><strong>Отдельный лимит перерисовки:</strong> до '
                     f'{state["max_api_calls"]} новых API-вызовов после подтверждения; '
                     'старые вызовы не списываются из нового лимита.</p>')
        else:
            body += (f'<p>Осталось до {max(0, state["max_api_calls"] - state["inherited_calls"])} '
                     'вызовов из старого общего лимита.</p>')
        if state['status'] == 'waiting_approval':
            body += ('<p class="sg-warning" role="status"><strong>Задание на перерисовку создано.</strong> '
                     'Для запуска новых рисунков подтвердите дополнительные платные вызовы ниже.</p>')
    review = state.get('visual_review_result')
    if isinstance(review, dict):
        if review.get('author_decision_fallback'):
            body += ('<p class="sg-warning" role="status">ИИ-автор не смог выдать корректное решение '
                     'по замечаниям критика. Исходные рисунки сохранены без перерисовки; '
                     'проверьте их перед публикацией. Повторные платные вызовы для этого решения не нужны.</p>')
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
    region_audit = (review.get('region_layout') or []) if isinstance(review, dict) else (state.get('region_layout') or [])
    if any(isinstance(item, dict) and item.get('operation') == 'offline_fallback_region_split'
           for item in region_audit):
        body += ('<p class="sg-warning" role="status"><strong>Требуется проверка рисунков.</strong><br>Часть рисунков была размечена локально '
                 'после ошибки или некорректного ответа провайдера. '
                 'Проверьте порядок появления элементов перед публикацией.</p>')
    if state['status'] == 'waiting_approval':
        model_names = ' · '.join(esc(value) for value in state['models'].values())
        body += (f'<details><summary>Модели и голос</summary><p>{model_names}</p>'
                 f'<p>Голос: {esc(state["request"]["voice"])}</p></details>'
                 '<p>Генерация платная. Точная стоимость в долларах пока неизвестна. '
                 'Лимит ниже ограничивает число вызовов, а не сумму списаний. После временных сбоев '
                 'генерация продолжается автоматически, но только в пределах этого лимита.</p>'
                 f'<form method="post" action="{base}/approve" class="sg-form">'
                 f'<input type="hidden" name="request_digest" value="{state["request_digest"]}">'
                 f'<label class="sg-field"><span><input type="checkbox" name="confirmed" value="yes" required> '
                 f'Разрешаю {"перерисовку" if state.get("visual_repair_from") else "генерацию видео"}: до {state["max_api_calls"]} API-вызовов. '
                 + ('До 105 дополнительных потенциально платных попыток при сбоях изображений; резервная модель только после 24 часов. ' if state.get('image_recovery') else '')
                 + '</span></label>'
                 f'<button class="btn primary" type="submit">{"Подтвердить перерисовку" if state.get("visual_repair_from") else "Подтвердить и сгенерировать"}</button></form>')
    if state['status'] in {'queued', 'running', 'waiting_approval'}:
        body += (f'<form method="post" action="{base}/cancel"><button class="btn secondary">Остановить задание</button></form>')
    if state['status'] in {'queued', 'running'}:
        body += '<p>Можно закрыть страницу. Генерация продолжится; статус обновляется автоматически. Задание останется в списке ваших анимаций.</p>'
    if completed:
        body += '</details>'
        if not state.get('visual_repair_from'):
            selected_model = (state.get('models') or {}).get('image', DEFAULT_IMAGE_MODEL)
            if selected_model not in CLAUDEHUB_IMAGE_MODELS:
                selected_model = DEFAULT_IMAGE_MODEL
            repair_options = image_model_options(selected_model)
            body += (f'<form method="post" action="{base}/repair" class="sg-form wb-repair">'
                     f'<label class="sg-field"><span>Модель новых рисунков · ClaudeHub</span>'
                     f'<select name="image_model" aria-label="Модель новых рисунков">{repair_options}</select></label>'
                     '<button type="submit" class="btn secondary">Переделать рисунки</button>'
                     '<small role="status" aria-live="polite">Откроется отдельное задание с явным статусом и подтверждением дополнительных вызовов. Сценарий и MP3 сохранятся.</small></form>'
                     '<script>(()=>{const form=document.querySelector(".wb-repair");'
                     'if(!form)return;form.addEventListener("submit",()=>{'
                     'const button=form.querySelector("button");'
                     'if(button){button.disabled=true;button.textContent="Создаём задание…";}'
                     'const status=form.querySelector("[role=status]");'
                     'if(status)status.textContent="Проверяем сохранённые файлы и создаём заявку на перерисовку…";'
                     '});})();</script>')
    body += f'<p class="wb-return"><a href="/studio/products/{product_id}/whiteboard">К списку видео</a></p></section>'
    return body


def image_model_options(selected=DEFAULT_IMAGE_MODEL):
    return ''.join(
        f'<option value="{value}"' + (' selected' if selected == value else '') + f'>{label}</option>'
        for value, label in [(DEFAULT_IMAGE_MODEL, 'Gemini 3.1 Flash Image'),
                             (GPT_IMAGE_MODEL, 'GPT Image 2.5')]
    )


def creation_form(product_id, *, fields=None, error=None, durations=None):
    fields = fields or {}
    base = f'/studio/products/{product_id}/whiteboard'
    profile = fields.get('profile', 'gemini_ru')
    maximum = str(fields.get('max_api_calls') or 'auto')
    visual_review = fields.get('visual_review', 'once')
    length = str(fields.get('duration_seconds', '30'))
    science_mode = fields.get('science_mode', 'auto')
    duration_options = ''.join(
        f'<option value="{value}"' + (' selected' if length == str(value) else '') + f'>{label}</option>'
        for value, label in [(30, '30 с · 3 сцены'), (60, '60 с · 6 сцен')]
    )
    science_options = ''.join(
        f'<option value="{value}"' + (' selected' if science_mode == value else '') + f'>{label}</option>'
        for value, label in [('auto', 'Автоматически по теме'),
                             ('scientific', 'Научный сюжет — обязательно проверять'),
                             ('general', 'Обычный сюжет (научные утверждения проверяются)')]
    )
    image_options = image_model_options(fields.get('image_model', DEFAULT_IMAGE_MODEL))
    profile_options = ''.join(
        f'<option value="{value}"' + (' selected' if profile == value else '') + f'>{label}</option>'
        for value, label in [('gemini_ru', 'Grok Voice TTS — озвучка'),
                             ('configured', 'Озвучка из настроек сервера')]
    )
    review_options = ''.join(
        f'<option value="{value}"' + (' selected' if visual_review == value else '') + f'>{label}</option>'
        for value, label in [('once', 'Критик один раз, автор выбирает исправления'),
                             ('off', 'Без критика')]
    )
    call_options = ''.join(
        f'<option value="{value}"' + (' selected' if maximum == str(value) else '') + f'>{label}</option>'
        for value, label in [('auto', 'Минимум без запаса на ошибки'),
                             ('robust', 'Автозавершение · до 5 дополнительных вызовов'),
                             (19, '19'), (25, '25'), (34, '34'), (45, '45')]
    )
    feedback = f'<p class="sg-warning" role="alert">{esc(error)}</p>' if error else ''
    return f"""
    <section class="sg-surface wb-create wb-screen" aria-labelledby="wb-create-title">
      <header class="wb-create-head">
        <p class="wb-eyebrow">Видеостудия · 9:16</p>
        <h2 id="wb-create-title">Простое видео</h2>
        <p>Из идеи в готовый ролик: рисунки, русский голос и MP4.</p>
        <div class="wb-specs wb-pipeline" aria-label="Этапы создания">
          <span>Сценарий</span><span>Рисунки</span><span>Озвучка</span><span>MP4</span>
        </div>
      </header>
      {feedback}
      <form method="post" action="{base}" class="sg-form wb-create-form" id="wb-create-form">
        <input type="hidden" name="wb_topic_choice" value="">
        <input type="hidden" name="wb_paid_start" value="">
        <input type="hidden" name="wb_paid_consent" value="">
        <div class="wb-create-main">
          <div class="wb-create-question">
            <div class="wb-topic-top">
              <label for="wb-create-topic">О чём видео?</label>
              <button class="btn secondary wb-suggest" type="button" id="wb-suggest-btn">✦ Подобрать тему</button>
            </div>
            <textarea id="wb-create-topic" name="topic" minlength="8" maxlength="400" rows="2" required
              placeholder="Например: почему горячий предмет массивнее холодного">{esc(fields.get('topic', ''))}</textarea>
          </div>
          <div class="wb-create-settings">
            <label class="sg-field" for="wb-duration"><span>Длительность</span>
              <select id="wb-duration" name="duration_seconds">{duration_options}</select>
            </label>
            <label class="sg-field" for="wb-cta"><span>Призыв <small>· необязательно</small></span>
              <input id="wb-cta" type="text" name="call_to_action" maxlength="200"
                placeholder="Подписка и лайк — по умолчанию"
                value="{esc(fields.get('call_to_action', ''))}">
            </label>
          </div>
        </div>
        <div class="wb-more-grid">
        <details class="wb-create-more">
          <summary>Сценарий и материалы <span>+ добавить</span></summary>
          <div class="wb-create-fields">
            <label class="sg-field"><span>Готовый сценарий</span>
              <textarea name="source_script" rows="5" maxlength="12000"
                placeholder="Вставьте исходный текст ролика">{esc(fields.get('source_script', ''))}</textarea>
              <small>Сценарист сохранит смысл и адаптирует текст под длительность. Важные утверждения не должны изменяться.</small>
            </label>
            <label class="sg-field"><span>Дополнительная информация</span>
              <textarea name="background_notes" rows="3" maxlength="6000"
                placeholder="Аудитория, контекст и ограничения">{esc(fields.get('background_notes', ''))}</textarea>
            </label>
            <label class="sg-field"><span>Материалы для проверки фактов</span>
              <textarea name="reference_materials" rows="3" maxlength="6000"
                placeholder="Источники, ссылки, выдержки">{esc(fields.get('reference_materials', ''))}</textarea>
              <small>Предоставленные материалы сами по себе не считаются независимым подтверждением фактов.</small>
            </label>
          </div>
        </details>
        <details class="wb-create-more">
          <summary>Настройки <span>Авто</span></summary>
          <div class="wb-create-fields">
            <label class="sg-field"><span>Научная проверка</span>
              <select name="science_mode">{science_options}</select>
              <small>Неподтверждённые научные утверждения остановят производство до рисунков и озвучки.</small>
            </label>
            <label class="sg-field"><span>Модель рисунков · ClaudeHub</span>
              <select name="image_model">{image_options}</select>
            </label>
            <label class="sg-field"><span>Модель озвучки</span>
              <select name="profile">{profile_options}</select>
              <small>Выбор сохраняется для этого задания. Модели и голос можно проверить до платного запуска.</small>
            </label>
            <label class="sg-field"><span>Проверка рисунков</span>
              <select name="visual_review">{review_options}</select>
              <small>Один проход критика с выборочной перерисовкой. Для 30 секунд может потребоваться 19 вызовов, для минуты — 34.</small>
            </label>
            <label class="sg-field"><span>Максимум платных API-вызовов</span>
              <select name="max_api_calls">{call_options}</select>
              <small>При «Автозавершении» заранее подтверждается максимум на 5 вызовов больше необходимого: они расходуются только при необходимости. Отдельный резерв восстановления изображений (до 105 потенциально платных попыток) подтверждается при запуске. Это ограничение вызовов, а не стоимости в деньгах.</small>
            </label>
          </div>
        </details>
        </div>
        <footer class="wb-create-actions">
          <div class="wb-create-hint">
            <strong>Сначала — подтверждение</strong>
            <span>Без платного запуска до вашего согласия.</span>
          </div>
          <button class="btn primary" type="submit">Продолжить к подтверждению</button>
        </footer>
      </form>
      <details class="wb-duration-more"><summary>Сколько займёт создание?</summary>
        <p>{esc(duration_notice(durations or {}, 'whiteboard'))}</p>
      </details>
    </section>
    {topic_picker_html(product_id)}
    """


def history_html(states, product_id):
    base = f'/studio/products/{product_id}/whiteboard'
    body = '<section class="sg-surface wb-library" aria-labelledby="wb-library-title"><div class="wb-library-head"><h2 id="wb-library-title">Мои видео</h2><span class="wb-library-count">' + str(len(states)) + '</span></div><div id="whiteboard-jobs" aria-live="polite">'
    for state in states:
        url = f'{base}/{state["task_id"]}'
        repair = bool(state.get('visual_repair_from'))
        kind = 'Перерисовка · ' if repair else ''
        stage = state.get('stage') or ''
        repair_stage = {'image': 'новые рисунки', 'visual_review': 'проверка критика',
                        'visual_decision': 'разбор замечаний', 'image_revision': 'исправление рисунка',
                        'regions': 'области рисования', 'audio_reuse': 'готовое аудио',
                        'rendering': 'сборка MP4'}.get(stage)
        status_label = STATUS_NAMES.get(state['status'], state['status'])
        if repair and state['status'] == 'running' and repair_stage:
            status_label += ' · ' + repair_stage
        body += (f'<article class="wb-library-item"><p class="wb-library-title"><span class="wb-list-icon" aria-hidden="true">▶</span><a href="{url}">{esc(kind + state["request"]["topic"])}</a></p>'
                 f'<p class="wb-library-status">{esc(status_label)}</p>')
        if repair and state['status'] == 'waiting_approval':
            body += f'<p><a class="btn primary" href="{url}">Подтвердить перерисовку</a></p>'
        if repair and state['status'] == 'failed':
            body += f'<p><a class="btn secondary" href="{url}">Посмотреть причину ошибки</a></p>'
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
      const endpoint = ''' + json.dumps(f'/studio/products/{product_id}/whiteboard/jobs.json') + ''';
      const names = ''' + json.dumps(STATUS_NAMES, ensure_ascii=False) + ''';
      const repairStages = {image:'новые рисунки',visual_review:'проверка критика',
        visual_decision:'разбор замечаний',image_revision:'исправление рисунка',
        regions:'области рисования',audio_reuse:'готовое аудио',rendering:'сборка MP4'};
      const counter = document.querySelector('.wb-library-count');
      async function refresh() {
        if (document.hidden) return;
        try {
          const response = await fetch(endpoint, {credentials:'same-origin', cache:'no-store'});
          if (!response.ok) return;
          const states = await response.json();
          if (!Array.isArray(states)) return;
          const fragment = document.createDocumentFragment();
          let shown = 0;
          for (const state of states) {
            if (!/^[0-9a-f-]{36}$/.test(state.task_id) || !state.request || typeof state.request.topic !== 'string') continue;
            shown += 1;
            const article = document.createElement('article'); article.className = 'wb-library-item';
            const title = document.createElement('p'); title.className = 'wb-library-title';
            const link = document.createElement('a');
            link.href = base + '/' + state.task_id;
            const repair = !!state.visual_repair_from;
            link.textContent = (repair ? 'Перерисовка · ' : '') + state.request.topic;
            const glyph = document.createElement('span'); glyph.className = 'wb-list-icon'; glyph.textContent = '▶'; glyph.setAttribute('aria-hidden', 'true');
            title.append(glyph, link); article.append(title);
            const status = document.createElement('p'); status.className = 'wb-library-status';
            status.textContent = (names[state.status] || 'Обновление статуса') +
              (repair && state.status === 'running' && repairStages[state.stage] ? ' · ' + repairStages[state.stage] : '');
            article.append(status);
            if (repair && (state.status === 'waiting_approval' || state.status === 'failed')) {
              const actions = document.createElement('p');
              const action = document.createElement('a'); action.href = link.href;
              action.className = 'btn ' + (state.status === 'waiting_approval' ? 'primary' : 'secondary');
              action.textContent = state.status === 'waiting_approval' ? 'Подтвердить перерисовку' : 'Посмотреть причину ошибки';
              actions.append(action); article.append(actions);
            }
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
          if (!shown) {
            const empty = document.createElement('p');
            empty.textContent = 'Здесь появятся ваши задания.';
            fragment.append(empty);
          }
          root.replaceChildren(fragment);
          if (counter) counter.textContent = String(shown);
        } catch (_) {}
      }
      setInterval(refresh, 8000);
      document.addEventListener('visibilitychange', refresh);
    })();
    </script>'''
    return body


@router.get('/studio/products/{product_id}/whiteboard/topics.json')
def studio_topic_suggestions(product_id: UUID, db: Session = Depends(get_db), settings=Depends(get_settings)):
    return suggest_company_video_topics(settings.products_root, _owner_product(db, product_id))


@router.get('/studio/products/{product_id}/create/topics.json')
def studio_create_topic_suggestions(
    product_id: UUID,
    format: Literal["post", "story", "static_image", "short_video", "whiteboard", "package"] = "post",
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    """Suggestions share tenant authorization with the existing Studio topics."""
    return suggest_company_create_topics(
        settings.products_root, _owner_product(db, product_id), format
    )


@router.get('/studio/products/{product_id}/create/platforms.json')
def studio_create_platforms(product_id: UUID, db: Session = Depends(get_db)):
    """Return saved connection records only, not unverified live API availability."""
    from app.social_vk import connected as vk_connection
    from app.content.telegram_publishing import connected_channels
    product = _owner_product(db, product_id)
    return {
        "vk": {"state": "configured" if vk_connection(db, product.id) else "not_configured"},
        "telegram": {
            "state": "configured" if connected_channels(db, product.id) else "not_configured"
        },
    }


@router.get('/studio/products/{product_id}/whiteboard/quote.json')
@router.post('/studio/products/{product_id}/whiteboard/quote.json')
async def studio_topic_quote(product_id: UUID, request: Request, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    q = await _studio_form(request) if request.method == 'POST' else request.query_params
    try:
        seconds = int(q.get('duration_seconds', '30'))
        draft = WhiteboardRequest(product_id=product_id, topic=q.get('topic', ''),
            duration_seconds=seconds, scenes=SCENES_FOR_SECONDS[seconds],
            science_mode=q.get('science_mode', 'auto'), science_policy_version=1,
            source_script=q.get('source_script', ''))
        needed = required_calls(draft, visual_review=q.get('visual_review', 'once') == 'once')
        raw = q.get('max_api_calls', 'auto')
        limit = needed if raw == 'auto' else min(MAX_API_CALLS, needed + 5) if raw == 'robust' else int(raw)
        if not 13 <= limit <= MAX_API_CALLS:
            raise ValueError('Invalid limit')
    except (TypeError, ValueError, KeyError, ValidationError):
        raise HTTPException(422, 'Проверьте тему и лимит API-вызовов.') from None
    return {'required_calls': needed, 'limit': limit, 'allowed': limit >= needed,
            'image_recovery_reserve': IMAGE_RECOVERY_RESERVE,
            'science_review': requires_science_review(draft)}


@router.get('/studio/products/{product_id}/whiteboard', response_class=HTMLResponse)
async def studio_index(product_id: UUID, db: Session = Depends(get_db), settings=Depends(get_settings)):
    product = _owner_product(db, product_id)
    error = None
    try:
        generation_profile(settings, 'gemini_ru')
        if not settings.openrouter_api_key or not settings.xai_api_key:
            error = MESSAGES['configuration_missing']
    except ValueError:
        error = MESSAGES['configuration_missing']
    body = creation_form(product_id, error=error, durations=task_durations(db))
    tasks = list(db.scalars(select(Task).where(Task.product_id == product_id, Task.assigned_agent == AGENT)
                           .order_by(Task.created_at.desc(), Task.id.desc()).limit(20)))
    body += history_html([_load(db, product_id, task.id) for task in tasks], product_id)
    return page(product, 'Простое видео', body)


@router.post('/studio/products/{product_id}/whiteboard')
async def studio_create(product_id: UUID, request: Request, db: Session = Depends(get_db),
                        actor=Depends(require_studio_auth), settings=Depends(get_settings)):
    product = _owner_product(db, product_id)
    fields = await _studio_form(request)
    try:
        limit = fields.get('max_api_calls', 'auto')
        body = CreateJob(topic=fields.get('topic', ''), max_api_calls=None if limit in ('', 'auto', 'robust') else limit,
                         recovery_budget=limit == 'robust',
                         duration_seconds=int(fields.get('duration_seconds') or 30),
                         call_to_action=(fields.get('call_to_action') or '').strip(),
                         profile=fields.get('profile', 'gemini_ru'),
                         image_model=fields.get('image_model', 'gemini-3.1-flash-image'), visual_review=fields.get('visual_review') == 'once',
                         source_script=fields.get('source_script', ''),
                         background_notes=fields.get('background_notes', ''),
                         reference_materials=fields.get('reference_materials', ''),
                         science_mode=fields.get('science_mode', 'auto'))
    except ValueError:
        return page(product, 'Создать анимацию', creation_form(product_id, fields=fields, durations=task_durations(db),
                    error='Тема: 8–400 символов. Призыв: не короче 6 и не длиннее 200 символов, либо пусто. Выберите длину видео и вариант генерации.'), status=422)
    auto_start = fields.get('wb_paid_start') == 'yes'
    if auto_start:
        topics = suggest_company_video_topics(settings.products_root, product)['topics']
        if (fields.get('wb_paid_consent') != 'yes'
                or fields.get('wb_topic_choice') != body.topic
                or body.topic not in {item['topic'] for item in topics}):
            return page(product, 'Создать анимацию',
                        creation_form(product_id, fields=fields, durations=task_durations(db),
                                      error='Недействительное подтверждение выбранной темы.'),
                        status=409)
    try:
        task = _queue(db, product, body, settings)
    except HTTPException as exc:
        return page(product, 'Создать анимацию', creation_form(product_id, fields=fields, durations=task_durations(db), error=exc.detail), status=exc.status_code)
    if auto_start:
        state = job_state(db, product_id, task.id)
        try:
            await _approve(db, product_id, task.id,
                           ApproveJob(request_digest=state['request_digest'], confirmed=True),
                           actor, settings)
        except HTTPException as exc:
            state = _load(db, product_id, task.id)
            return page(product, 'Генерация видео', job_html(state, product_id, error=exc.detail),
                        status=exc.status_code)
    return RedirectResponse(f'/studio/products/{product_id}/whiteboard/{task.id}', status_code=303)


@router.get('/studio/products/{product_id}/whiteboard/{task_id}', response_class=HTMLResponse)
def studio_job(product_id: UUID, task_id: UUID, db: Session = Depends(get_db)):
    state = _load(db, product_id, task_id)
    title = 'Готовое видео' if state['status'] == 'completed' else 'Генерация видео'
    return page(_owner_product(db, product_id), title, job_html(state, product_id),
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


@router.post('/studio/products/{product_id}/whiteboard/{task_id}/resume')
async def studio_resume(product_id: UUID, task_id: UUID, request: Request, db: Session = Depends(get_db),
                        actor=Depends(require_studio_auth)):
    state = _load(db, product_id, task_id)
    fields = await _studio_form(request)
    try:
        extra = int(fields.get('extra_calls') or 0)
        resume_job(db, product_id, task_id, confirmed=fields.get('confirmed') == 'yes', decided_by=actor or 'local-owner',
                   extra_calls=extra)
    except (ReadinessError, ValueError):
        db.rollback()
        return page(_owner_product(db, product_id), 'Генерация видео',
                    job_html(state, product_id, error=MESSAGES['approval_invalid']), status=409)
    return RedirectResponse(f'/studio/products/{product_id}/whiteboard/{task_id}', status_code=303)


@router.post('/studio/products/{product_id}/whiteboard/{task_id}/repair')
async def studio_repair(product_id: UUID, task_id: UUID, request: Request,
                        db: Session = Depends(get_db), settings=Depends(get_settings)):
    state = _load(db, product_id, task_id)
    from app.content.whiteboard.repair import queue_visual_repair
    # Older clients submitted an empty POST before there was a model selector.
    # Keep that path compatible without accepting a different body format.
    if not request.headers.get('content-type'):
        async for chunk in request.stream():
            if chunk:
                raise HTTPException(415, 'Ожидается форма')
        fields = {}
    else:
        fields = await _studio_form(request)
    try:
        task = queue_visual_repair(db, settings, product_id, task_id, image_model=fields.get('image_model'))
    except (ReadinessError, ValueError, CheckpointError) as exc:
        db.rollback()
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
