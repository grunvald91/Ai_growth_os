"""2GIS company search: reading its pages, stopping when refused, loading results into a campaign."""
import asyncio
import json
import os

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import httpx
import pytest

from web.api.routes import mail_company_search as search
from web.api.routes import owner_mail
from web.api.routes.mail_campaign_api import CampaignInput, create_campaign
from web.api.routes.mail_campaign_store import initialize as init_campaigns


def page(state):
    """A page shaped like the real one: the data sits in `initialState = JSON.parse('...')`."""
    raw = json.dumps(state, ensure_ascii=False).replace("\\", "\\\\").replace("'", "\\'")
    return "<html><script>var __x = 1; var initialState = JSON.parse('" + raw + "');</script></html>"


def profile(firm_id, name, rubrics=("Салоны красоты",), contacts=None, rating=4.8):
    data = {"id": firm_id, "name": name, "address_name": "улица Роз, 1", "reviews": {"general_rating": rating},
            "rubrics": [{"name": r, "kind": "primary"} for r in rubrics]}
    if contacts is not None:
        data["contact_groups"] = [{"contacts": contacts}]
    return {"data": data, "meta": {"code": 200}}


def search_page(ids, firms, current=1, pages=1, total=None):
    return page({"data": {
        "entity": {"profile": {i: profile(i, firms[i]) for i in ids}},
        "search": {"profile": {"q": {"data": {"pages": pages, "total": total or len(ids), "currentPage": current}}},
                   "pagination": {"q": {str(current): {"data": list(ids), "meta": {"code": 200}}}}}}})


def firm_page(firm_id, name, contacts, rubrics=("Салоны красоты",)):
    return page({"data": {"entity": {"profile": {firm_id: profile(firm_id, name, rubrics, contacts)}}}})


CONTACTS_FULL = [
    {"type": "phone", "value": "+78620000001", "text": "+7 (862) 000-00-01"},
    {"type": "website", "url": "http://www.mint-studio.ru/booking?utm_source=2gis&utm_medium=cpc&a=1",
     "value": "http://link.2gis.ru/1.2/x?http://www.mint-studio.ru/"},
    {"type": "email", "value": "Hello@Mint-Studio.ru", "text": "hello@mint-studio.ru"},
    {"type": "telegram", "value": "https://t.me/mint_studio"},
    {"type": "whatsapp", "value": "https://wa.me/78620000001"},
]


def test_search_page_gives_ids_names_and_page_count():
    html = search_page(["1", "2"], {"1": "Мята", "2": "Лотос"}, pages=24, total=285)
    found = search.parse_search_page(html, 1)
    assert found.ids == ["1", "2"] and found.pages == 24 and found.total == 285
    assert found.firms["1"] == {"name": "Мята", "address": "улица Роз, 1", "rubrics": ["Салоны красоты"], "rating": 4.8}


def test_firm_page_gives_contacts_with_a_clean_https_site():
    card = search.parse_firm_page(firm_page("1", "Мята", CONTACTS_FULL), "1")
    assert card["phones"] == ["+78620000001"]
    assert card["email"] == "hello@mint-studio.ru"
    assert card["website"] == "https://www.mint-studio.ru/booking?a=1"
    assert card["telegram"] == "https://t.me/mint_studio" and card["whatsapp"] == "https://wa.me/78620000001"
    assert search.parse_firm_page(firm_page("1", "Мята", []), "1")["email"] == ""
    assert search.parse_firm_page(firm_page("1", "Мята", []), "other") is None


def test_page_without_data_is_treated_as_a_refusal():
    with pytest.raises(search.Blocked):
        search.parse_search_page("<html>Проверка, что вы не робот</html>", 1)
    with pytest.raises(search.Blocked):
        search.parse_firm_page("<html></html>", "1")


