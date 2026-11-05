"""Mail on the same machine (127.0.0.1) must not require a TLS certificate for the IP.

Regression: SMTP_HOST=127.0.0.1 with STARTTLS verification always failed with
"[SSL: CERTIFICATE_VERIFY_FAILED] IP address mismatch", so no e-mail was ever sent.
"""
import os
os.environ.setdefault('BOT_TOKEN', 'test-token')
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')

import asyncio

from bot.services import email_notifications as mail


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.calls = []
        FakeSMTP.instances.append(self)

    def ehlo(self): self.calls.append('ehlo')
    def starttls(self, context=None): self.calls.append('starttls')
    def login(self, user, password): self.calls.append('login')
    def send_message(self, message): self.calls.append('send')
    def quit(self): self.calls.append('quit')
    def close(self): self.calls.append('close')


def _send(monkeypatch, host, **env):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(mail.smtplib, 'SMTP', FakeSMTP)
    monkeypatch.setenv('SMTP_HOST', host)
    monkeypatch.setenv('SMTP_FROM_EMAIL', 'no-reply@servgo.ru')
    monkeypatch.delenv('SMTP_STARTTLS', raising=False)
    monkeypatch.delenv('SMTP_USE_SSL', raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    sent = asyncio.run(mail.send_email(to_email='client@example.org', subject='s', text_body='b'))
    return sent, FakeSMTP.instances[0].calls


def test_loopback_server_sends_without_starttls(monkeypatch):
    for host in ('127.0.0.1', 'localhost', '::1'):
        sent, calls = _send(monkeypatch, host, SMTP_PORT='25')
        assert sent and 'starttls' not in calls and 'send' in calls


def test_remote_server_still_uses_starttls(monkeypatch):
    sent, calls = _send(monkeypatch, 'mail.servgo.ru', SMTP_PORT='587')
    assert sent and calls.index('starttls') < calls.index('send')


def test_starttls_can_still_be_forced_by_setting(monkeypatch):
    sent, calls = _send(monkeypatch, '127.0.0.1', SMTP_STARTTLS='true')
    assert 'starttls' in calls
