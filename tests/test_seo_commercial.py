"""Commercial SEO funnel: verified anonymous clicks, consent and received leads.

No network, Telegram or production database access.
"""
import os
os.environ.setdefault("BOT_TOKEN", "123:test")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import ast
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import create_async_engine

from database.models import MarketingLead
from webapp import seo_commercial
from webapp.server import validate_landing_request_payload


def _blog_payload(url, *, consent=True, name="Заявитель"):
    params = parse_qs(urlparse(url).query)
    return {
        "name": name, "email": "owner@example.org",
        "consent": consent,
        "utm_source": params["utm_source"][0],
        "utm_medium": params["utm_medium"][0],
        "utm_campaign": params["utm_campaign"][0],
        "utm_content": params["utm_content"][0],
        "utm_term": params.get("utm_term", [""])[0],
        "seo_module": params.get("seo_module", ["platform"])[0],
        "requested_module": "shifts",
    }


class SeoCommercialUnitTest(unittest.TestCase):
    def test_seo_contact_requires_consent_and_keeps_module_separate(self):
        url = ("https://servgo.ru/?utm_source=servgo_blog&utm_medium=seo"
               "&utm_campaign=seo_booking&utm_content=kak-vybrat-crm"
               "&utm_term=" + "a" * 32 + "&seo_module=booking")
        with self.assertRaisesRegex(ValueError, "согласие"):
            validate_landing_request_payload(_blog_payload(url, consent=False))
        data = validate_landing_request_payload(_blog_payload(url))
        self.assertEqual(data["utm_content"], "kak-vybrat-crm")
        self.assertEqual(data["seo_module"], "booking")
        self.assertEqual(data["requested_module"], "shifts")
        self.assertTrue(data["consent"])

    def test_legacy_business_lead_is_not_forcibly_rejected(self):
        data = validate_landing_request_payload({"name": "Компания", "email": "x@y.ru"})
        self.assertNotEqual(data.get("utm_source"), "servgo_blog")

    def test_public_url_does_not_accept_unsafe_slug(self):
        self.assertEqual(seo_commercial.article_cta_url("valid-slug"), "/blog/cta/valid-slug")
        self.assertEqual(seo_commercial.article_cta_url("../evil"), "/#launch")


class SeoCommercialRoutesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.app = web.Application()
        async def user(request):
            return {"id": 42} if request.headers.get("X-Test-Admin") == "yes" else None
        seo_commercial.register_seo_commercial_routes(
            self.app, engine=self.engine, get_user=user, admin_ids=[42],
            public_origin="https://servgo.ru")
        # Existing public dispatcher proxies the private report on production.
        async def report_proxy(request):
            return await self.app["seo_report_handler"](request)
        self.app.router.add_post("/api/landing/connect-request", report_proxy)
        async def listed(_):
            return [{"slug": "kak-vybrat-crm"}, {"slug": "onlayn-zapis"}]
        self.patch = patch("webapp.blog_feed.all_articles", listed)
        self.patch.start()
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        async with self.engine.begin() as conn:
            await conn.run_sync(MarketingLead.__table__.create, checkfirst=True)

    async def asyncTearDown(self):
        await self.client.close()
        self.patch.stop()
        await self.engine.dispose()

    async def _click(self, slug="kak-vybrat-crm", module="booking"):
        response = await self.client.get(f"/blog/cta/{slug}?module={module}",
                                         allow_redirects=False)
        return response

    async def _report(self, admin=True):
        return await self.client.post("/api/landing/connect-request?seo=report",
            headers={"X-Test-Admin": "yes"} if admin else {})

    async def test_real_article_clicked_and_token_is_verified_without_pii(self):
        response = await self._click()
        self.assertEqual(response.status, 303)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        self.assertIn("noindex", response.headers.get("X-Robots-Tag", ""))
        url = response.headers["Location"]
        self.assertTrue(url.startswith("https://servgo.ru/?utm_source=servgo_blog"))
        self.assertTrue(url.endswith("#launch"))
        self.assertNotIn("evil.example", url)
        payload = _blog_payload(url)
        self.assertEqual(payload["utm_campaign"], "seo_booking")
        self.assertTrue(await seo_commercial.verify_click(
            self.engine, payload["utm_term"], "kak-vybrat-crm", "booking"))
        self.assertFalse(await seo_commercial.verify_click(
            self.engine, payload["utm_term"], "onlayn-zapis", "booking"))
        self.assertFalse(await seo_commercial.verify_click(
            self.engine, "a" * 32, "kak-vybrat-crm", "booking"))

        public = await self._report(admin=False)
        self.assertEqual(public.status, 403)
        admin = await self._report()
        data = await admin.json()
        self.assertEqual(data["total_cta_clicks"], 1)
        self.assertEqual(data["total_accepted_leads"], 0)
        self.assertEqual(data["articles"][0]["article_slug"], "kak-vybrat-crm")
        self.assertEqual(data["articles"][0]["module"], "booking")
        self.assertNotIn("name", str(data))
        self.assertNotIn("email", str(data))
        self.assertIn("no-store", admin.headers["Cache-Control"])

    async def test_only_verified_delivered_leads_count_as_conversions(self):
        response = await self._click(module="shifts")
        payload = validate_landing_request_payload(_blog_payload(response.headers["Location"]))
        self.assertTrue(await seo_commercial.verify_click(
            self.engine, payload["utm_term"], payload["utm_content"], "shifts"))
        async with self.engine.begin() as conn:
            await conn.execute(insert(MarketingLead).values(
                name="Секретное имя", email="private@example.org",
                utm_source="servgo_blog", utm_medium="seo",
                utm_campaign="seo_shifts", utm_content=payload["utm_content"],
                utm_term=payload["utm_term"], attribution_confidence="verified",
                status="pending",
            ))
        first = await (await self._report()).json()
        self.assertEqual(first["total_accepted_leads"], 0)
        async with self.engine.begin() as conn:
            await conn.execute(update(MarketingLead).values(status="received"))
        second = await (await self._report()).json()
        self.assertEqual(second["total_accepted_leads"], 1)
        self.assertEqual(second["total_cta_clicks"], 1)
        self.assertNotIn("Секретное", str(second))
        self.assertNotIn("private@example.org", str(second))

    async def test_unpublished_slug_and_invalid_module_do_not_record_click(self):
        self.assertEqual((await self._click(slug="not-published")).status, 404)
        self.assertEqual((await self._click(module="javascript:alert")).status, 404)
        report = await (await self._report()).json()
        self.assertEqual(report["total_cta_clicks"], 0)

    async def test_click_storage_failure_does_not_fabricate_attribution(self):
        self.app["seo_click_table_ready"] = False
        res = await self._click()
        self.assertEqual(res.status, 303)
        self.assertNotIn("utm_term=", res.headers["Location"])
        self.assertEqual((await self._report()).status, 503)


class SeoCommercialWiringTest(unittest.TestCase):
    def test_live_blog_template_uses_server_verified_cta_not_llm_url(self):
        template = (Path(__file__).resolve().parents[1] /
                    "webapp/templates/blog.html").read_text(encoding="utf-8")
        self.assertIn('href="{{ commercial_cta_url or', template)
        self.assertNotIn("article.cta_href or", template)
        for family in ("booking", "growth", "mail", "shifts", "seo"):
            self.assertIn(f"?module={family}", template)

    def test_server_dispatch_wires_real_owner_only_report(self):
        source = (Path(__file__).resolve().parents[1] /
                  "webapp/server.py").read_text(encoding="utf-8")
        self.assertIn('request.query.get("seo") == "report"', source)
        self.assertIn('register_seo_commercial_routes(', source)
        self.assertIn('if is_seo_lead and not tracking_saved:', source)
        self.assertIn('await mark_received_seo_lead(lead_id)', source)

    def test_landing_form_requires_contact_consent_and_keeps_source(self):
        tmpl = (Path(__file__).resolve().parents[1] /
                "webapp/templates/landing.html").read_text(encoding="utf-8")
        script = (Path(__file__).resolve().parents[1] /
                  "webapp/static/js/landing.js").read_text(encoding="utf-8")
        self.assertIn('type="checkbox" name="consent"', tmpl)
        self.assertIn('name="seo_module"', tmpl)
        self.assertIn('readGrowthAttribution()', script)
        self.assertIn('requested_module:', script)
        self.assertIn('utm_term:', script)


if __name__ == "__main__":
    unittest.main()
