"""Explicit model configuration and read-only provider readiness; no generation retries."""
from __future__ import annotations

from app.llm.limits import output_ceiling, rejects_ceiling
import re
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import field_validator

from .contracts import Contract
from app.llm.schemas import LLMResponse, LLMUsage


class WhiteboardModels(Contract):
    storyboard: str
    image: str
    speech: str
    regions: str

    @field_validator('*')
    @classmethod
    def public_id(cls, value):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}', value) or value.startswith(('sk-', 'sk_')):
            raise ValueError('Configure four public model IDs')
        return value


def configured_models(settings) -> WhiteboardModels:
    return WhiteboardModels(**{name: getattr(settings, 'whiteboard_' + name + '_model', '')
                              for name in ('storyboard', 'image', 'speech', 'regions')})


def scoped_settings(settings, models: WhiteboardModels, voice: str):
    """Copy public job choices, retaining the existing credentials and proxy."""
    return settings.model_copy(update={
        **{'whiteboard_' + name + '_model': value for name, value in models.model_dump().items()},
        'whiteboard_voice': voice,
    })


STORYBOARD_ROLE = 'whiteboard_storyboard'


def storyboard_model(settings) -> str:
    """New jobs use the model chosen on the team page; otherwise the configured default."""
    from app.llm.router_store import role_override
    chosen = role_override(settings, STORYBOARD_ROLE)
    return chosen[0] if chosen else settings.whiteboard_storyboard_model


def generation_profile(settings, profile: str):
    if profile == 'gemini_ru':
        return WhiteboardModels(storyboard=storyboard_model(settings),
            image='gemini-3.1-flash-image', speech='x-ai/grok-voice-tts-1.0',
            regions='qwen/qwen3-vl-30b-a3b-instruct'), 'eve'
    if profile == 'configured':
        return (configured_models(settings).model_copy(update={'image': 'gemini-3.1-flash-image',
                                                              'storyboard': storyboard_model(settings)}),
                settings.whiteboard_voice)
    raise ValueError('Unknown generation profile')


class ReadinessError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


MESSAGES = {
    'configuration_missing': 'Администратору нужно настроить ключи и модели генерации.',
    'openrouter_security_policy': 'OpenRouter отклонил доступ сервера: HTTP 403, Access denied by security policy. Новые вызовы остановлены. Администратору нужно обратиться в поддержку OpenRouter.',
    'openrouter_forbidden': 'OpenRouter вернул HTTP 403. Новые вызовы остановлены; проверьте разрешения аккаунта и доступ сервера.',
    'openrouter_key_invalid': 'OpenRouter не принял API-ключ. Администратору нужно проверить ключ.',
    'openrouter_unavailable': 'OpenRouter сейчас недоступен с сервера. Платная генерация не началась.',
    'model_unavailable': 'Выбранная модель или голос отсутствует в каталоге провайдера.',
    'approval_invalid': 'Подтверждение отсутствует или относится к другой версии задания.',
    'call_limit': 'Достигнут подтверждённый лимит API-вызовов. Чтобы доделать видео, добавьте вызовы и продолжите: готовые этапы повторно не оплачиваются.',
    'generation_failed': 'Генерация остановлена. Завершённые этапы сохранены; стоимость незавершённого вызова может быть неизвестна. Можно продолжить с места остановки.',
    'provider_server_error': 'Сервис модели вернул ошибку на своей стороне (HTTP 5xx) — это сбой провайдера, а не задания. Готовые этапы сохранены; можно продолжить с места остановки.',
    'storyboard_empty': 'Модель сценария вернула пустой ответ. Генерация остановлена до создания изображений.',
    'storyboard_truncated': 'Модель сценария писала больше 16 000 токенов и так и не закончила ответ — это сбой модели, а не нехватка лимита. Генерация остановлена до создания изображений; попробуйте ещё раз или переформулируйте тему.',
    'interrupted': 'Worker был остановлен. Готовые этапы сохранены; можно продолжить с места остановки.',
    'cancelled': 'Задание остановлено. Начатый API-вызов мог завершиться и быть оплачен; следующие вызовы запрещены.',
    'review_budget': 'В сценарии больше реплик, чем предусмотрено лимитом проверки рисунков. Генерация остановлена до изображений; автоматического повтора нет.',
    'repair_source_invalid': 'Исходный сценарий или озвучка не прошли проверку сохранённых файлов. Визуальная правка остановлена; новая озвучка не запускается.',
    'repair_budget': 'В подтверждённом лимите исходного ролика осталось меньше 8 вызовов для визуальной правки. Повтор автоматически не запускается.',
    'image_provider_required': 'Новые рисунки разрешены только через Gemini 3.1 Flash Image в ClaudeHub. Создайте новый запрос с этими настройками; автоматической замены нет.',
    'claudehub_image_unavailable': 'Gemini 3.1 Flash Image недоступна с текущим ключом ClaudeHub. Новые изображения не заказаны; замены модели нет.',
}


