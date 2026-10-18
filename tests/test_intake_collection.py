"""Stage 1 of richer company intake: site crawl, registry check, Studio files and answers."""
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.intake.contracts import IntakeError
from app.intake.registry import DadataRegistryLookup, registry_identifier
from app.intake.service import CompanyIntakeService, MAX_SOURCES
from app.intake.sources import (
    PublicWebsiteFetcher, _VisibleHTML, looks_script_rendered, page_text, rank_internal_links,
)
from app.llm.fake import FakeLLMProvider
from app.llm.schemas import LLMResponse
from app.llm.service import LLMService
from app.main import app
from app.models.domain import Product, Task
from app.privacy.gateway import PrivacyGateway

HOME = """<html><head><title>Ноготок — студия маникюра в Сочи</title>
<meta name="description" content="Маникюр и педикюр в Сочи, запись онлайн">
<script type="application/ld+json">{"@type":"BeautySalon","name":"Ноготок","priceRange":"от 1500 ₽",
 "address":{"addressLocality":"Сочи","streetAddress":"ул. Навагинская, 9"},"secretToken":"zzz"}</script>
</head><body><nav>
<a href="/uslugi/">Услуги</a><a href="/prices">Цены</a><a href="/kontakty">Контакты</a>
<a href="/blog/post-1">Блог</a><a href="/cart">Корзина</a><a href="https://other.example/">Партнёр</a>
<a href="/files/price.pdf">Прайс PDF</a><a href="tel:+79990000000">Позвонить</a><a href="/uslugi/#top">Наверх</a>
</nav><main><h1>Студия маникюра Ноготок</h1><p>Делаем маникюр, педикюр и наращивание для женщин в Сочи.</p></main>
<script>window.a=1</script></body></html>"""

PAGES = {
    "https://nogotok.example/": HOME,
    "https://nogotok.example/uslugi/": "<html><body><h1>Услуги</h1><p>Маникюр с покрытием — 1500 ₽. Педикюр — 2200 ₽. Наращивание ногтей — 3000 ₽.</p></body></html>",
    "https://nogotok.example/prices": "<html><body><h1>Услуги</h1><p>Маникюр с покрытием — 1500 ₽. Педикюр — 2200 ₽. Наращивание ногтей — 3000 ₽.</p></body></html>",
    "https://nogotok.example/kontakty": "<html><body><h1>Контакты</h1><p>Сочи, ул. Навагинская, 9. Работаем ежедневно с 10 до 21 часа.</p></body></html>",
}


@pytest.fixture
def fake_site(monkeypatch):
    requested = []

    def get(self, url):
        requested.append(url)
        if url not in PAGES:
            raise IntakeError("Страница недоступна (HTTP 404).")
        return url, "text/html; charset=utf-8", PAGES[url]

    monkeypatch.setattr(PublicWebsiteFetcher, "_get", get)
    return requested


def test_internal_links_are_same_site_informative_first_and_without_assets():
    links = [("/blog/post-1", "Блог"), ("/about", "О нас"), ("/prices", "Цены"), ("/cart", "Корзина"),
             ("https://www.nogotok.example/uslugi", "Услуги"), ("https://other.example/prices", "Цены"),
             ("/price.pdf", "Прайс"), ("mailto:a@b.c", "Почта"), ("/prices#x", "Цены"), ("/", "Главная")]
    ranked = rank_internal_links("https://nogotok.example/", links)
    assert ranked[0] == "https://nogotok.example/prices"
    assert "https://www.nogotok.example/uslugi" in ranked and "https://nogotok.example/about" in ranked
    assert all("other.example" not in url and not url.endswith(".pdf") for url in ranked)
    assert "https://nogotok.example/cart" not in ranked and "https://nogotok.example/blog/post-1" not in ranked
    assert ranked.count("https://nogotok.example/prices") == 1


def test_page_text_keeps_title_meta_and_whitelisted_structured_data():
    parser = _VisibleHTML()
    parser.feed(HOME)
    text = page_text(parser)
    assert "Заголовок страницы: Ноготок — студия маникюра в Сочи" in text
    assert "Маникюр и педикюр в Сочи, запись онлайн" in text
    assert "priceRange: от 1500 ₽" in text and "address.addressLocality: Сочи" in text
    assert "secretToken" not in text and "window.a" not in text
    assert "Делаем маникюр, педикюр и наращивание" in text


def test_script_rendered_site_is_flagged():
    parser = _VisibleHTML()
    parser.feed("<html><head><title>App</title></head><body><div id=root></div>"
                "<script src=a.js></script><script src=b.js></script><script src=c.js></script></body></html>")
    assert looks_script_rendered(parser)