def test_email_from_site_prefers_mailto_and_reads_the_whole_page():
    assert search.email_from_site({"mailto": ["Sales@Mint.ru"], "full_text": "x@y.ru"}) == "sales@mint.ru"
    assert search.email_from_site({"mailto": ["bad"], "full_text": "в подвале: Info@Mint.ru"}) == "info@mint.ru"
    assert search.email_from_site({"excerpt": "a@b.ru"}) == "a@b.ru"


def test_only_numeric_firm_ids_are_followed():
    html = search_page(["1", "../admin", "2"], {"1": "Мята", "../admin": "x", "2": "Лотос"})
    assert search.parse_search_page(html, 1).ids == ["1", "2"]


def test_email_found_in_site_text_and_city_names():
    assert search.email_from_site_text("Пишите: Info@Salon.ru, тел. 123") == "info@salon.ru"
    assert search.email_from_site_text("logo@2x.png и ничего больше") == ""
    assert search.city_slug("Сочи") == "sochi" and search.city_slug("moscow") == "moscow"
    with pytest.raises(search.HTTPException):
        search.city_slug("Неизвестный город")
    assert search.search_url("sochi", "салон красоты", 3) == "https://2gis.ru/sochi/search/%D1%81%D0%B0%D0%BB%D0%BE%D0%BD%20%D0%BA%D1%80%D0%B0%D1%81%D0%BE%D1%82%D1%8B/page/3"


class FakeClient:
    """Serves prepared pages by URL. An exception value is raised, like a dropped connection."""
    routes: dict = {}
    asked: list = []

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        FakeClient.asked.append(url)
        answer = FakeClient.routes.get(url, (404, ""))
        if isinstance(answer, Exception):
            raise answer
        return type("R", (), {"status_code": answer[0], "text": answer[1]})()


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "mail"
    data.mkdir(mode=0o700)
    monkeypatch.setenv("SERVGO_MAIL_DATA_DIR", str(data))
    monkeypatch.setenv("SERVGO_SEARCH_PAUSE_SECONDS", "0")
    monkeypatch.setattr(search.httpx, "AsyncClient", FakeClient)
    FakeClient.routes, FakeClient.asked = {}, []

    prompts = []

    async def fake_hub(prompt):
        prompts.append(prompt)
        found = prompt.split("Найденная организация:")[-1]
        return {"fit": "Мята" in found or "Лотос" in found, "reason": "запись на услугу"}

    monkeypatch.setattr(search, "hub", fake_hub)
    monkeypatch.setattr(search, "research_public_site",
                        lambda url, *args: {"excerpt": "", "full_text": "Пишите на booking@lotos.ru", "mailto": []})
    FakeClient.prompts = prompts
    return FakeClient


def new_job(want=None):
    with owner_mail.db_open() as db:
        search.initialize(db)
        db.execute("INSERT INTO search_job (id,niche,city,want,status,created_at,updated_at) VALUES "
                   "('j1','салон','sochi',?,'running','t','t')", (want,))


def serve_two_firms(client):
    client.routes[search.search_url("sochi", "салон", 1)] = (200, search_page(
        ["1", "2", "3"], {"1": "Мята", "2": "Лотос", "3": "Магазин шин"}))
    client.routes[search.firm_url("sochi", "1")] = (200, firm_page("1", "Мята", CONTACTS_FULL))
    client.routes[search.firm_url("sochi", "2")] = (200, firm_page(
        "2", "Лотос", [{"type": "website", "url": "https://lotos.ru/"}]))
    client.routes[search.firm_url("sochi", "3")] = (200, firm_page(
        "3", "Магазин шин", [{"type": "phone", "value": "+78620000009"}], rubrics=("Шины",)))