# The provider did not answer; nothing was produced, so another model may take over.
TRANSIENT_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def safe_error(exc) -> str:
    if isinstance(exc, ReadinessError):
        return exc.code
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 403:
            return 'openrouter_security_policy' if 'Access denied by security policy' in exc.response.text else 'openrouter_forbidden'
        if exc.response.status_code == 401:
            return 'openrouter_key_invalid'
        if exc.response.status_code >= 500:
            return 'provider_server_error'
    return 'generation_failed'


async def preflight(settings, models: WhiteboardModels, voice: str, *, client=None, claudehub_client=None):
    """Only GETs. Fail before any paid call, using the actual adapter's HTTP client."""
    if not settings.openrouter_api_key or not settings.xai_api_key:
        raise ReadinessError('configuration_missing')
    try:
        if configured_models(settings) != models or voice != settings.whiteboard_voice:
            raise ReadinessError('configuration_missing')
    except ValueError:
        raise ReadinessError('configuration_missing') from None
    async def check(client):
        headers = {'Authorization': 'Bearer ' + settings.openrouter_api_key}
        response = await client.get('key', headers=headers)
        if response.status_code == 403:
            code = 'openrouter_security_policy' if 'Access denied by security policy' in response.text else 'openrouter_forbidden'
            raise ReadinessError(code)
        if response.status_code == 401:
            raise ReadinessError('openrouter_key_invalid')
        if response.status_code != 200:
            raise ReadinessError('openrouter_unavailable')
        catalogues = [('models?output_modalities=speech', models.speech), ('models', models.regions)]
        if models.image != 'gemini-3.1-flash-image':
            catalogues.insert(0, ('images/models', models.image))
        for endpoint, chosen in catalogues:
            result = await client.get(endpoint, headers=headers)
            if result.status_code != 200:
                raise ReadinessError('openrouter_unavailable')
            entries = result.json().get('data', [])
            match = next((m for m in entries if m.get('id') == chosen), None)
            if match is None:
                raise ReadinessError('model_unavailable')
            if endpoint == 'models?output_modalities=speech':
                voices = match.get('supported_voices')
                if voices and voice not in voices:
                    raise ReadinessError('model_unavailable')
        return {'ready': True, 'code': 'ready'}
    async def check_claudehub(connection):
        response = await connection.get('models', headers={'Authorization':'Bearer '+settings.xai_api_key})
        if response.status_code != 200 or not any(item.get('id') == 'gemini-3.1-flash-image' for item in response.json().get('data',[])):
            raise ReadinessError('claudehub_image_unavailable')
    try:
        if models.image == 'gemini-3.1-flash-image':
            from urllib.parse import urlsplit
            url = urlsplit(settings.xai_base_url)
            if url.scheme != 'https' or url.hostname != 'api.claudehub.fun' or url.path.rstrip('/') != '/v1' or url.username or url.password or url.query or url.fragment:
                raise ReadinessError('claudehub_image_unavailable')
            if claudehub_client is not None:
                await check_claudehub(claudehub_client)
            else:
                async with httpx.AsyncClient(base_url=settings.xai_base_url.rstrip('/')+'/', timeout=15,
                        follow_redirects=False, trust_env=False) as connection:
                    await check_claudehub(connection)
        if client is not None:
            return await check(client)
        async with httpx.AsyncClient(base_url='https://openrouter.ai/api/v1/', timeout=15, follow_redirects=False,
                                     proxy=settings.whiteboard_proxy_url, trust_env=False) as connection:
            return await check(connection)
    except ReadinessError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise ReadinessError('openrouter_unavailable') from None


