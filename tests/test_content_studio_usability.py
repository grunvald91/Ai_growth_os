"""Usability guard-rails for Growth Studio: things a non-technical owner notices first."""
import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.content_studio import MAIL_URL, plural
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Artifact, Product

PAGES = ("", "/create", "/work", "/review", "/materials", "/plan", "/calendar", "/channels", "/analytics")
# Внутренние слова, которые не должны попадать на экран владельцу бизнеса.
JARGON = ("артефакт", "слотов", "слоты", "Идеи и неизвестные", "горизонт плана", "owner workspace")


@pytest.fixture
def studio():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        product = Product(name="ServGo", slug="servgo")
        db.add(product)
        db.flush()
        plan = {"generated_at": "2026-10-14T12:00:00+00:00", "approval_state": "draft", "horizon_days": 14,
                "review_at": "2026-10-28T00:00:00+03:00", "unknowns": ["Вопрос"],
                "slots": [{"package_key": "pkg-1", "topic": "Тема", "platform": "dzen", "format": "article",
                           "publish_at": "2026-10-20T10:00:00+03:00", "prepare_at": "2026-10-19T10:00:00+03:00",
                           "state": "planned", "task_id": None, "audience": "Владельцы", "objective": "Заявки"}]}
        db.add(Artifact(product_id=product.id, kind="editorial_plan", title="План",
                        content=json.dumps(plan, ensure_ascii=False)))
        db.commit()

        def override():
            yield db
        app.dependency_overrides[get_db] = override
        try:
            yield TestClient(app), f"/studio/products/{product.id}"
        finally:
            app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_mail_link_is_on_every_page_and_opens_safely(studio):
    client, base = studio
    assert MAIL_URL == "https://mail.servgo.ru"
    for route in PAGES:
        text = client.get(base + route).text
        links = re.findall(r'<a[^>]*href="https://mail\.servgo\.ru"[^>]*>', text)
        assert links, f"нет ссылки на почту на {route or 'главной'}"
        for tag in links:
            assert 'target="_blank"' in tag and 'rel="noopener noreferrer"' in tag, tag


def test_every_page_has_basic_mobile_and_language_setup(studio):
    client, base = studio
    for route in PAGES:
        text = client.get(base + route).text
        assert '<html lang="ru">' in text, route
        assert 'name="viewport"' in text and "width=device-width" in text, route
        assert len(re.findall(r"<h1[ >]", text)) >= 1, f"нет заголовка h1: {route}"


def test_pages_do_not_show_internal_jargon(studio):
    client, base = studio
    for route in PAGES:
        visible = re.sub(r"<(script|style)[\s\S]*?</\1>", "", client.get(base + route).text)
        visible = re.sub(r"<[^>]+>", " ", visible)
        for word in JARGON:
            assert word not in visible, f"«{word}» на странице {route or 'главная'}"


def test_dropdown_cannot_leave_the_screen_and_tap_targets_are_large(studio):
    client, base = studio
    css = client.get("/studio/assets/studio.css").text
    # меню «Другие разделы» привязано к левому краю и на телефоне раскрывается в потоке страницы
    assert ".sg-inline-more>div{left:0;right:auto" in css
    assert ".sg-inline-more>div{position:static" in css
    # фильтры не растягиваются и не режут слова
    assert ".tab{flex:0 0 auto;white-space:nowrap" in css
    # кнопки и ссылки не меньше 44 px
    assert "min-height:44px" in css


def test_state_changing_errors_are_readable_not_raw_json(studio):
    client, base = studio
    plan_page = client.get(base + "/plan")
    assert plan_page.status_code == 200
    assert "Что нужно уточнить" in plan_page.text


def test_russian_plural_forms():
    assert [plural(n, "публикация", "публикации", "публикаций") for n in (1, 2, 5, 11, 21, 22, 25)] == [
        "публикация", "публикации", "публикаций", "публикаций", "публикация", "публикации", "публикаций"]