def test_search_collects_cards_finds_email_on_site_and_marks_fit(env):
    serve_two_firms(env)
    new_job()
    asyncio.run(search.run_job("j1", "салон", "sochi", None))
    with owner_mail.db_open() as db:
        view = search._view(db, "j1")
    assert view["job"]["status"] == "done"
    by_name = {h["name"]: h for h in view["hits"]}
    assert by_name["Мята"]["email"] == "hello@mint-studio.ru" and by_name["Мята"]["email_source"] == "2ГИС"
    assert by_name["Лотос"]["email"] == "booking@lotos.ru" and by_name["Лотос"]["email_source"] == "сайт"
    assert by_name["Магазин шин"]["email"] == "" and by_name["Магазин шин"]["fit"] is None
    assert view["counts"] == {"found": 3, "with_email": 2, "fit": 2, "ready": 2}


def test_search_stops_when_enough_companies_with_email_are_found(env):
    serve_two_firms(env)
    new_job(want=1)
    asyncio.run(search.run_job("j1", "салон", "sochi", 1))
    with owner_mail.db_open() as db:
        view = search._view(db, "j1")
    assert view["job"]["status"] == "done" and len(view["hits"]) == 1
    assert search.firm_url("sochi", "2") not in env.asked


def test_refusal_stops_the_search_keeps_results_and_does_not_retry(env):
    serve_two_firms(env)
    env.routes[search.firm_url("sochi", "2")] = httpx.ReadError("connection reset")
    new_job()
    asyncio.run(search.run_job("j1", "салон", "sochi", None))
    with owner_mail.db_open() as db:
        view = search._view(db, "j1")
    assert view["job"]["status"] == "blocked" and "оборвал" in view["job"]["message"]
    assert [h["name"] for h in view["hits"]] == ["Мята"]
    assert env.asked.count(search.firm_url("sochi", "2")) == 1
    assert search.firm_url("sochi", "3") not in env.asked


def test_http_429_is_a_refusal_too(env):
    env.routes[search.search_url("sochi", "салон", 1)] = (429, "")
    new_job()
    asyncio.run(search.run_job("j1", "салон", "sochi", None))
    with owner_mail.db_open() as db:
        assert search._view(db, "j1")["job"]["status"] == "blocked"


def test_second_page_is_read_when_there_are_more_pages(env):
    env.routes[search.search_url("sochi", "салон", 1)] = (200, search_page(["1"], {"1": "Мята"}, pages=2, total=2))
    env.routes[search.search_url("sochi", "салон", 2)] = (200, search_page(["2"], {"2": "Лотос"}, current=2, pages=2, total=2))
    env.routes[search.firm_url("sochi", "1")] = (200, firm_page("1", "Мята", CONTACTS_FULL))
    env.routes[search.firm_url("sochi", "2")] = (200, firm_page("2", "Лотос", [{"type": "email", "value": "a@lotos.ru"}]))
    new_job()
    asyncio.run(search.run_job("j1", "салон", "sochi", None))
    with owner_mail.db_open() as db:
        view = search._view(db, "j1")
    assert view["job"]["status"] == "done" and view["job"]["pages_total"] == 2
    assert {h["name"] for h in view["hits"]} == {"Мята", "Лотос"}


def test_fit_companies_with_email_go_into_a_campaign_once(env):
    serve_two_firms(env)
    new_job()
    asyncio.run(search.run_job("j1", "салон", "sochi", None))
    with owner_mail.db_open() as db:
        init_campaigns(db)
    campaign_id = create_campaign(CampaignInput(name="Салоны Сочи"), None)["id"]
    first = search.to_campaign("j1", search.ToCampaign(campaign_id=campaign_id), None)
    assert first["selected"] == 2 and first["added"] == 2
    again = search.to_campaign("j1", search.ToCampaign(campaign_id=campaign_id), None)
    assert again["selected"] == 0
    with owner_mail.db_open() as db:
        rows = db.execute("SELECT email,company,website,notes,status FROM campaign_recipient ORDER BY email").fetchall()
    assert [r["email"] for r in rows] == ["booking@lotos.ru", "hello@mint-studio.ru"]
    mint = next(r for r in rows if r["company"] == "Мята")
    assert mint["website"] == "https://www.mint-studio.ru/booking?a=1"
    assert "2ГИС" in mint["notes"] and "Салоны красоты" in mint["notes"] and "+78620000001" in mint["notes"]


