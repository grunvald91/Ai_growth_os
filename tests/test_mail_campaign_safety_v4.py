"""Mail campaign V4 safety: mocked storage, no SMTP and no external AI."""
import json
import os

import pytest

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from web.api.routes import owner_mail
from web.api.routes.mail_campaign_api import CampaignInput, create_campaign
from web.api.routes.mail_campaign_dispatch import sender_for_campaign
from web.api.routes.mail_campaign_review import CampaignLaunch, launch
from web.api.routes.mail_campaign_store import initialize


@pytest.fixture
def mailenv(tmp_path, monkeypatch):
    data = tmp_path / "mail"
    data.mkdir(mode=0o700)
    monkeypatch.setenv("SERVGO_MAIL_DATA_DIR", str(data))
    monkeypatch.setenv("SERVGO_MAIL_SESSION_SECRET", "a" * 48)
    monkeypatch.setenv("SERVGO_MAIL_FROM", "mail@servgo.ru")
    monkeypatch.delenv("SERVGO_MAIL_COMPANY_SENDERS", raising=False)
    with owner_mail.db_open() as db:
        initialize(db)
    return monkeypatch


def approved_campaign():
    cid = create_campaign(CampaignInput(name="Проверка письма"), None)["id"]
    with owner_mail.db_open() as db:
        db.execute(
            "INSERT INTO campaign_recipient (id,campaign_id,email,company,subject,body,status,created_at) "
            "VALUES ('r1',?,'one@example.org','Тест','Предложение',"
            "'Здравствуйте! Направляем деловое предложение для рассмотрения.','approved',?)",
            (cid, "2026-11-02T07:00:00+00:00"))
    return cid


def test_launch_requires_explicit_attestation(mailenv):
    cid = approved_campaign()
    with pytest.raises(Exception) as error:
        launch(cid, CampaignLaunch(), None)
    assert getattr(error.value, "status_code", None) == 422
    result = launch(cid, CampaignLaunch(legal_basis_confirmed=True), None)
    assert result["status"] == "running"
    with owner_mail.db_open() as db:
        events = [row["event"] for row in db.execute("SELECT event FROM campaign_event")]
    assert "legal_basis_confirmed_by_owner" in events


def test_other_company_needs_verified_sender(mailenv):
    cid = approved_campaign()
    company = "11111111-2222-3333-4444-555555555555"
    with owner_mail.db_open() as db:
        db.execute("UPDATE campaign SET company_id=?,company_brief=? WHERE id=?",
                   (company, '{"name":"Мята"}', cid))
    with pytest.raises(Exception) as error:
        launch(cid, CampaignLaunch(legal_basis_confirmed=True), None)
    assert getattr(error.value, "status_code", None) == 409
    assert sender_for_campaign(company) == ("", "")


def test_company_identity_requires_verified_configuration(mailenv):
    company = "11111111-2222-3333-4444-555555555555"
    mapping = {company: {"email": "hello@mint.example", "name": "Мята", "verified": True}}
    mailenv.setenv("SERVGO_MAIL_COMPANY_SENDERS", json.dumps(mapping))
    assert sender_for_campaign(company) == ("hello@mint.example", "Мята")
    mapping[company]["verified"] = False
    mailenv.setenv("SERVGO_MAIL_COMPANY_SENDERS", json.dumps(mapping))
    assert sender_for_campaign(company) == ("", "")