@pytest.mark.asyncio
async def test_crawl_reads_home_and_priority_pages_skipping_duplicates_and_failures(fake_site):
    pages = await PublicWebsiteFetcher().crawl("https://nogotok.example/")
    references = [page.reference for page in pages]
    assert references[0] == "https://nogotok.example/"
    # /prices duplicates /uslugi word for word, so only one of them is kept.
    assert len(references) == 3
    assert "https://nogotok.example/kontakty" in references
    assert all(page.source_type == "owned_website" and page.status == "read" for page in pages)
    assert not any("cart" in url or "blog" in url or url.endswith(".pdf") for url in fake_site)


@pytest.mark.asyncio
async def test_crawl_respects_page_limit_and_text_budget(fake_site):
    pages = await PublicWebsiteFetcher().crawl("https://nogotok.example/", max_pages=2)
    assert len(pages) == 2
    small = await PublicWebsiteFetcher().crawl("https://nogotok.example/", text_budget=700)
    assert sum(len(page.text) for page in small) <= 700
    assert len(small) < len(await PublicWebsiteFetcher().crawl("https://nogotok.example/"))


@pytest.mark.asyncio
async def test_unreachable_site_is_one_visible_unavailable_source(monkeypatch):
    def broken(self, url):
        raise OSError("down")
    monkeypatch.setattr(PublicWebsiteFetcher, "_get", broken)
    pages = await PublicWebsiteFetcher().crawl("https://down.example/")
    assert len(pages) == 1 and pages[0].status == "unavailable" and pages[0].note


def _service(db, tmp_path, **kwargs):
    product = Product(name="Ноготок", slug="nogotok")
    db.add(product)
    db.commit()
    provider = FakeLLMProvider(LLMResponse(content='{"claims":[]}', model="kimi-2.7"))
    service = CompanyIntakeService(db, products_root=tmp_path,
                                   llm=LLMService(privacy_gateway=PrivacyGateway(), provider=provider), **kwargs)
    session = UUID(service.start(product.id, actor="owner")["session_id"])
    return product, session, service


@pytest.mark.asyncio
async def test_service_adds_crawled_pages_as_separate_sources_in_one_revision(db, tmp_path, fake_site):
    product, session, service = _service(db, tmp_path)
    state = await service.add_link(product.id, session, "https://nogotok.example/", actor="owner")
    pages = [s for s in state["sources"] if s["source_type"] == "owned_website"]
    assert len(pages) == 3 and state["revision"] == 1
    again = await service.add_link(product.id, session, "https://nogotok.example/", actor="owner")
    assert again["revision"] == 1  # Same pages are deduplicated, no empty revision.
    assert len(state["sources"]) <= MAX_SOURCES


@pytest.mark.asyncio
async def test_owner_answer_is_evidence_for_an_asked_question_only(db, tmp_path):
    product, session, service = _service(db, tmp_path)
    service.add_text(product.id, session, "Студия маникюра в Сочи для женщин.", actor="owner")
    review = await service.build(product.id, session, actor="owner")
    question = review["questions"][0]
    with pytest.raises(IntakeError):
        service.answer_question(product.id, session, "brand.tone_not_asked", "любой", actor="owner")
    with pytest.raises(IntakeError):
        service.answer_question(product.id, session, question["field_path"], "  ", actor="owner")
    state = service.answer_question(product.id, session, question["field_path"], "Ноготок", actor="owner")
    answer = state["sources"][-1]
    assert answer["source_type"] == "owner_message"
    assert question["text"] in answer["text"] and "Ответ владельца: Ноготок" in answer["text"]


def test_registry_identifier_accepts_inn_and_ogrn_only():
    assert registry_identifier("ИНН 7707083893") == "7707083893"
    assert registry_identifier("ОГРН 1027700132195, Сбер") == "1027700132195"
    assert registry_identifier("ИП, ИНН 7707-083-893-12") == "770708389312"
    assert registry_identifier("Ноготок") is None
    assert registry_identifier("12345") is None


def _transport(payload=None, status=200, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=payload if payload is not None else {})
    return httpx.MockTransport(handler)


PARTY = {"suggestions": [{"value": "ООО \"НОГОТОК\"", "data": {
    "type": "LEGAL", "inn": "2320000000", "ogrn": "1222300000000", "okved": "96.02",
    "name": {"full_with_opf": "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ \"НОГОТОК\"", "short_with_opf": "ООО \"НОГОТОК\""},
    "address": {"value": "г Сочи, ул Навагинская, д 9"},
    "state": {"status": "ACTIVE", "registration_date": 1640995200000},
    "management": {"name": "Иванова Анна", "post": "ДИРЕКТОР"}}}]}


@pytest.mark.asyncio
async def test_dadata_lookup_returns_registry_facts_without_leaking_the_key():
    seen = []
    source = await DadataRegistryLookup("secret-key", transport=_transport(PARTY, seen=seen)).lookup("ИНН 2320000000")
    assert source.source_type == "official_registry" and source.status == "read"
    assert "Краткое наименование: ООО \"НОГОТОК\"" in source.text
    assert "Основной ОКВЭД: 96.02" in source.text and "Статус: действует" in source.text
    assert "Дата регистрации: 2022-01-01" in source.text
    assert "secret-key" not in source.text + source.reference and "Иванова" not in source.text
    assert seen[0].headers["Authorization"] == "Token secret-key"
    assert json.loads(seen[0].content)["query"] == "2320000000"


