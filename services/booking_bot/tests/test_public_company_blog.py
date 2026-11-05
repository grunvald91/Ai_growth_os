"""Isolated tests for the public company SEO feed bridge; no outside network."""
import os
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from web.api.routes.client import _company_blog_data


class FakeDatabase:
    def __init__(self, active=True):
        self.active = active

    async def get(self, model, business_id):
        if not self.active or business_id != 64:
            return None
        return SimpleNamespace(id=64, is_active=True)


class StubHttpClient:
    seen_urls = []
    response = None
    failure = None

    def __init__(self, **kwargs):
        self.options = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        self.seen_urls.append(url)
        if self.failure is not None:
            raise self.failure
        return self.response


class CompanyBlogBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        StubHttpClient.seen_urls = []
        StubHttpClient.failure = None
        StubHttpClient.response = SimpleNamespace(
            status_code=200, json=lambda: {"articles": [{"slug": "article"}]})
        self.env = patch.dict(os.environ, {"GROWTH_PUBLIC_BLOG_URL": "https://growth.example.test/"})
        self.client = patch("web.api.routes.client.httpx.AsyncClient", StubHttpClient)
        self.env.start()
        self.client.start()

    def tearDown(self):
        self.client.stop()
        self.env.stop()

    async def test_active_business_gets_only_its_own_published_feed(self):
        result = await _company_blog_data(64, FakeDatabase())
        self.assertEqual(result["articles"][0]["slug"], "article")
        self.assertEqual(StubHttpClient.seen_urls, [
            "https://growth.example.test/public/blog/businesses/64/articles"])
        self.assertEqual((await _company_blog_data(64, FakeDatabase(), "article"))["articles"][0]["slug"], "article")
        self.assertEqual(StubHttpClient.seen_urls[-1],
                         "https://growth.example.test/public/blog/businesses/64/articles/article")

    async def test_invalid_business_or_slug_cannot_fetch_upstream(self):
        for business_id, slug in ((0, None), (65, None), (64, "../admin"), (64, "a?x=y")):
            with self.subTest(business_id=business_id, slug=slug):
                with self.assertRaises(HTTPException) as ctx:
                    await _company_blog_data(business_id, FakeDatabase(), slug)
                self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(StubHttpClient.seen_urls, [])

    async def test_unknown_and_upstream_errors_are_not_exposed_as_articles(self):
        for status, expected in ((404, 404), (500, 502)):
            StubHttpClient.response = SimpleNamespace(status_code=status, json=lambda: {})
            with self.assertRaises(HTTPException) as ctx:
                await _company_blog_data(64, FakeDatabase())
            self.assertEqual(ctx.exception.status_code, expected)
        StubHttpClient.response = SimpleNamespace(status_code=200, json=lambda: [])
        with self.assertRaises(HTTPException) as ctx:
            await _company_blog_data(64, FakeDatabase())
        self.assertEqual(ctx.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
