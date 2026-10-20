import asyncio
import json
import stat
from types import SimpleNamespace

import httpx
import pytest

from app.content.whiteboard.runtime import ReadinessError, SingleCallStoryboardLLM
from app.llm.schemas import LLMMessage, LLMRequest


def settings():
    return SimpleNamespace(xai_api_key='private-test-text-key',
                           openrouter_api_key='private-test-media-key',
                           xai_base_url='https://provider.example/v1')


@pytest.mark.parametrize('reason,content,error', [
    ('length', '', 'storyboard_truncated'),
    ('stop', '', 'storyboard_empty'),
    ('stop', '{}', None),
])
def test_response_diagnostics_single_call(tmp_path, reason, content, error):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'model': 'test', 'choices': [
            {'finish_reason': reason, 'message': {'content': content}}],
            'usage': {'completion_tokens': 2200}})
    async def run():
        async with httpx.AsyncClient(base_url='https://provider.example/v1/',
                                    transport=httpx.MockTransport(respond)) as client:
            provider = SingleCallStoryboardLLM(settings(), client=client,
                                               diagnostic_directory=tmp_path)
            request = LLMRequest(model='test', messages=[LLMMessage(role='user', content='topic')],
                                 response_schema={'type': 'object'}, max_tokens=2200)
            if error:
                with pytest.raises(ReadinessError) as caught:
                    await provider.generate(request)
                assert caught.value.code == error
            else:
                assert (await provider.generate(request)).content == content
    asyncio.run(run())
    assert len(calls) == 1
    files = list(tmp_path.glob('storyboard-response-*.json'))
    assert len(files) == 1
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    saved = files[0].read_text()
    assert settings().xai_api_key not in saved
    assert settings().openrouter_api_key not in saved
    assert json.loads(saved)['http_status'] == 200


def test_private_capture_redacts_credentials_and_rejects_symlink(tmp_path):
    provider = SingleCallStoryboardLLM(settings(), diagnostic_directory=tmp_path / 'safe')
    provider.record_response(httpx.Response(401, text=settings().xai_api_key))
    saved = next((tmp_path / 'safe').glob('*.json')).read_text()
    assert settings().xai_api_key not in saved
    link = tmp_path / 'linked'
    link.symlink_to(tmp_path / 'safe', target_is_directory=True)
    provider.diagnostic_directory = link
    with pytest.raises(ValueError, match='Symlink'):
        provider.record_response(httpx.Response(200, text='{}'))


def test_storyboard_gets_a_generous_ceiling_and_truncation_is_not_auto_resumed(tmp_path):
    from app.content.whiteboard.jobs import RESUMABLE_ERRORS
    sent = []
    def respond(request):
        sent.append(json.loads(request.content)['max_completion_tokens'])
        return httpx.Response(200, json={'model': 'test', 'choices': [
            {'finish_reason': 'stop', 'message': {'content': '{}'}}]})
    async def run():
        async with httpx.AsyncClient(base_url='https://provider.example/v1/',
                                    transport=httpx.MockTransport(respond)) as client:
            provider = SingleCallStoryboardLLM(settings(), client=client, diagnostic_directory=tmp_path)
            await provider.generate(LLMRequest(model='test', messages=[LLMMessage(role='user', content='t')],
                                               response_schema={'type': 'object'}, max_tokens=2200))
    asyncio.run(run())
    # 2200 cut a normal storyboard off at 2198 tokens (the model reasons before answering).
    assert sent == [16000]
    # A reply that still overruns 16 000 tokens is a genuine failure: do not bill it five more times.
    assert 'storyboard_truncated' not in RESUMABLE_ERRORS and 'storyboard_empty' in RESUMABLE_ERRORS


def _chain_settings(chain):
    value = settings()
    value.whiteboard_storyboard_fallback_models = chain
    return value


def _chain(respond, chain='kimi-k3,grok-4.7,claude-sonnet-5-5'):
    """Run one storyboard call against a fake provider; returns (result_or_exception, models asked)."""
    asked = []
    def route(request):
        asked.append(json.loads(request.content)['model'])
        return respond(asked[-1])
    async def run():
        async with httpx.AsyncClient(base_url='https://provider.example/v1/',
                                    transport=httpx.MockTransport(route)) as client:
            provider = SingleCallStoryboardLLM(_chain_settings(chain), client=client)
            return await provider.generate(LLMRequest(
                model='kimi-2.7', messages=[LLMMessage(role='user', content='t')],
                response_schema={'type': 'object'}, max_tokens=2200))
    try:
        return asyncio.run(run()), asked
    except Exception as exc:  # noqa: BLE001 - the tests inspect the exception
        return exc, asked


def _ok(model):
    return httpx.Response(200, json={'model': model, 'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]})


def test_unavailable_storyboard_model_hands_over_to_kimi_3_then_grok_then_sonnet():
    result, asked = _chain(lambda m: _ok(m) if m != 'kimi-2.7' else httpx.Response(502, json={'error': 'upstream'}))
    assert result.model == 'kimi-k3' and asked == ['kimi-2.7', 'kimi-k3']
    result, asked = _chain(lambda m: httpx.Response(503) if m in {'kimi-2.7', 'kimi-k3'} else _ok(m))
    assert result.model == 'grok-4.7' and asked == ['kimi-2.7', 'kimi-k3', 'grok-4.7']
    result, asked = _chain(lambda m: _ok(m) if m == 'claude-sonnet-5-5' else httpx.Response(502))
    assert result.model == 'claude-sonnet-5-5' and asked == ['kimi-2.7', 'kimi-k3', 'grok-4.7', 'claude-sonnet-5-5']


def test_fallback_skips_unknown_or_refusing_replacement_and_reports_the_provider_outage():
    result, asked = _chain(lambda m: _ok(m) if m == 'grok-4.7' else httpx.Response(404 if m == 'kimi-k3' else 502))
    assert result.model == 'grok-4.7' and asked == ['kimi-2.7', 'kimi-k3', 'grok-4.7']
    result, asked = _chain(lambda m: httpx.Response(502 if m == 'kimi-2.7' else 404))
    # The reported error is the original outage (a refundable 5xx), not the replacements' 404s.
    assert isinstance(result, httpx.HTTPStatusError) and result.response.status_code == 502 and len(asked) == 4


def test_fallback_is_not_used_when_the_model_answered_or_none_is_configured():
    result, asked = _chain(lambda m: httpx.Response(400, json={'error': 'bad request'}))
    assert isinstance(result, httpx.HTTPStatusError) and asked == ['kimi-2.7']
    result, asked = _chain(lambda m: httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'content': ''}}]}))
    assert isinstance(result, ReadinessError) and asked == ['kimi-2.7']
    result, asked = _chain(_ok, chain='')
    assert result.model == 'kimi-2.7' and asked == ['kimi-2.7']
    result, asked = _chain(lambda m: httpx.Response(502), chain='')
    assert isinstance(result, httpx.HTTPStatusError) and asked == ['kimi-2.7']
