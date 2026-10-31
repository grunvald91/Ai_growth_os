"""Public SEO HTML renders complete, escaped article content without JS or external calls."""
import os
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import unittest
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree

from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.api.database import get_db
from web.api.routes import seo_pages


ARTICLE = {
    "slug": "kak-uhazhivat-za-volosami",
    "title": "Как ухаживать за волосами <script>alert(1)</script>",
    "description": "Советы по уходу за волосами.",
    "lead": "Уход после окрашивания.",
    "direct_answer": "Не используйте слишком горячую воду.",
    "sections": [{"title": "Правила ухода", "paragraphs": ["Бережный шампунь."],
                  "bullets": ["Защита от солнца"]}],
    "closing": "Записывайтесь на консультацию.",
    "faq": [{"question": "Как часто?", "answer": "По состоянию волос."}],
    "cta": "Запись в салон",
    "published_at": "2026-10-31T12:00:00+00:00",
    "updated_at": "2026-10-31T12:45:00+00:00",
}
FEED = {"business_id": 64, "articles": [ARTICLE]}


class FakeDB:
    def __init__(self, active=True):
        self.active = active

    async def get(self, model, key):
        if key != 64:
            return None
        return SimpleNamespace(id=64, name="Салон <Мария>", address="Сочи",
                               phone="+7 (900) 000-00-00", is_active=self.active)


class PublicSEOHTMLTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        app = FastAPI()
        app.include_router(seo_pages.router)
        async def get_test_db():
            yield self.db
        app.dependency_overrides[get_db] = get_test_db
        self.client = TestClient(app)

        async def fake_data(business_id, db, slug=None):
            if business_id != 64 or (slug is not None and slug != ARTICLE["slug"]):
                from fastapi import HTTPException
                raise HTTPException(404, "No published article")
            return ARTICLE if slug is not None else FEED
        self.feed_patch = patch.object(seo_pages, "_company_blog_data", fake_data)
        self.feed_patch.start()

    def tearDown(self):
        self.feed_patch.stop()
        self.client.close()

    def test_article_html_is_complete_search_readable_and_escaped(self):
        response = self.client.get("/seo-pages/business/64/blog/kak-uhazhivat-za-volosami")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        body = response.text
        self.assertIn('<h2>Правила ухода</h2>', body)
        self.assertIn("Бережный шампунь.", body)
        self.assertIn("Защита от солнца", body)
        self.assertIn("Не используйте слишком горячую воду.", body)
        self.assertIn('href="/book/64"', body)
        self.assertIn('href="/business/64"', body)
        self.assertIn('rel="canonical" href="https://servgo.ru/business/64/blog/kak-uhazhivat-za-volosami"', body)
        self.assertIn('<script type="application/ld+json">', body)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)
        self.assertIn("\\u003cscript>", body)
        self.assertIn('name="robots" content="index,follow"', body)

    def test_index_page_links_only_to_published_articles(self):
        response = self.client.get("/seo-pages/business/64/blog")
        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/business/64/blog/kak-uhazhivat-za-volosami"', response.text)
        self.assertIn("Салон &lt;Мария&gt;", response.text)
        self.assertIn('href="/book/64"', response.text)

    def test_unknown_or_inactive_business_is_not_indexed(self):
        self.assertEqual(self.client.get("/seo-pages/business/65/blog").status_code, 404)
        self.db.active = False
        self.assertEqual(self.client.get("/seo-pages/business/64/blog").status_code, 404)
        self.assertEqual(self.client.get("/seo-pages/business/64/blog/kak-uhazhivat-za-volosami").status_code, 404)

    def test_company_sitemap_uses_public_urls_and_article_lastmod(self):
        response = self.client.get("/seo-pages/business/64/blog/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        root = ElementTree.fromstring(response.content)
        self.assertTrue(root.tag.endswith("urlset"))
        locs = [v.text for v in root.iter() if v.tag.endswith("loc")]
        self.assertEqual(locs, [
            "https://servgo.ru/business/64/blog",
            "https://servgo.ru/business/64/blog/kak-uhazhivat-za-volosami"])
        self.assertIn("<lastmod>2026-10-31</lastmod>", response.text)

    def test_empty_public_blog_is_404_not_an_indexable_thin_page(self):
        async def empty(business_id, db, slug=None):
            return {"articles": []}
        with patch.object(seo_pages, "_company_blog_data", empty):
            self.assertEqual(self.client.get("/seo-pages/business/64/blog").status_code, 404)
            self.assertEqual(self.client.get("/seo-pages/business/64/blog/sitemap.xml").status_code, 404)

    def test_sitemap_index_lists_published_active_companies(self):
        async def ids(db):
            return [64]
        with patch.object(seo_pages, "_published_business_ids", ids):
            response = self.client.get("/seo-pages/sitemap.xml")
            self.assertEqual(response.status_code, 200)
            root = ElementTree.fromstring(response.content)
            self.assertTrue(root.tag.endswith("sitemapindex"))
            locs = [v.text for v in root.iter() if v.tag.endswith("loc")]
            self.assertEqual(locs, ["https://servgo.ru/business/64/blog/sitemap.xml"])


if __name__ == "__main__":
    unittest.main()
