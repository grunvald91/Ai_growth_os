"""Outbox shows what was sent so the owner can read it and write again."""
import os

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from web.api.routes import owner_mail


def test_outbox_returns_subject_and_text_for_direct_and_campaign_mail(tmp_path, monkeypatch):
    data = tmp_path / "mail"
    data.mkdir(mode=0o700)
    monkeypatch.setenv("SERVGO_MAIL_DATA_DIR", str(data))
    with owner_mail.db_open() as db:
        db.execute("INSERT INTO prospects (id,company,email,website,notes) VALUES ('p1','Мята','hi@mint.ru','','')")
        db.execute("INSERT INTO drafts (prospect_id,subject,body,reviewed,state) VALUES ('p1','Тема А','Текст А',1,'smtp_accepted')")
        db.execute("INSERT INTO deliveries (id,prospect_id,status,created_at) VALUES ('d1','p1','smtp_accepted','2026-10-17T10:00:00')")
        db.execute("INSERT INTO direct_mail (id,email,subject,body,status,created_at) VALUES "
                   "('m1','x@y.ru','Тема Б','Текст Б','delivery_unknown','2026-10-18T10:00:00')")
    rows = owner_mail.outbox(None)
    assert [(r["email"], r["subject"], r["body"], r["status"]) for r in rows] == [
        ("x@y.ru", "Тема Б", "Текст Б", "delivery_unknown"),
        ("hi@mint.ru", "Тема А", "Текст А", "smtp_accepted"),
    ]


def test_outbox_tab_offers_compose_and_write_again():
    page = (owner_mail.Path(owner_mail.__file__).parent / "owner_mail_ui.html").read_text(encoding="utf-8")
    assert all(x in page for x in ('id="list-compose"', "Написать ещё раз", "openCompose(", "↩ Ответить", 'id="fab-compose"', "in_reply_to"))


def test_reply_goes_to_sender_and_keeps_the_thread():
    import email
    import email.policy
    msg = email.message_from_string(
        "From: =?utf-8?b?0JDQvdC90LA=?= <Anna@Example.ru>\nReply-To: desk@example.ru\n"
        "Message-ID: <abc.1@example.ru>\nSubject: Hi\n\nBody", policy=email.policy.default)
    assert owner_mail.reply_fields(msg) == {"reply_to": "desk@example.ru", "message_id": "<abc.1@example.ru>"}
    bad = email.message_from_string("From: nobody\nMessage-ID: no brackets\n\nx", policy=email.policy.default)
    assert owner_mail.reply_fields(bad) == {"reply_to": "", "message_id": ""}


def test_direct_send_sets_reply_headers(tmp_path, monkeypatch):
    data = tmp_path / "mail"
    data.mkdir(mode=0o700)
    monkeypatch.setenv("SERVGO_MAIL_DATA_DIR", str(data))
    monkeypatch.setenv("SERVGO_MAIL_FROM", "hello@servgo.ru")
    sent = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def send_message(self, message, **kwargs):
            sent.append(message)
            return {}
    monkeypatch.setattr(owner_mail.smtplib, "SMTP", FakeSMTP)
    info = owner_mail.DirectSendInput(recipient="anna@example.ru", subject="Re: Hi", body="Ответ",
                                      request_id="a" * 32, in_reply_to="<abc.1@example.ru>")
    assert owner_mail.direct_send(info, None)["status"] == "smtp_accepted"
    assert sent[0]["In-Reply-To"] == "<abc.1@example.ru>" and sent[0]["References"] == "<abc.1@example.ru>"
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        owner_mail.direct_send(owner_mail.DirectSendInput(recipient="anna@example.ru", subject="Re: Hi", body="x",
                               request_id="b" * 32, in_reply_to="<bad\r\nBcc: x@y.ru>"), None)