def test_a_search_that_died_with_the_service_is_shown_as_interrupted(env):
    new_job()
    with owner_mail.db_open() as db:
        view = search._view(db, "j1")
    assert view["job"]["status"] == "interrupted"


BRIEF = {"id": "11111111-2222-3333-4444-555555555555", "name": "Мята", "about": "Студия маникюра в Сочи",
         "geography": ["Сочи"], "ideal_customer": "Отели рядом", "not_our_customers": ["Оптовики"],
         "booked_services": [{"service": "Маникюр", "bookings": 12}], "profile_source": "company-profile.json"}


def test_pause_between_requests_is_five_seconds_by_default(env, monkeypatch):
    monkeypatch.delenv("SERVGO_SEARCH_PAUSE_SECONDS", raising=False)
    pauses = []

    async def sleep(seconds):
        pauses.append(seconds)

    monkeypatch.setattr(search.asyncio, "sleep", sleep)
    serve_two_firms(env)
    new_job()
    asyncio.run(search.run_job("j1", "салон", "sochi", None))
    assert pauses and set(pauses) == {5.0}


def test_found_companies_are_judged_against_the_chosen_company(env):
    serve_two_firms(env)
    new_job()
    asyncio.run(search.run_job("j1", "салон", "sochi", None, BRIEF))
    judged = [p for p in env.prompts if "Найденная организация" in p]
    assert len(judged) == 2  # avoid paying for LLM calls for leads without email
    assert all("Студия маникюра в Сочи" in p and "Отели рядом" in p and "Маникюр" in p for p in judged)
    assert all("profile_source" not in p for p in judged)


def test_without_a_company_the_search_works_for_servgo(env):
    serve_two_firms(env)
    new_job()
    asyncio.run(search.run_job("j1", "салон", "sochi", None))
    assert all("ServGo" in p for p in env.prompts if "Найденная организация" in p)


def studio(monkeypatch, answers):
    async def fake(path):
        return answers.get(path)
    monkeypatch.setattr(search, "studio_get", fake)


def test_companies_come_from_the_studio(env, monkeypatch):
    studio(monkeypatch, {"/studio/internal/companies": [{"id": BRIEF["id"], "name": "Мята", "slug": "mint"}]})
    assert asyncio.run(search.companies(None)) == [{"id": BRIEF["id"], "name": "Мята"}]
    studio(monkeypatch, {})
    assert asyncio.run(search.companies(None)) == []


def test_suggestions_are_built_from_the_company_brief(env, monkeypatch):
    studio(monkeypatch, {f"/studio/internal/companies/{BRIEF['id']}/brief": BRIEF})
    asked = []

    async def fake_hub(prompt):
        asked.append(prompt)
        return {"city": "Сочи", "queries": [{"query": "гостиница", "why": "гости хотят маникюр"},
                                           {"query": "x"}, "мусор", {"query": "фитнес-клуб"}]}

    monkeypatch.setattr(search, "hub", fake_hub)
    answer = asyncio.run(search.suggest(search.SuggestInput(company_id=BRIEF["id"]), None))
    assert answer["company"] == "Мята" and answer["city"] == "Сочи"
    assert [q["query"] for q in answer["queries"]] == ["гостиница", "фитнес-клуб"]
    assert "Студия маникюра в Сочи" in asked[0] and "Оптовики" in asked[0]


def test_a_company_the_studio_cannot_describe_stops_the_start(env, monkeypatch):
    studio(monkeypatch, {})
    with pytest.raises(search.HTTPException) as caught:
        asyncio.run(search.company_brief(BRIEF["id"]))
    assert caught.value.status_code == 502
    with pytest.raises(search.HTTPException) as bad:
        asyncio.run(search.company_brief("../../etc"))
    assert bad.value.status_code == 422


