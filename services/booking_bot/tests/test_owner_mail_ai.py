"""The mail AI call must not cut reasoning models off and must accept any model from the env."""
import asyncio
import os

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest

from web.api.routes import owner_mail


@pytest.fixture(autouse=True)
def fresh_model_cache():
    owner_mail._TEAM_MODELS.update(at=0.0, models=[])
    yield
    owner_mail._TEAM_MODELS.update(at=0.0, models=[])


class FakeResponse:
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def fake_client(sent, content):
    class Client:
        def __init__(self, timeout):
            sent["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers, json):
            sent["body"] = json
            return FakeResponse(content)

    return Client


def test_hub_uses_model_from_env_and_large_limits(monkeypatch):
    sent = {}
    monkeypatch.setenv("CLAUDEHUB_API_KEY", "test-key")
    monkeypatch.setenv("SERVGO_MAIL_AI_MODEL", "deepseek-v4.1-flash")
    monkeypatch.delenv("SERVGO_MAIL_AI_MAX_TOKENS", raising=False)
    monkeypatch.delenv("SERVGO_MAIL_AI_TIMEOUT", raising=False)
    monkeypatch.setattr(owner_mail.httpx, "AsyncClient", fake_client(sent, '{"subject":"a","body":"b"}'))
    result = asyncio.run(owner_mail.hub("prompt"))
    assert result == {"subject": "a", "body": "b"}
    assert sent["body"]["model"] == "deepseek-v4.1-flash"
    assert sent["body"]["max_completion_tokens"] == 32000
    assert sent["timeout"] == 300.0


def test_hub_reads_json_wrapped_in_code_fence(monkeypatch):
    sent = {}
    monkeypatch.setenv("CLAUDEHUB_API_KEY", "test-key")
    fenced = chr(96) * 3 + 'json\n{"critical": false, "reason": ""}\n' + chr(96) * 3
    monkeypatch.setattr(owner_mail.httpx, "AsyncClient", fake_client(sent, fenced))
    assert asyncio.run(owner_mail.hub("prompt")) == {"critical": False, "reason": ""}


class Status(Exception):
    pass


def scripted_client(sent, team, replies):
    """team: what the growth endpoint answers (a dict, or an exception); replies: ClaudeHub answers in order."""
    httpx = owner_mail.httpx

    class Client:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers):
            sent.setdefault("team_calls", []).append((url, headers["X-Studio-Key"]))
            if isinstance(team, Exception):
                raise team
            return type("R", (), {"status_code": 200, "json": lambda self: team})()

        async def post(self, url, headers, json):
            sent.setdefault("models", []).append(json["model"])
            reply = replies.pop(0)
            if isinstance(reply, int):
                request = httpx.Request("POST", url)
                response = httpx.Response(reply, request=request)

                class Failing:
                    def raise_for_status(self):
                        raise httpx.HTTPStatusError("x", request=request, response=response)
                return Failing()
            return FakeResponse(reply)

    return Client


def test_hub_uses_the_models_chosen_on_the_team_page(monkeypatch):
    sent = {}
    monkeypatch.setenv("CLAUDEHUB_API_KEY", "test-key")
    monkeypatch.setenv("STUDIO_INTERNAL_KEY", "k" * 40)
    monkeypatch.setenv("SERVGO_MAIL_AI_MODEL", "grok-4.7")
    monkeypatch.setattr(owner_mail.httpx, "AsyncClient",
                        scripted_client(sent, {"role": "mail_agent", "models": ["deepseek-v4.1-flash", "kimi-k3"]},
                                        ['{"ok": true}']))
    assert asyncio.run(owner_mail.hub("prompt")) == {"ok": True}
    assert sent["models"] == ["deepseek-v4.1-flash"]
    assert sent["team_calls"] == [("https://growth.servgo.ru/studio/internal/model/mail_agent", "k" * 40)]


def test_hub_moves_to_the_next_chosen_model_when_the_first_is_busy(monkeypatch):
    sent = {}
    monkeypatch.setenv("CLAUDEHUB_API_KEY", "test-key")
    monkeypatch.setenv("STUDIO_INTERNAL_KEY", "k" * 40)
    monkeypatch.setattr(owner_mail.httpx, "AsyncClient",
                        scripted_client(sent, {"models": ["deepseek-v4.1-flash", "kimi-k3"]}, [503, '{"ok": 1}']))
    assert asyncio.run(owner_mail.hub("prompt")) == {"ok": 1}
    assert sent["models"] == ["deepseek-v4.1-flash", "kimi-k3"]


def test_hub_does_not_try_other_models_after_a_rejected_request(monkeypatch):
    import pytest
    sent = {}
    monkeypatch.setenv("CLAUDEHUB_API_KEY", "test-key")
    monkeypatch.setenv("STUDIO_INTERNAL_KEY", "k" * 40)
    monkeypatch.setattr(owner_mail.httpx, "AsyncClient",
                        scripted_client(sent, {"models": ["deepseek-v4.1-flash", "kimi-k3"]}, [401, '{"ok": 1}']))
    with pytest.raises(owner_mail.HTTPException) as caught:
        asyncio.run(owner_mail.hub("prompt"))
    assert caught.value.status_code == 502
    assert sent["models"] == ["deepseek-v4.1-flash"]


def test_hub_keeps_its_own_model_when_the_team_page_does_not_answer(monkeypatch):
    sent = {}
    monkeypatch.setenv("CLAUDEHUB_API_KEY", "test-key")
    monkeypatch.setenv("STUDIO_INTERNAL_KEY", "k" * 40)
    monkeypatch.setenv("SERVGO_MAIL_AI_MODEL", "grok-4.7")
    monkeypatch.setattr(owner_mail.httpx, "AsyncClient",
                        scripted_client(sent, owner_mail.httpx.ConnectError("down"), ['{"ok": 2}']))
    assert asyncio.run(owner_mail.hub("prompt")) == {"ok": 2}
    assert sent["models"] == ["grok-4.7"]


def test_hub_keeps_its_own_model_when_no_choice_was_made(monkeypatch):
    sent = {}
    monkeypatch.setenv("CLAUDEHUB_API_KEY", "test-key")
    monkeypatch.setenv("STUDIO_INTERNAL_KEY", "k" * 40)
    monkeypatch.setenv("SERVGO_MAIL_AI_MODEL", "deepseek-v4.1-flash")
    monkeypatch.setattr(owner_mail.httpx, "AsyncClient", scripted_client(sent, {"models": []}, ['{"ok": 3}']))
    assert asyncio.run(owner_mail.hub("prompt")) == {"ok": 3}
    assert sent["models"] == ["deepseek-v4.1-flash"]


def test_team_page_choice_is_asked_once_a_minute_not_on_every_call(monkeypatch):
    sent = {}
    monkeypatch.setenv("CLAUDEHUB_API_KEY", "test-key")
    monkeypatch.setenv("STUDIO_INTERNAL_KEY", "k" * 40)
    monkeypatch.setattr(owner_mail.httpx, "AsyncClient",
                        scripted_client(sent, {"models": ["kimi-k3"]}, ['{"a": 1}', '{"a": 2}']))
    asyncio.run(owner_mail.hub("one"))
    asyncio.run(owner_mail.hub("two"))
    assert len(sent["team_calls"]) == 1 and sent["models"] == ["kimi-k3", "kimi-k3"]
