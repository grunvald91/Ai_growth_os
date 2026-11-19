"""Offline contract tests for GEO P0 public crawler probe."""
import unittest
from unittest.mock import patch

from scripts.geo_public_crawl_audit import (
    AGENTS, ORIGIN, PublicResult, allowed_public_path, audit,
)

MAIN = "/blog/kak-vesti-zapis"
BUSINESS = "/business/64/blog/how-to-book"
INDEX = (
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    '<sitemap><loc>https://servgo.ru/business/64/blog/sitemap.xml</loc></sitemap></sitemapindex>'
)
MAIN_MAP = (
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    '<url><loc>https://servgo.ru/blog</loc></url>'
    f'<url><loc>{ORIGIN}{MAIN}</loc></url></urlset>'
)
BUSINESS_MAP = (
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    f'<url><loc>{ORIGIN}{BUSINESS}</loc></url></urlset>'
)


def html(path, *, kind="BlogPosting", canonical=None):
    canonical = canonical or ORIGIN + path
    return (
        '<html><head>' + f'<link rel="canonical" href="{canonical}">'
        '<meta name="robots" content="index,follow,max-snippet:-1">'
        f'<script type="application/ld+json">{{"@type":"{kind}"}}</script>'
        '</head><body><main><h1>Как работать с клиентами</h1>'
        '<p>Полезная публичная статья содержит понятное пошаговое описание '
        'работы с записью клиентов, конкретные действия и проверяемые факты, '
        'примеры, ограничения и рекомендации для небольшого бизнеса.</p>'
        '</main></body></html>'
    )


def sample_responses():
    return {
        "/robots.txt": PublicResult(200, "text/plain",
                                     "User-agent: *\nAllow: /\nSitemap: https://servgo.ru/blog/sitemap.xml\n"),
        "/blog/sitemap.xml": PublicResult(200, "application/xml", MAIN_MAP),
        "/business-blog-sitemap.xml": PublicResult(200, "application/xml", INDEX),
        "/business/64/blog/sitemap.xml": PublicResult(200, "application/xml", BUSINESS_MAP),
        "/blog": PublicResult(200, "text/html", html("/blog", kind="Blog")),
        MAIN: PublicResult(200, "text/html", html(MAIN)),
        BUSINESS: PublicResult(200, "text/html", html(BUSINESS, kind="Article")),
    }


class GeoPublicCrawlAuditTests(unittest.TestCase):
    def test_network_probe_explicitly_bypasses_host_proxy_variables(self):
        from scripts import geo_public_crawl_audit as probe
        from urllib.request import ProxyHandler
        with patch.object(probe, "build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.headers.get.return_value = "text/html"
            response.read.return_value = b"<html><main>Public blog</main></html>"
            result = probe.get_public("/blog", "Googlebot")
            self.assertEqual(result.status, 200)
            handlers = opener.call_args.args
            self.assertTrue(any(isinstance(x, ProxyHandler) and x.proxies == {}
                                for x in handlers))

    def test_baseline_all_crawlers_get_indexable_ssr_with_distinct_sites(self):
        data = sample_responses()
        calls = []
        def get(path, agent):
            calls.append((path, agent))
            return data[path]
        report = audit(get, region="test-vantage-unknown")
        self.assertEqual(report["samples"], ["/blog", MAIN, BUSINESS])
        self.assertEqual(len(report["checks"]), len(AGENTS) * 3)
        self.assertEqual(report["errors"], [])
        self.assertTrue(all(x["result"] == "pass" for x in report["checks"]))
        self.assertFalse(report["region_verified"])
        self.assertFalse(report["bot_identity_verified"])
        self.assertEqual(report["ai_citations"], "not_measured")
        self.assertNotIn("/account", [path for path, _ in calls])

    def test_no_published_business_sitemaps_is_coverage_gap_not_http_failure(self):
        data = sample_responses()
        data["/business-blog-sitemap.xml"] = PublicResult(
            200, "application/xml",
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>')
        report = audit(lambda path, agent: data[path])
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["coverage"]["business_article"],
                         "not_sampled_no_published_businesses")
        self.assertEqual(report["coverage"]["main_article"], "sampled")
        self.assertEqual(len(report["checks"]), 2 * len(AGENTS))
        self.assertTrue(all(x["result"] == "pass" for x in report["checks"]))

    def test_robots_denial_and_specific_search_user_agent_http_403(self):
        data = sample_responses()
        data["/robots.txt"] = PublicResult(200, "text/plain",
            "User-agent: OAI-SearchBot\nDisallow: /blog/\n"
            "User-agent: *\nAllow: /\n")
        def get(path, agent):
            if path == BUSINESS and agent == "PerplexityBot":
                return PublicResult(403, "text/html", html(path, kind="Article"))
            return data[path]
        report = audit(get)
        failures = [x for x in report["checks"] if x["result"] == "fail"]
        self.assertTrue(any(x["agent"] == "OAI-SearchBot" and
                            "robots_disallow" in x["issues"] for x in failures))
        self.assertTrue(any(x["agent"] == "PerplexityBot" and
                            "http_403" in x["issues"] for x in failures))
        self.assertTrue(all(x["path"] in ["/blog", MAIN, BUSINESS] for x in failures))

    def test_canonical_noindex_and_shell_are_reported(self):
        data = sample_responses()
        data[MAIN] = PublicResult(200, "text/html",
                                  html(MAIN, canonical=ORIGIN+"/wrong"),
                                  "noindex")
        data[BUSINESS] = PublicResult(200, "text/html",
                                     "<html><main>JavaScript SPA</main></html>")
        report = audit(lambda path, agent: data[path])
        main = [x for x in report["checks"] if x["path"] == MAIN]
        business = [x for x in report["checks"] if x["path"] == BUSINESS]
        self.assertTrue(all("canonical_mismatch" in x["issues"] and
                            "indexing_header" in x["issues"] for x in main))
        self.assertTrue(all("thin_or_js_shell" in x["issues"] for x in business))

    def test_unavailable_http_is_unknown_not_fake_zero(self):
        data = sample_responses()
        def get(path, agent):
            if path == MAIN:
                raise OSError("fake network disruption")
            return data[path]
        report = audit(get)
        self.assertTrue(any(x["result"] == "unknown" for x in report["checks"]))
        self.assertEqual(report["search_indexing"], "not_measured")
        self.assertEqual(len(report["errors"]), len(AGENTS))

    def test_never_fetch_nonpublic_or_cross_origin_urls(self):
        for path in ("/studio", "/account", "/admin", "/blog/../admin",
                     "//evil.tld/blog", "/business/64/blog/../../../admin",
                     "/business/64/blog/sitemap.xml?redirect=evil",
                     "/api/seo-pages/business/64/blog"):
            self.assertFalse(allowed_public_path(path), path)
        data = sample_responses()
        data["/business-blog-sitemap.xml"] = PublicResult(
            200, "application/xml",
            INDEX.replace("https://servgo.ru", "https://evil.example"))
        report = audit(lambda path, agent: data[path])
        self.assertNotIn(BUSINESS, report["samples"])
        self.assertTrue(any(x["path"] == "/business-blog-sitemap.xml"
                            for x in report["errors"]))


if __name__ == "__main__":
    unittest.main()