def job_for(company_id, brief):
    with owner_mail.db_open() as db:
        search.initialize(db)
        db.execute("INSERT INTO search_job (id,niche,city,want,company_id,company_name,company_brief,status,"
                   "created_at,updated_at) VALUES ('j1','салон','sochi',NULL,?,?,?,'running','t','t')",
                   (company_id, brief.get("name", ""), json.dumps(brief, ensure_ascii=False)))


def test_campaign_takes_the_company_and_its_letters_speak_for_it(env):
    serve_two_firms(env)
    job_for(BRIEF["id"], BRIEF)
    asyncio.run(search.run_job("j1", "салон", "sochi", None, BRIEF))
    with owner_mail.db_open() as db:
        init_campaigns(db)
    campaign_id = create_campaign(CampaignInput(name="Для Мяты"), None)["id"]
    assert search.to_campaign("j1", search.ToCampaign(campaign_id=campaign_id), None)["added"] == 2
    from web.api.routes import mail_campaign_research as research
    with owner_mail.db_open() as db:
        stored = db.execute("SELECT company_id,company_brief FROM campaign WHERE id=?", (campaign_id,)).fetchone()
    assert stored["company_id"] == BRIEF["id"]
    sender = research.sender_company(stored["company_brief"])
    assert sender["name"] == "Мята" and "id" not in sender and "profile_source" not in sender


def test_campaign_of_another_company_is_refused(env):
    serve_two_firms(env)
    job_for(BRIEF["id"], BRIEF)
    asyncio.run(search.run_job("j1", "салон", "sochi", None, BRIEF))
    with owner_mail.db_open() as db:
        init_campaigns(db)
    campaign_id = create_campaign(CampaignInput(name="ServGo"), None)["id"]
    with owner_mail.db_open() as db:
        db.execute("UPDATE campaign SET company_id='',company_brief=? WHERE id=?",
                   (json.dumps({"name": "ServGo"}), campaign_id))
    with pytest.raises(search.HTTPException) as caught:
        search.to_campaign("j1", search.ToCampaign(campaign_id=campaign_id), None)
    assert caught.value.status_code == 409 and "ServGo" in caught.value.detail


def test_campaign_with_servgo_addressees_is_not_turned_into_another_companys(env):
    from web.api.routes.mail_campaign_api import RecipientInput, RecipientList, add_recipients
    serve_two_firms(env)
    job_for(BRIEF["id"], BRIEF)
    asyncio.run(search.run_job("j1", "салон", "sochi", None, BRIEF))
    with owner_mail.db_open() as db:
        init_campaigns(db)
    campaign_id = create_campaign(CampaignInput(name="Старая"), None)["id"]
    add_recipients(campaign_id, RecipientList(recipients=[RecipientInput(email="old@servgo-client.ru")]), None)
    with pytest.raises(search.HTTPException) as caught:
        search.to_campaign("j1", search.ToCampaign(campaign_id=campaign_id), None)
    assert caught.value.status_code == 409


def test_campaign_drafts_without_a_company_stay_servgo():
    from web.api.routes import mail_campaign_research as research
    assert research.sender_company("")["name"] == "ServGo"
    assert research.sender_company("not json")["website"] == "https://servgo.ru"


