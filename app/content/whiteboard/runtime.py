"""Explicit model configuration and read-only provider readiness; no generation retries."""
from __future__ import annotations

import re

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
    'call_limit': 'Достигнут подтверждённый лимит API-вызовов. Автоматического повтора нет.',
    'generation_failed': 'Генерация остановлена. Завершённые этапы сохранены; стоимость незавершённого вызова может быть неизвестна. Автоматического повтора нет.',
    'interrupted': 'Worker был остановлен. Этапы сохранены; требуется проверить результат и списания у провайдера перед новым запуском.',
    'cancelled': 'Задание остановлено. Начатый API-вызов мог завершиться и быть оплачен; следующие вызовы запрещены.',
}


def safe_error(exc) -> str:
    if isinstance(exc, ReadinessError):
        return exc.code
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 403:
            return 'openrouter_security_policy' if 'Access denied by security policy' in exc.response.text else 'openrouter_forbidden'
        if exc.response.status_code == 401:
            return 'openrouter_key_invalid'
    return 'generation_failed'


async def preflight(settings, models: WhiteboardModels, voice: str, *, client=None):
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
        for endpoint, chosen in (('images/models', models.image), ('models?output_modalities=speech', models.speech), ('models', models.regions)):
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
    try:
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
    """Configured ClaudeHub-compatible provider, exactly one POST and no fallbacks."""
    def __init__(self, settings, *, client=None):
        self.settings, self.client = settings, client

    async def generate(self, request):
        settings = self.settings
        if not settings.xai_api_key:
            raise ReadinessError('configuration_missing')
        messages = ([{'role': 'system', 'content': request.system_prompt}] if request.system_prompt else [])
        messages += [m.model_dump() for m in request.messages]
        payload = {'model': request.model, 'messages': messages, 'max_completion_tokens': request.max_tokens,
                   'response_format': {'type': 'json_schema', 'json_schema': {
                       'name': 'whiteboard_storyboard', 'strict': True, 'schema': request.response_schema}}}
        async def send(connection):
            response = await connection.post('chat/completions', json=payload,
                                             headers={'Authorization': 'Bearer ' + settings.xai_api_key})
            response.raise_for_status()
            data = response.json()
            usage = data.get('usage') or {}
            return LLMResponse(model=str(data.get('model') or request.model),
                               content=data['choices'][0]['message']['content'],
                               usage=LLMUsage(input_tokens=usage.get('prompt_tokens', 0), output_tokens=usage.get('completion_tokens', 0)))
        if self.client is not None:
            return await send(self.client)
        async with httpx.AsyncClient(base_url=settings.xai_base_url.rstrip('/') + '/', timeout=120, follow_redirects=False) as connection:
            return await send(connection)
