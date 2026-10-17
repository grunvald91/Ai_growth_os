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