def test_background_draft_is_written_on_behalf_of_the_campaigns_company(env, monkeypatch):
    from web.api.routes import mail_campaign_research as research
    from web.api.routes.mail_campaign_api import RecipientInput, RecipientList, add_recipients
    with owner_mail.db_open() as db:
        init_campaigns(db)
    campaign_id = create_campaign(CampaignInput(name="Для Мяты"), None)["id"]
    with owner_mail.db_open() as db:
        db.execute("UPDATE campaign SET company_id=?,company_brief=? WHERE id=?",
                   (BRIEF["id"], json.dumps(BRIEF, ensure_ascii=False), campaign_id))
    add_recipients(campaign_id, RecipientList(recipients=[RecipientInput(email="hotel@sea.ru", company="Отель Море")]), None)
    with owner_mail.db_open() as db:
        db.execute("UPDATE campaign_recipient SET status='research_queued'")
    prompts = []

    async def fake_hub(prompt):
        prompts.append(prompt)
        if "критик" in prompt.lower():
            return {"critical": False, "reason": ""}
        return {"subject": "Маникюр для гостей", "body": "Здравствуйте! Предлагаем вашим гостям маникюр рядом."}

    monkeypatch.setattr(research, "hub", fake_hub)
    monkeypatch.setattr(research, "research_public_site", lambda url: {"excerpt": "", "status": "Сайт не указан"})
    assert asyncio.run(research.prepare_one()) is True
    assert "Студия маникюра в Сочи" in prompts[0] and "servgo_offer" not in prompts[0]
    with owner_mail.db_open() as db:
        assert db.execute("SELECT status FROM campaign_recipient").fetchone()["status"] == "review"


def test_requested_count_means_suitable_email_leads_not_any_email(env):
    serve_two_firms(env)
    # The first company has a public email but is not a potential customer.
    env.routes[search.firm_url("sochi", "1")] = (
        200, firm_page("1", "Магазин шин", CONTACTS_FULL, rubrics=("Шины",)))
    new_job(want=1)
    asyncio.run(search.run_job("j1", "салон", "sochi", 1))
    with owner_mail.db_open() as db:
        view = search._view(db, "j1")
    assert view["job"]["status"] == "done"
    assert len(view["hits"]) == 2
    assert view["counts"]["ready"] == 1
    assert search.firm_url("sochi", "3") not in env.asked


def test_manual_review_and_selected_import_preserve_unselected_companies(env):
    serve_two_firms(env)
    new_job()
    asyncio.run(search.run_job("j1", "салон", "sochi", None))
    with owner_mail.db_open() as db:
        init_campaigns(db)
    campaign_id = create_campaign(CampaignInput(name="Выбранные"), None)["id"]
    review = search.review_hit("j1", "1", search.HitReview(fit=False), None)
    assert next(x for x in review["hits"] if x["firm_id"] == "1")["fit"] == 0
    result = search.to_campaign("j1", search.ToCampaign(campaign_id=campaign_id, firm_ids=["2"]), None)
    assert result["added"] == 1 and result["selected"] == 1
    with owner_mail.db_open() as db:
        entries = db.execute("SELECT email FROM campaign_recipient").fetchall()
        untouched = db.execute("SELECT imported FROM search_hit WHERE firm_id='1'").fetchone()
        imported = db.execute("SELECT imported FROM search_hit WHERE firm_id='2'").fetchone()
    assert [r["email"] for r in entries] == ["booking@lotos.ru"]
    assert untouched["imported"] == 0 and imported["imported"] == 1


def test_exa_provider_enriches_bounded_websites_without_real_network(env, monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "test-exa-not-real")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    class ExaResponse:
        def raise_for_status(self):
            pass
        def json(self):
            return {"results": [
                {"url": "https://lotos.ru", "title": "Лотос"},
                {"url": "https://lotos.ru/about", "title": "Лотос — о нас"},
                {"url": "https://mint-studio.ru", "title": "Мята"}]}

    class ExaClient:
        def __init__(self, **_):
            self.calls = []
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return None
        async def post(self, url, *, headers, json):
            assert url == "https://api.exa.ai/search"
            assert headers["x-api-key"] == "test-exa-not-real"
            assert json["numResults"] <= 50
            return ExaResponse()

    monkeypatch.setattr(search.httpx, "AsyncClient", ExaClient)
    new_job()
    with owner_mail.db_open() as db:
        db.execute("UPDATE search_job SET provider='exa' WHERE id='j1'")
    asyncio.run(search.run_exa_job("j1", "салон", "Сочи", None))
    with owner_mail.db_open() as db:
        view = search._view(db, "j1")
    assert view["job"]["status"] == "done"
    assert len(view["hits"]) == 2
    assert all(r["source"] == "Exa" for r in view["hits"])
    assert view["counts"]["ready"] == 2