@pytest.mark.asyncio
async def test_dadata_lookup_not_found_name_and_errors():
    missing = await DadataRegistryLookup("k", transport=_transport({"suggestions": []})).lookup("2320000000")
    assert missing.status == "unavailable" and "не найдена" in missing.note
    by_name = await DadataRegistryLookup("k", transport=_transport(PARTY)).lookup("Ноготок")
    assert by_name.status == "unavailable" and "ИНН или ОГРН" in by_name.note
    with pytest.raises(OSError):
        await DadataRegistryLookup("k", transport=_transport(status=403)).lookup("2320000000")


@pytest.mark.asyncio
async def test_service_registry_lookup_adds_official_source_and_survives_outage(db, tmp_path):
    product, session, service = _service(db, tmp_path, registry_lookup=DadataRegistryLookup(
        "k", transport=_transport(PARTY)))
    state = await service.add_identifier(product.id, session, "2320000000", actor="owner")
    registry = [s for s in state["sources"] if s["source_type"] == "official_registry"]
    assert registry and registry[0]["status"] == "read"
    service.registry_lookup = DadataRegistryLookup("k", transport=_transport(status=500))
    state = await service.add_identifier(product.id, session, "1222300000000", actor="owner")
    assert state["sources"][-1]["status"] == "unavailable"


@pytest.fixture
def studio(tmp_path, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "products_root", Path(tmp_path))
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        def override():
            yield db
        app.dependency_overrides[get_db] = override
        with TestClient(app) as client:
            yield client, db
        app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


ABOUT = "Студия маникюра в Сочи: маникюр, педикюр, наращивание. Для женщин 25–45, запись онлайн."


def test_new_company_form_offers_site_inn_and_files(studio):
    client, _ = studio
    page = client.get("/studio/new").text
    assert 'enctype="multipart/form-data"' in page
    assert 'name="identifier"' in page and 'name="files"' in page and 'type="file"' in page


def test_new_company_with_site_and_price_file_collects_every_material(studio, fake_site):
    client, db = studio
    response = client.post("/studio/new",
                           data={"name": "Ноготок", "about": ABOUT, "website": "https://nogotok.example/",
                                 "identifier": "2320000000"},
                           files=[("files", ("price.csv", "Услуга;Цена\nМаникюр;1500\n".encode(), "text/csv"))],
                           follow_redirects=False)
    assert response.status_code == 303
    assert "notice=" in response.headers["location"]
    product = db.scalar(select(Product).where(Product.name == "Ноготок"))
    page = client.get(response.headers["location"]).text
    assert "Прочитано страниц сайта: 3." in page
    assert "price.csv" in page and "https://nogotok.example/kontakty" in page
    assert "Материалы · " in page
    task = db.scalar(select(Task).where(Task.product_id == product.id, Task.assigned_agent == "company_intake"))
    assert task is not None


def test_profile_shows_questions_and_answers_rebuild_the_draft(studio, monkeypatch):
    client, db = studio
    provider = FakeLLMProvider(LLMResponse(content='{"claims":[]}', model="kimi-2.7"))

    def build_intake(db, settings, provider_override=None):
        from app.core.config import get_settings
        return CompanyIntakeService(db, products_root=get_settings().products_root,
                                    llm=LLMService(privacy_gateway=PrivacyGateway(), provider=provider))

    monkeypatch.setattr("app.core.composition.build_intake", build_intake)
    response = client.post("/studio/new", data={"name": "Ноготок", "about": ABOUT}, follow_redirects=False)
    base = response.headers["location"]
    assert client.post(base + "/build", data={"go": "1"}, follow_redirects=False).status_code == 303
    page = client.get(base).text
    assert "Чего не хватает" in page and "Ответить и пересобрать профиль" in page
    assert 'name="q0_field"' in page
    field = page.split('name="q0_field" value="')[1].split('"')[0]
    second = page.split('name="q1_field" value="')[1].split('"')[0]
    result = client.post(base + "/answers", data={"q0_field": field, "q0_answer": "Ноготок",
                                                  "q1_field": second, "q1_unknown": "1"},
                         follow_redirects=False)
    assert result.status_code == 303
    after = client.get(base).text
    assert "ответ владельца:" in after
    assert "Подтвердить профиль" in after  # Rebuilt draft is current again.
    assert len(provider.requests) == 2
    empty = client.post(base + "/answers", data={"q0_field": field}, follow_redirects=False)
    assert empty.status_code == 409


def test_oversized_upload_is_rejected_before_parsing(studio):
    client, _ = studio
    response = client.post("/studio/new", data={"name": "Ноготок", "about": ABOUT},
                           files=[("files", ("big.txt", b"x" * 10, "text/plain"))],
                           headers={"content-length": str(30_000_000)})
    assert response.status_code in {400, 413}
