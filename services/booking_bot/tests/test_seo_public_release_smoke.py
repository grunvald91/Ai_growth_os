"""Offline-only regression for the production HTTP SEO smoke contract."""
import unittest

from scripts.seo_public_release_smoke import PublicResponse, verify

ROOT = "https://servgo.ru"
BID = 64
SLUG = "how-to-book"
ARTICLE = f"{ROOT}/business/{BID}/blog/{SLUG}"
SITEMAP = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    f'<sitemap><loc>{ROOT}/business/{BID}/blog/sitemap.xml</loc></sitemap>'
    '</sitemapindex>'
)
BUSINESS_MAP = (
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    f'<url><loc>{ARTICLE}</loc></url></urlset>'
)
HTML = (
    '<!doctype html><html lang="ru"><head>'
    f'<link rel="canonical" href="{ARTICLE}">'
    '<meta name="robots" content="index,follow,max-snippet:-1">'
    '<script type="application/ld+json">'
    f'{{"@type":"Article","url":"{ARTICLE}"}}'
    '</script></head><body><main><h1>Как записаться</h1>'
    '<p>Это полезное руководство по онлайн-записи клиентов, '
    'которое содержит достаточное описание процесса работы сервиса, '
    'примеры и пошаговые инструкции, а также ответы на частые вопросы.</p>'
    '<a href="/book/64">Записаться</a></main></body></html>'
)


def responses():
    return {
        "/robots.txt": PublicResponse(
            200, "text/plain",
            "User-agent: *\nAllow: /\nSitemap: https://servgo.ru/business-blog-sitemap.xml\n"),
        "/business-blog-sitemap.xml": PublicResponse(200, "application/xml", SITEMAP),
        f"/business/{BID}/blog/sitemap.xml": PublicResponse(200, "application/xml", BUSINESS_MAP),
        f"/business/{BID}/blog/{SLUG}": PublicResponse(200, "text/html; charset=utf-8", HTML),
        f"/business/{BID}/blog/__invalid_slug__": PublicResponse(404, "application/json", "{}"),
    }


class SeoPublicSmokeTests(unittest.TestCase):
    def test_read_only_discovery_and_complete_article(self):
        data = responses()
        self.assertEqual(verify(data.__getitem__)["search_indexing"], "not_measured")
        result = verify(data.__getitem__, business_id=BID, slug=SLUG)
        self.assertEqual(result["article"], "served_and_indexable_in_html")
        self.assertEqual(result["invalid_article"], "404")

    def test_robots_blocks_search_bot(self):
        data = responses()
        data["/robots.txt"] = PublicResponse(200, "text/plain",
            "User-agent: OAI-SearchBot\nDisallow: /\n"
            "User-agent: *\nAllow: /\n"
            "Sitemap: https://servgo.ru/business-blog-sitemap.xml\n")
        with self.assertRaisesRegex(RuntimeError, "OAI-SearchBot"):
            verify(data.__getitem__)

    def test_spa_or_wrong_canonical_fails(self):
        data = responses()
        data[f"/business/{BID}/blog/{SLUG}"] = PublicResponse(
            200, "text/html", HTML.replace(ARTICLE, ROOT + "/incorrect"))
        with self.assertRaisesRegex(RuntimeError, "Canonical"):
            verify(data.__getitem__, business_id=BID, slug=SLUG)

    def test_unknown_slug_must_return_real_404(self):
        data = responses()
        data[f"/business/{BID}/blog/__invalid_slug__"] = PublicResponse(
            200, "text/html", "<html>SPA fallback</html>")
        with self.assertRaisesRegex(RuntimeError, "Unknown article"):
            verify(data.__getitem__, business_id=BID, slug=SLUG)

    def test_sitemap_rejects_foreign_domain(self):
        data = responses()
        data["/business-blog-sitemap.xml"] = PublicResponse(
            200, "application/xml", SITEMAP.replace(ROOT, "https://elsewhere.example"))
        with self.assertRaisesRegex(RuntimeError, "unexpected URL"):
            verify(data.__getitem__)


if __name__ == "__main__":
    unittest.main()
