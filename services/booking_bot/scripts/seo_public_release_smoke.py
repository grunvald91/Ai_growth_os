"""Read-only public SEO smoke after a coordinated ServGo release.

No credentials, writes, sitemap submissions, model calls, or publication.
Run from a network with DNS/HTTPS access after the nginx/API rollout:
    python scripts/seo_public_release_smoke.py
    python scripts/seo_public_release_smoke.py --business-id 123 --slug real-published-slug
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from html.parser import HTMLParser
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

ORIGIN = "https://servgo.ru"
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SITEMAP_LOCATION = re.compile(
    r"^https://servgo\.ru/business/[1-9][0-9]*/blog/sitemap\.xml$"
)


@dataclass
class PublicResponse:
    status: int
    content_type: str
    body: str
    x_robots: str = ""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(path: str) -> PublicResponse:
    """Only send GETs to canonical URLs, never follow external redirects."""
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("Path must be a local absolute path")
    request = Request(ORIGIN + path, headers={"User-Agent": "ServGoSEOReleaseSmoke/1.0"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=12) as response:
            return PublicResponse(
                response.status, response.headers.get("Content-Type", ""),
                response.read(2_000_000).decode("utf-8", "replace"),
                response.headers.get("X-Robots-Tag", ""),
            )
    except HTTPError as exc:
        return PublicResponse(exc.code, exc.headers.get("Content-Type", ""),
                              exc.read(4000).decode("utf-8", "replace"),
                              exc.headers.get("X-Robots-Tag", ""))
    except URLError as exc:
        raise RuntimeError("Public HTTPS/DNS endpoint unavailable") from exc


class ArticleMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = False
        self.h1 = False
        self.main = False
        self.main_chars = []
        self.h1_chars = []
        self.canonical = []
        self.robots = []
        self.json_ld = []
        self._jsonld = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonical.append(attrs.get("href", ""))
        if tag == "meta" and attrs.get("name", "").lower() == "robots":
            self.robots.append(attrs.get("content", ""))
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self._jsonld = True
        if tag == "main":
            self.main = True
        if tag == "h1":
            self.h1 = True

    def handle_endtag(self, tag):
        if tag == "script":
            self._jsonld = False
        if tag == "main":
            self.main = False
        if tag == "h1":
            self.h1 = False

    def handle_data(self, data):
        if self.main:
            self.main_chars.append(data)
        if self.h1:
            self.h1_chars.append(data)
        if self._jsonld:
            self.json_ld.append(data)


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def xml_locs(body: str, *, expected_root: str) -> list[str]:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise RuntimeError("Malformed public sitemap XML") from exc
    require(root.tag.rsplit("}", 1)[-1] == expected_root, "Wrong sitemap XML root")
    return [n.text for n in root.iter() if n.tag.rsplit("}", 1)[-1] == "loc" and n.text]


def verify(get=fetch, *, business_id: int | None = None, slug: str | None = None) -> dict:
    """Verify exact public paths without making claims about actual indexing."""
    require((business_id is None) == (slug is None),
            "Use --business-id and --slug together")
    robots = get("/robots.txt")
    require(robots.status == 200 and "text/plain" in robots.content_type,
            "robots.txt not publicly served as text")
    require("business-blog-sitemap.xml" in robots.body,
            "Company sitemap missing from robots.txt")
    rules = RobotFileParser()
    rules.parse(robots.body.splitlines())
    sample = (f"/business/{business_id}/blog/{slug}" if business_id is not None
              else "/business/123/blog/example")
    for bot in ("Googlebot", "YandexBot", "Bingbot", "OAI-SearchBot"):
        require(rules.can_fetch(bot, ORIGIN + sample),
                f"robots.txt disallows public articles for {bot}")

    sitemap = get("/business-blog-sitemap.xml")
    require(sitemap.status == 200 and "xml" in sitemap.content_type,
            "Public company sitemap index is not XML HTTP 200")
    company_maps = xml_locs(sitemap.body, expected_root="sitemapindex")
    require(all(SITEMAP_LOCATION.fullmatch(loc) for loc in company_maps),
            "Sitemap index contains an unexpected URL")

    report = {"robots": "ok", "company_sitemap": "ok",
              "company_sitemaps_listed": len(company_maps),
              "search_indexing": "not_measured"}
    if business_id is None:
        return report
    require(business_id > 0 and bool(SLUG.fullmatch(slug or "")),
            "Business ID or slug invalid")
    map_url = f"{ORIGIN}/business/{business_id}/blog/sitemap.xml"
    require(map_url in company_maps,
            "Known company is absent from public sitemap index")
    business_map = get(f"/business/{business_id}/blog/sitemap.xml")
    require(business_map.status == 200 and "xml" in business_map.content_type,
            "Company sitemap not served")
    pages = xml_locs(business_map.body, expected_root="urlset")
    expected = f"{ORIGIN}/business/{business_id}/blog/{slug}"
    require(expected in pages, "Published article missing from company sitemap")

    article = get(f"/business/{business_id}/blog/{slug}")
    require(article.status == 200 and "text/html" in article.content_type,
            "Article is not server-rendered HTML 200")
    require("noindex" not in article.x_robots.lower(),
            "HTTP headers block article indexing")
    meta = ArticleMetadata()
    meta.feed(article.body)
    require(meta.canonical == [expected], "Canonical URL missing or incorrect")
    require(meta.robots and all("noindex" not in x.lower() for x in meta.robots),
            "HTML robots directives disallow indexing")
    require(bool("".join(meta.h1_chars).strip()) and len("".join(meta.main_chars).strip()) > 100,
            "SEO endpoint returned an empty SPA shell or thin article")
    try:
        schemas = [json.loads(raw) for raw in meta.json_ld]
    except ValueError as exc:
        raise RuntimeError("Invalid Article structured data") from exc
    require(any(s.get("@type") == "Article" and s.get("url") == expected
                for s in schemas if isinstance(s, dict)),
            "Article JSON-LD missing or mismatched")
    missing = get(f"/business/{business_id}/blog/__invalid_slug__")
    require(missing.status == 404,
            "Unknown article returns 200 (possible SPA fallback)")
    report["article"] = "served_and_indexable_in_html"
    report["invalid_article"] = "404"
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-id", type=int)
    parser.add_argument("--slug")
    args = parser.parse_args()
    print(json.dumps(verify(business_id=args.business_id, slug=args.slug),
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
