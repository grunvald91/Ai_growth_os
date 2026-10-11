"""Offline acceptance checks for the audited creation-to-publication failures."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from uuid import UUID
from uuid import uuid4

import pytest
from html.parser import HTMLParser
from sqlalchemy import select
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import Settings, get_settings
from app.content.quality import factual_claim_warnings
from app.content.service import queue_content
from app.content.revisions import source_fingerprint
from app.content.telegram_publishing import schedule_post, run_due_telegram_post_once, reconcile_publication
from app.models.domain import Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus
from test_content_studio_v5 import studio_v5
from test_telegram_publishing import FakeTelegram, company_and_post, sessions


def element_text(markup, element_id):
    class Parser(HTMLParser):
        depth = 0
        pieces = []
        def handle_starttag(self, tag, attrs):
            if self.depth or dict(attrs).get("id") == element_id:
                self.depth += 1
        def handle_endtag(self, tag):
            if self.depth:
                self.depth -= 1
        def handle_data(self, data):
            if self.depth:
                self.pieces.append(data)
    parser = Parser()
    parser.pieces = []
    parser.feed(markup)
    return ''.join(parser.pieces)


def test_long_cyrillic_brief_opens_its_own_task_and_preserves_invalid_form(studio_v5):
    client, db, company, _ = studio_v5
    base = f"/studio/products/{company.id}"
    result = client.post(base + "/create", data={"format": "post", "channel": "telegram",
        "topic": "Полезный пост", "editorial_brief": "я" * 3500}, follow_redirects=False)
    assert result.status_code == 303
    page = client.get(result.headers["location"])
    assert page.status_code == 200 and "В очереди" in page.text
    assert page.headers["refresh"] == "8"
    task = db.scalar(select(Task).where(Task.product_id == company.id))
    payload = db.scalar(select(Artifact).where(Artifact.task_id == task.id))
    assert json.loads(payload.content)["editorial_brief"] == "я" * 3500
    invalid = client.post(base + "/create", data={"format": "post", "channel": "wrong",
        "topic": "Сохраните мою тему", "editorial_brief": "Сохраните требования"})
    assert invalid.status_code == 422
    assert "Сохраните мою тему" in invalid.text and "Сохраните требования" in invalid.text


def test_failed_creative_task_is_visible_and_retries_once(studio_v5):
    client, db, company, _ = studio_v5
    task = Task(product_id=company.id, assigned_agent="content_creative", title="Визуальный бриф", status=TaskStatus.FAILED)
    db.add(task); db.commit()
    base = f"/studio/products/{company.id}"
    assert "Визуальный бриф" in client.get(base + "/create").text
    assert "Повторить подготовку" in client.get(base + f"/tasks/{task.id}").text
    assert client.post(base + f"/tasks/{task.id}/retry", follow_redirects=False).status_code == 303
    assert client.post(base + f"/tasks/{task.id}/retry").status_code == 409


def test_changed_audience_and_brief_are_distinct_jobs(db):
    company = Product(name="Компания", slug="dedupe-audit"); db.add(company); db.commit()
    first = queue_content(db, company.id, "Одна тема", format="post", channel="telegram", editorial_brief="Коротко")
    second = queue_content(db, company.id, "Одна тема", format="post", channel="telegram", editorial_brief="Подробно")
    repeated = queue_content(db, company.id, "Одна тема", format="post", channel="telegram", editorial_brief="Подробно")
    assert first.id != second.id and second.id == repeated.id


def test_clean_copy_and_manual_revision_keep_previous_bytes(studio_v5):
    client, db, company, _ = studio_v5
    source = Artifact(product_id=company.id, kind="channel_text", title="Пост о выборе", status=ArtifactStatus.APPROVED,
                      content=json.dumps({"channel":"telegram", "body":"Только текст публикации", "origin":"quick_post"}, ensure_ascii=False))
    db.add(source); db.commit()
    old = source.content
    url = f"/studio/products/{company.id}/materials/{source.id}"
    detail = client.get(url)
    assert element_text(detail.text, "material-copy") == "Только текст публикации"
    assert "Предпросмотр" not in element_text(detail.text, "material-copy")
    form = {"source_version":str(source.version), "source_digest":source_fingerprint(source),
            "title":"Пост после правки", "edit_0":"Новая версия полезного поста"}
    result = client.post(url + "/edit", data=form, follow_redirects=False)
    assert result.status_code == 303
    db.refresh(source)
    assert source.content == old and source.status == ArtifactStatus.REJECTED
    revised = db.get(Artifact, UUID(result.headers["location"].split("/")[-1]))
    assert revised.status == ArtifactStatus.AWAITING_APPROVAL
    assert json.loads(revised.content)["body"] == "Новая версия полезного поста"
    assert client.post(url + "/edit", data=form).status_code == 409
    assert list(db.scalars(select(Publication))) == []


def test_scheduled_post_cannot_be_edited(studio_v5):
    client, db, company, _ = studio_v5
    post = Artifact(product_id=company.id, kind="channel_text", title="Пост", status="approved",
                    content=json.dumps({"channel":"telegram", "body":"Исходный текст"}))
    db.add(post); db.flush()
    db.add(Publication(product_id=company.id, artifact_id=post.id, channel="telegram", status="scheduled")); db.commit()
    form = {"source_version":str(post.version), "source_digest":source_fingerprint(post), "title":"Новый пост", "edit_0":"Новый текст"}
    result = client.post(f"/studio/products/{company.id}/materials/{post.id}/edit", data=form)
    assert result.status_code == 409
    assert json.loads(post.content)["body"] == "Исходный текст"


def test_old_publications_remain_reachable_through_history_pages(studio_v5):
    client, db, company, _ = studio_v5
    post = Artifact(product_id=company.id, kind="channel_text", title="Исторический пост", content='{}')
    db.add(post); db.flush()
    for index in range(31):
        db.add(Publication(product_id=company.id, artifact_id=post.id, channel="telegram", status="published",
                           created_at=datetime.now(timezone.utc) - timedelta(minutes=index)))
    db.commit()
    base = f"/studio/products/{company.id}/channels"
    first, second = client.get(base), client.get(base + "?page=2")
    assert first.status_code == second.status_code == 200
    assert "Страница 1 из 2 · Всего 31" in first.text and '?page=2' in first.text
    assert "Страница 2 из 2 · Всего 31" in second.text and '?page=1' in second.text
    assert first.text.count('class="sg-publication-row"') == 30
    assert second.text.count('class="sg-publication-row"') == 1


def test_manual_article_version_links_to_original(studio_v5):
    client, db, company, _ = studio_v5
    article = Artifact(product_id=company.id, kind="article", title="Исходная статья", status="approved",
                       content=json.dumps({"full_article":"Первая версия статьи"}))
    db.add(article); db.commit()
    base = f"/studio/products/{company.id}/materials/"
    response = client.post(base + str(article.id) + "/edit", data={"source_version":str(article.version),
        "source_digest":source_fingerprint(article), "title":"Исправленная статья", "edit_0":"Исправленный текст"}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get(response.headers["location"])
    assert page.status_code == 200 and "История материала" in page.text
    assert base + str(article.id) in page.text
    assert json.loads(article.content)["full_article"] == "Первая версия статьи"


def test_standalone_scheduled_post_appears_in_calendar_in_moscow_time(studio_v5):
    client, db, company, _ = studio_v5
    post = Artifact(product_id=company.id, kind="channel_text", title="Назначенная отправка", status="approved",
                    content=json.dumps({"channel":"telegram", "body":"Текст"}))
    conn = Artifact(product_id=company.id, kind="telegram_channel_connection", title="Мой канал", status="approved",
                    content=json.dumps({"chat_id":"-1001234567890"}))
    db.add_all([post, conn]); db.commit()
    now = datetime.now(timezone.utc)
    when = (now + timedelta(days=1)).replace(hour=12, minute=34)
    pub = schedule_post(db, post.id, conn.id, when, now=now)
    response = client.get(f"/studio/products/{company.id}/calendar")
    assert response.status_code == 200
    assert "Назначенная отправка" in response.text and "15:34" in response.text
    channels = client.get(f"/studio/products/{company.id}/channels")
    assert "Мой канал" in channels.text and "15:34" in channels.text


def test_telegram_length_is_rejected_before_publication_is_created(db):
    company, post = company_and_post(db)
    connection = Artifact(product_id=company.id, kind="telegram_channel_connection", title="Канал", status="approved",
                          content=json.dumps({"chat_id":"-1001234567890"}))
    db.add(connection); db.commit()
    post.content = json.dumps({"channel":"telegram", "body":"😀" * 2049}); db.commit()
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="4096"):
        schedule_post(db, post.id, connection.id, now + timedelta(minutes=1), now=now)
    assert list(db.scalars(select(Publication))) == []


@pytest.mark.asyncio
async def test_due_post_is_not_starved_by_501_future_publications(db):
    company, post = company_and_post(db)
    conn = Artifact(product_id=company.id, kind="telegram_channel_connection", title="Канал", status="approved",
                    content=json.dumps({"chat_id":"-1001234567890"}))
    db.add(conn); db.commit()
    now = datetime.now(timezone.utc)
    for _ in range(501):
        db.add(Publication(product_id=company.id, artifact_id=post.id, channel="telegram", status="scheduled",
                           scheduled_at=now + timedelta(days=10)))
    db.commit()
    pub = schedule_post(db, post.id, conn.id, now + timedelta(minutes=1), now=now)
    bot = FakeTelegram()
    settings = Settings(telegram_bot_token="fake", content_telegram_autopost_enabled=True, _env_file=None)
    assert await run_due_telegram_post_once(sessions(db), settings, api=bot, now=now + timedelta(minutes=2)) == 1
    db.refresh(pub)
    assert pub.status == "published"
    assert len([m for m, _ in bot.calls if m == "sendMessage"]) == 1


@pytest.mark.asyncio
async def test_crashed_send_requires_reconciliation_even_with_dispatch_disabled(db):
    company, post = company_and_post(db)
    conn = Artifact(product_id=company.id, kind="telegram_channel_connection", title="Канал", status="approved",
                    content=json.dumps({"chat_id":"-1001234567890"}))
    db.add(conn); db.commit()
    now = datetime.now(timezone.utc)
    pub = schedule_post(db, post.id, conn.id, now + timedelta(minutes=1), now=now)
    pub.status = "sending"; pub.sending_started_at = now - timedelta(minutes=16); db.commit()
    bot = FakeTelegram()
    await run_due_telegram_post_once(sessions(db), Settings(_env_file=None), api=bot, now=now)
    db.refresh(pub)
    assert pub.status == "delivery_unknown" and bot.calls == []
    reconcile_publication(db, pub.id, product_id=company.id, outcome="published", message_id=876)
    assert pub.status == "published" and pub.published_at is None
    with pytest.raises(ValueError):
        reconcile_publication(db, pub.id, product_id=company.id, outcome="not_sent")


def test_invented_numbers_require_explicit_fact_review(studio_v5):
    client, db, company, _ = studio_v5
    claims = "У нас 1000 довольных клиентов. Скидка 50%."
    assert factual_claim_warnings(claims)
    post = Artifact(product_id=company.id, kind="channel_text", title="Проверка фактов", status="awaiting_approval",
                    content=json.dumps({"channel":"telegram", "body":claims, "fact_check_required":True}))
    db.add(post); db.commit()
    url = f"/studio/products/{company.id}/review/{post.id}/approve"
    assert client.post(url, data={"facts_confirmed":"no"}).status_code == 422
    assert post.status == ArtifactStatus.AWAITING_APPROVAL
    assert client.post(url, data={"facts_confirmed":"yes"}, follow_redirects=False).status_code == 303


@pytest.mark.asyncio
async def test_fake_provider_claims_are_flagged_in_real_generation_path(db, tmp_path):
    from test_quick_post import FakePostProvider
    from app.content.quick_post import QuickPostRequest, run_quick_post
    class UnverifiedProvider(FakePostProvider):
        async def generate(self, request):
            response = await super().generate(request)
            if request.metadata["team_role"] == "post_writer_editor":
                value = json.loads(response.content)
                value["body"] += " У нас 1000 довольных клиентов. Скидка 50%."
                response = response.model_copy(update={"content":json.dumps(value, ensure_ascii=False)})
            return response
    company = Product(name="Мастерская", slug="facts-review-audit"); db.add(company); db.commit()
    task = queue_content(db, company.id, "Подарок близким", format="post", channel="telegram")
    payload = db.scalar(select(Artifact).where(Artifact.task_id == task.id, Artifact.kind == "content_request"))
    request = QuickPostRequest.model_validate_json(payload.content).model_copy(update={"task_id":task.id})
    assert await run_quick_post(db, Settings(products_root=tmp_path, _env_file=None), request, provider=UnverifiedProvider())
    post = db.scalar(select(Artifact).where(Artifact.task_id == task.id, Artifact.kind == "channel_text"))
    content = json.loads(post.content)
    assert content["fact_check_required"] is True
    assert "1000 довольных клиентов" in ' '.join(content["quality_warnings"])
    assert "50%" in ' '.join(content["quality_warnings"])
    assert post.status == ArtifactStatus.AWAITING_APPROVAL
    assert list(db.scalars(select(Publication))) == []


def test_client_account_cannot_access_another_company_or_global_api(studio_v5, tmp_path):
    client, db, company, other = studio_v5
    salt = "12" * 16
    password_hash = "pbkdf2_sha256$600000$" + salt + "$" + hashlib.pbkdf2_hmac("sha256", b"test-password", bytes.fromhex(salt), 600000).hex()
    users = tmp_path / "users.json"
    users.write_text(json.dumps({"client":{"password_hash":password_hash, "product_ids":[str(company.id)]}}))
    settings = Settings(app_env="production", studio_users_file=users, studio_public_origin="https://growth.servgo.ru", _env_file=None)
    app.dependency_overrides[get_settings] = lambda: settings
    auth = ("client", "test-password")
    try:
        assert client.get(f"/studio/products/{company.id}/create", auth=auth).status_code == 200
        assert client.get(f"/studio/products/{other.id}/create", auth=auth).status_code == 404
        assert client.get(f"/products/{other.id}", auth=auth).status_code == 404
        response = client.get("/products", auth=auth)
        assert response.status_code == 200 and [p["id"] for p in response.json()] == [str(company.id)]
        assert client.get("/control/status", auth=auth).status_code == 403
        assert client.get("/products").status_code == 401
        assert other.name not in client.get("/studio?choose=true", auth=auth).text
        assert client.post(f"/products/{company.id}/goals", auth=auth, json={"name":"Goal"}, headers={"Origin":"https://evil.example"}).status_code == 403
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_markdown_article_is_rendered_safely_and_copy_keeps_source(studio_v5):
    client, db, company, _ = studio_v5
    text = "## Заголовок\n\n**Важная мысль**\n<script>alert(1)</script>"
    article = Artifact(product_id=company.id, kind="article", title="Статья", status="awaiting_approval", content=json.dumps({"full_article":text}))
    db.add(article); db.commit()
    response = client.get(f"/studio/products/{company.id}/materials/{article.id}")
    assert response.status_code == 200
    assert "<h3>Заголовок</h3>" in response.text and "<strong>Важная мысль</strong>" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert element_text(response.text, "material-copy") == text


def test_due_migration_backfills_existing_intents_and_preserves_rows():
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine, text, inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    company, publication = uuid4(), uuid4()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0005_publication_due.py"
    spec = importlib.util.spec_from_file_location("due_migration", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE publications (id CHAR(32) PRIMARY KEY, product_id CHAR(32), channel TEXT, status TEXT)"))
        conn.execute(text("CREATE TABLE artifacts (product_id CHAR(32), kind TEXT, content TEXT)"))
        conn.execute(text("INSERT INTO publications VALUES (:id, :company, 'telegram', 'scheduled')"), {"id":publication.hex, "company":company.hex})
        conn.execute(text("INSERT INTO artifacts VALUES (:company, 'telegram_publish_intent', :content)"),
            {"company":company.hex, "content":json.dumps({"publication_id":str(publication), "scheduled_at":"2026-10-12T09:00:00+00:00"})})
        module.op = Operations(MigrationContext.configure(conn))
        module.upgrade()
        module.upgrade()  # Same fields may exist after metadata-based initial creation.
        when, status = conn.execute(text("SELECT scheduled_at, status FROM publications")).one()
        assert str(when).startswith("2026-10-12 09:00:00") and status == "scheduled"
        assert "ix_publication_due" in {idx["name"] for idx in inspect(conn).get_indexes("publications")}
    engine.dispose()