class SingleCallStoryboardLLM:
    """Configured ClaudeHub-compatible provider.

    One POST per model. When the chosen model does not answer at all (provider 5xx/429, no
    connection) the next configured model is tried, so a provider outage does not stop the job.
    A model that answered (even with a bad result) is never retried or replaced here."""
    def __init__(self, settings, *, client=None, diagnostic_directory=None):
        self.settings, self.client = settings, client
        self.diagnostic_directory = diagnostic_directory

    def record_response(self, response, *, name='storyboard'):
        # Private response capture permits offline diagnosis without repeating a paid call.
        # No request, credentials, headers or URL are recorded.
        if self.diagnostic_directory is None:
            return
        if not re.fullmatch(r'[a-z][a-z-]{0,40}', name):
            raise ValueError('Invalid diagnostic name')
        directory = Path(self.diagnostic_directory)
        if directory.is_symlink() or any(p.is_symlink() for p in directory.parents):
            raise ValueError('Symlink diagnostic directory rejected')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        from app.privacy import PrivacyGateway
        body = response.text if len(response.content) <= 100000 else '{}'
        for key in (self.settings.xai_api_key, self.settings.openrouter_api_key):
            if key:
                body = body.replace(key, '[REDACTED]')
        body = PrivacyGateway().sanitize_text(body)
        path = directory / (name + '-response-' + uuid4().hex + '.json')
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump({'http_status': response.status_code, 'body': body}, stream, ensure_ascii=False)

    def fallback_models(self, model):
        from app.llm.router_store import role_override
        raw = getattr(self.settings, 'whiteboard_storyboard_fallback_models', '') or ''
        # The owner's reserve models (team page) go first, then the built-in chain.
        chosen = role_override(self.settings, STORYBOARD_ROLE)[1:]
        chain = chosen + [name.strip() for name in raw.split(',') if name.strip()]
        return [name for index, name in enumerate(chain) if name != model and name not in chain[:index]]

    async def generate(self, request):
        settings = self.settings
        if not settings.xai_api_key:
            raise ReadinessError('configuration_missing')
        messages = ([{'role': 'system', 'content': request.system_prompt}] if request.system_prompt else [])
        messages += [m.model_dump() for m in request.messages]

        async def send(connection, model):
            payload = {'model': model, 'messages': messages, 'max_completion_tokens': output_ceiling(request.max_tokens),
                       'response_format': {'type': 'json_schema', 'json_schema': {
                           'name': 'whiteboard_storyboard', 'strict': True, 'schema': request.response_schema}}}
            response = await connection.post('chat/completions', json=payload,
                                             headers={'Authorization': 'Bearer ' + settings.xai_api_key})
            self.record_response(response)
            if (payload['max_completion_tokens'] != request.max_tokens
                    and rejects_ceiling(response.status_code, response.text)):
                # This model has a smaller output limit: retry once with the caller's value.
                payload['max_completion_tokens'] = request.max_tokens
                response = await connection.post('chat/completions', json=payload,
                                                 headers={'Authorization': 'Bearer ' + settings.xai_api_key})
                self.record_response(response)
            response.raise_for_status()
            data = response.json()
            usage = data.get('usage') or {}
            choice = data['choices'][0]
            content = choice['message'].get('content')
            reason = choice.get('finish_reason')
            logging.getLogger(__name__).warning('whiteboard_storyboard_response %s', json.dumps({
                'model': model,
                'http_status': response.status_code,
                'finish_reason': reason if reason in {'stop', 'length', 'content_filter', 'tool_calls'} else 'unknown',
                'content_chars': len(content) if isinstance(content, str) else 0,
                'output_tokens': usage.get('completion_tokens'),
            }))
            if reason == 'length':
                raise ReadinessError('storyboard_truncated')
            if not isinstance(content, str) or not content.strip():
                raise ReadinessError('storyboard_empty')
            return LLMResponse(model=str(data.get('model') or model),
                               content=content,
                               usage=LLMUsage(input_tokens=usage.get('prompt_tokens', 0), output_tokens=usage.get('completion_tokens', 0)))

        async def with_fallbacks(connection):
            candidates = [request.model, *self.fallback_models(request.model)]
            first_error = None
            for index, model in enumerate(candidates):
                try:
                    return await send(connection, model)
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    # The chosen model: only "provider did not answer" moves on. A replacement that is
                    # unknown or refuses this request format is skipped as well.
                    if index == 0 and status not in TRANSIENT_STATUSES:
                        raise
                    first_error = first_error or exc
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    first_error = first_error or exc
                if index + 1 < len(candidates):
                    logging.getLogger(__name__).warning('whiteboard_storyboard_fallback %s', json.dumps({
                        'from': model, 'to': candidates[index + 1],
                        'http_status': getattr(getattr(first_error, 'response', None), 'status_code', None)}))
            raise first_error

        if self.client is not None:
            return await with_fallbacks(self.client)
        # A full storyboard at ClaudeHub takes ~2 min typically and up to ~7 min;
        # a client ReadTimeout drops a call that may already be billed.
        async with httpx.AsyncClient(base_url=settings.xai_base_url.rstrip('/') + '/',
                                     timeout=httpx.Timeout(600.0, connect=15.0), follow_redirects=False) as connection:
            return await with_fallbacks(connection)
