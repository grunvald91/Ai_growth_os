"""Блог на servgo.ru: статьи платформы, FAQ-разметка, llms.txt, карта сайта, редирект."""
import os
os.environ.setdefault('BOT_TOKEN', '123:test')
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')

import json
import re
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from webapp import blog_feed
from webapp.server import create_webapp

RAW = {
    "slug": "kak-vybrat-crm", "title": "Как выбрать CRM для салона",
    "description": "Короткий разбор выбора CRM.", "category": "Сравнение и выбор",
    "lead": "Выбор CRM начинается с процессов салона.",
    "direct_answer": "Выбирайте CRM по тому, как у вас устроена запись.",
    "sections": [{"title": "С чего начать", "paragraphs": ["Опишите процесс."], "bullets": ["Запись", "Напоминания"]}],
    "closing": "Начните с малого.",
    "faq": [{"question": "Нужна ли CRM салону?", "answer": "Нужна, если записей больше десятка в день и есть повторные клиенты."}],
    "cta": "Подключить запись", "published_at": "2026-10-28T09:00:00+00:00", "updated_at": "2026-10-28T10:00:00+00:00",
}


async def fake_feed(*a, **k):
    article = blog_feed.normalize(RAW)
    return [article]


class NormalizeTest(unittest.TestCase):
    def test_valid_item(self):
        a = blog_feed.normalize(RAW)
        self.assertEqual(a["updated"], "2026-10-28")
        self.assertEqual(a["date_label"], "28 октября 2026")
        self.assertTrue(a["from_feed"])

    def test_verified_company_facts_pass_through_without_html(self):
        raw = {**RAW, "company_facts": [
            "Собственная база клиентов.", "<script>alert(1)</script>", "", 17]}
        article = blog_feed.normalize(raw)
        self.assertEqual(article["company_facts"], [
            "Собственная база клиентов.", "<script>alert(1)</script>"])

    def test_ordered_source_blocks_keep_interleaved_paragraphs(self):
        raw = {**RAW, "sections": [{
            "title": "Как выбрать",
            "paragraphs": ["Начало", "Конец"],
            "bullets": ["Шаг один", "Шаг два"],
            "blocks": [
                {"type": "paragraph", "text": "Начало"},
                {"type": "list", "ordered": True, "items": ["Шаг один", "Шаг два"]},
                {"type": "paragraph", "text": "Конец"},
            ],
        }]}
        article = blog_feed.normalize(raw)
        self.assertEqual(article["sections"][0]["blocks"][1]["items"], ["Шаг один", "Шаг два"])
        self.assertTrue(article["sections"][0]["blocks"][1]["ordered"])

    def test_safe_rich_blocks_normalized(self):
        raw = {**RAW, "sections": [{
            "title": "Правила",
            "blocks": [
                {"type": "subheading", "text": "Уточните длительность"},
                {"type": "paragraph", "text": "**Важное** условие",
                 "spans": [{"text": "Важное", "style": "strong"},
                           {"text": " условие", "style": "text"}]},
                {"type": "quote", "text": "*Рекомендация*",
                 "spans": [{"text": "Рекомендация", "style": "em"}]},
                {"type": "list", "ordered": False,
                 "items": ["**Расписание**", "Клиентская база"],
                 "item_spans": [[{"text": "Расписание", "style": "strong"}],
                                [{"text": "Клиентская база", "style": "text"}]]},
            ],
        }]}
        blocks = blog_feed.normalize(raw)["sections"][0]["blocks"]
        self.assertEqual([b["type"] for b in blocks],
                         ["subheading", "paragraph", "quote", "list"])
        self.assertEqual(blocks[1]["spans"][0]["style"], "strong")
        self.assertEqual(blocks[-1]["item_spans"][0][0]["text"], "Расписание")

    def test_external_html_style_is_not_accepted(self):
        raw = {**RAW, "sections": [{
            "title": "Проверка",
            "blocks": [{"type": "paragraph", "text": "Простой текст",
                        "spans": [{"text": "Простой текст", "style": "raw_html"}]}],
        }]}
        block = blog_feed.normalize(raw)["sections"][0]["blocks"][0]
        self.assertNotIn("spans", block)

    def test_broken_items_dropped(self):
        self.assertIsNone(blog_feed.normalize({**RAW, "slug": "../x"}))
        self.assertIsNone(blog_feed.normalize({**RAW, "sections": []}))
        self.assertIsNone(blog_feed.normalize({"slug": "a"}))


class BlogFeedPagesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patch = patch('webapp.blog_feed.feed_articles', fake_feed)
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()

    async def test_article_has_answer_faq_and_jsonld(self):
        async with TestClient(TestServer(create_webapp())) as c:
            res = await c.get('/blog/kak-vybrat-crm')
            html = await res.text()
        self.assertEqual(res.status, 200)
        self.assertIn('Выбирайте CRM по тому', html)
        self.assertIn('Частые вопросы', html)
        types = [json.loads(m)["@type"] for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
        self.assertEqual(types, ["BlogPosting", "FAQPage"])
        self.assertIn('rel="canonical" href="https://servgo.ru/blog/kak-vybrat-crm"', html)

    async def test_live_article_renders_verified_facts_as_escaped_text(self):
        raw = {**RAW, "company_facts": [
            "Подтверждённая возможность", "<script>alert(1)</script>"]}
        async def with_facts(*a, **kw):
            return [blog_feed.normalize(raw)]
        with patch("webapp.blog_feed.feed_articles", with_facts):
            async with TestClient(TestServer(create_webapp())) as c:
                html = await (await c.get("/blog/kak-vybrat-crm")).text()
        self.assertIn("Подтверждённая возможность", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)

    async def test_rendered_seo_list_stays_between_paragraphs(self):
        raw = {**RAW, "sections": [{
            "title": "Проверка",
            "paragraphs": ["До списка", "После списка"],
            "bullets": ["Первый шаг", "Второй шаг"],
            "blocks": [
                {"type": "paragraph", "text": "До списка"},
                {"type": "list", "ordered": True, "items": ["Первый шаг", "Второй шаг"]},
                {"type": "paragraph", "text": "После списка"},
            ],
        }]}
        async def blocked_feed(*a, **kw):
            return [blog_feed.normalize(raw)]
        with patch('webapp.blog_feed.feed_articles', blocked_feed):
            async with TestClient(TestServer(create_webapp())) as c:
                html = await (await c.get('/blog/kak-vybrat-crm')).text()
        self.assertIn('<ol class="blog-ordered-list">', html)
        self.assertLess(html.index('До списка'), html.index('Первый шаг'))
        self.assertLess(html.index('Первый шаг'), html.index('После списка'))
        self.assertEqual(html.count('Первый шаг'), 1)

    async def test_rich_emphasis_and_heading_render_with_html_escaped(self):
        raw = {**RAW, "sections": [{
            "title": "Структура записи",
            "blocks": [
                {"type": "subheading", "text": "Сначала правила"},
                {"type": "paragraph", "text": "**<script>alert(1)</script>** и *проверка*",
                 "spans": [{"text": "<script>alert(1)</script>", "style": "strong"},
                           {"text": " и ", "style": "text"},
                           {"text": "проверка", "style": "em"}]},
                {"type": "quote", "text": "Сохраняйте историю",
                 "spans": [{"text": "Сохраняйте историю", "style": "strong"}]},
            ],
        }]}
        async def formatted_feed(*a, **kw):
            return [blog_feed.normalize(raw)]
        with patch("webapp.blog_feed.feed_articles", formatted_feed):
            async with TestClient(TestServer(create_webapp())) as c:
                html = await (await c.get("/blog/kak-vybrat-crm")).text()
        self.assertIn('<h3 class="blog-subheading">Сначала правила</h3>', html)
        self.assertIn("<strong>&lt;script&gt;alert(1)&lt;/script&gt;</strong>", html)
        self.assertIn("<em>проверка</em>", html)
        self.assertIn('<blockquote class="blog-editorial-note">', html)
        self.assertNotIn("<script>alert(1)</script>", html)

    async def test_list_sitemap_and_llms(self):
        async with TestClient(TestServer(create_webapp())) as c:
            self.assertIn('kak-vybrat-crm', await (await c.get('/blog')).text())
            self.assertIn('https://servgo.ru/blog/kak-vybrat-crm', await (await c.get('/blog/sitemap.xml')).text())
            llms = await (await c.get('/llms.txt')).text()
            robots = await (await c.get('/robots.txt')).text()
        self.assertIn('[Как выбрать CRM для салона](https://servgo.ru/blog/kak-vybrat-crm)', llms)
        self.assertIn('https://servgo.ru/blog/sitemap.xml', robots)

    async def test_builtin_articles_keep_working_when_feed_down(self):
        self.patch.stop()
        blog_feed.reset_cache()
        with patch('webapp.blog_feed.GROWTH_FEED_BASE', 'https://feed.test/articles'), \
                patch('webapp.blog_feed._download', side_effect=OSError) as download:
            async with TestClient(TestServer(create_webapp())) as c:
                res = await c.get('/blog')
        self.patch.start()
        self.assertEqual(res.status, 200)
        self.assertEqual(download.call_count, 1)

    async def test_old_consumer_url_redirects_after_owner_guide_goes_live(self):
        raw = {**RAW,
               "slug": "kak-psihologu-organizovat-zapis-klientov",
               "title": "Как психологу организовать запись клиентов"}
        async def owner_feed(*a, **kw):
            return [blog_feed.normalize(raw)]
        with patch("webapp.blog_feed.feed_articles", owner_feed):
            async with TestClient(TestServer(create_webapp())) as c:
                res = await c.get("/blog/zapis-k-psihologu", allow_redirects=False)
                new = await c.get("/blog/kak-psihologu-organizovat-zapis-klientov")
        self.assertEqual(res.status, 301)
        self.assertEqual(res.headers["Location"],
                         "https://servgo.ru/blog/kak-psihologu-organizovat-zapis-klientov")
        self.assertEqual(new.status, 200)

    async def test_no_redirect_before_new_page_exists(self):
        raw = {**RAW, "slug": "zapis-k-psihologu"}
        async def still_old_feed(*a, **kw):
            return [blog_feed.normalize(raw)]
        with patch("webapp.blog_feed.feed_articles", still_old_feed):
            async with TestClient(TestServer(create_webapp())) as c:
                res = await c.get("/blog/zapis-k-psihologu", allow_redirects=False)
        self.assertEqual(res.status, 200)

    async def test_unknown_slug_404(self):
        async with TestClient(TestServer(create_webapp())) as c:
            self.assertEqual((await c.get('/blog/net-takoj')).status, 404)

    async def test_old_promo_host_redirects(self):
        async with TestClient(TestServer(create_webapp())) as c:
            res = await c.get('/blog/kak-vybrat-crm', headers={'Host': 'promo.servgo.ru'}, allow_redirects=False)
        self.assertEqual(res.status, 301)
        self.assertEqual(res.headers['Location'], 'https://servgo.ru/blog/kak-vybrat-crm')


class IncrementalFeedTest(unittest.IsolatedAsyncioTestCase):
    """Полный текст скачивается только для новых или изменённых статей."""

    async def test_unchanged_articles_are_not_downloaded_again(self):
        calls = []

        class Resp:
            def __init__(self, body):
                self.body, self.status = body, 200
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            def raise_for_status(self):
                pass
            async def json(self):
                return self.body

        class Session:
            def get(self, url):
                calls.append(url)
                if url.endswith('/articles'):
                    return Resp({"articles": [{"slug": RAW["slug"], "updated_at": RAW["updated_at"]}]})
                return Resp(RAW)

        blog_feed.reset_cache()
        with patch('webapp.blog_feed.GROWTH_FEED_BASE', 'https://feed.test/articles'):
            first = await blog_feed._download(Session())
            second = await blog_feed._download(Session())
        blog_feed.reset_cache()
        self.assertEqual([a["slug"] for a in second], [RAW["slug"]])
        self.assertEqual(len(first), 1)
        self.assertEqual(sum(1 for u in calls if u.endswith(RAW["slug"])), 1)


if __name__ == '__main__':
    unittest.main()