def test_jev_typed_lead_decisions_and_conservative_uncertainty(env, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "mock-openrouter-key")
    monkeypatch.setenv("SERVGO_MAIL_USE_JEV", "true")
    monkeypatch.delenv("SERVGO_MAIL_JEV_FALLBACK_TO_HUB", raising=False)
    ratings = [0.94, 0.51, 0.12]
    requests = []

    class Reply:
        def __init__(self, value):
            self.value = value
        def raise_for_status(self):
            return None
        def json(self):
            return {"answers": {"fit": {"type": "noul", "noul": self.value}}}

    class JevClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return None
        async def post(self, url, **kwargs):
            assert url == "https://openrouter.ai/api/alpha/decisions"
            assert kwargs["headers"]["Authorization"] == "Bearer mock-openrouter-key"
            assert kwargs["json"]["questions"]["fit"]["type"] == "noul"
            requests.append(kwargs["json"])
            return Reply(ratings.pop(0))

    monkeypatch.setattr(search.httpx, "AsyncClient", JevClient)
    candidate = {"name": "Отель", "rubrics": ["Гостиницы"], "address": "Сочи",
                 "rating": None, "website": "https://example.org"}
    outcomes = [asyncio.run(search.judge(candidate, BRIEF)) for _ in range(3)]
    assert [value for value, _ in outcomes] == [1, None, 0]
    assert len(requests) == 3
    assert all("candidate" in x["state"] and "our_company" in x["state"] for x in requests)
    assert not env.prompts, "Jev results must not trigger a generative per-lead judge"


def test_jev_never_runs_by_default_even_if_openrouter_is_configured(env, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "configured-for-other-service")
    monkeypatch.delenv("SERVGO_MAIL_USE_JEV", raising=False)
    monkeypatch.setattr(search.httpx, "AsyncClient", FakeClient)
    value, reason = asyncio.run(search.judge({
        "name": "Мята", "rubrics": ["Салоны красоты"], "address": "Сочи",
        "rating": 4.9, "website": "https://example.org"
    }, BRIEF))
    assert value == 1 and reason
    assert env.prompts and not FakeClient.asked


def test_unrecognised_2gis_payload_is_not_reported_as_empty_success():
    payload = page({"data": {"search": {"profile": {"x": {"data": {"pages": 1, "total": 5}}}}}})
    with pytest.raises(search.Blocked):
        search.parse_search_page(payload, 1)
    payload = page({"data": {"search": {
        "profile": {"x": {"data": {"pages": 1, "total": 5}}},
        "pagination": {"x": {"1": {"data": []}}}
    }}})
    with pytest.raises(search.Blocked):
        search.parse_search_page(payload, 1)


def test_2gis_first_page_404_does_not_look_like_success(env):
    new_job()
    asyncio.run(search.run_job("j1", "салон", "sochi", None))
    with owner_mail.db_open() as db:
        view = search._view(db, "j1")
    assert view["job"]["status"] == "blocked"
    assert "404" in view["job"]["message"]


def test_missing_email_is_kept_without_paying_for_ai(env):
    serve_two_firms(env)
    new_job()
    asyncio.run(search.run_job("j1", "салон", "sochi", None))
    with owner_mail.db_open() as db:
        view = search._view(db, "j1")
    no_email = next(x for x in view["hits"] if x["name"] == "Магазин шин")
    assert no_email["fit"] is None
    assert "Email не найден" in no_email["fit_reason"]
    assert len(env.prompts) == 2
