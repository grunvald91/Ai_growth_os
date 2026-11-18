"""Read-only GEO P0 probe for ServGo public pages and crawler treatment.

- No model calls, secrets, publishing, sitemap submission or private URLs.
- Public URL sampling from sitemap; robots and per-user-agent actual GET.
- Strict origin and no redirects prevent crawler from following arbitrary URLs.
- Caller must report real probe region; a GitHub runner's region is UNKNOWN.
- A fake User-Agent tests treatment, NOT the identity or actual access of a bot.
- Passing checks do NOT prove indexing, citation, or click-through.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

ORIGIN = "https://servgo.ru"
AGENTS = ("Mozilla/5.0 (ServGoGEOAudit)", "Googlebot", "YandexBot",
          "Bingbot", "OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "Claude-User")
MAIN_ARTICLE = re.compile(r"^/blog/[a-z0-9]+(?:-[a-z0-9]+)*$")
BUSINESS_ARTICLE = re.compile(r"^/business/[1-9][0-9]*/blog/[a-z0-9]+(?:-[a-z0-9]+)*$")
BUSINESS_SITEMAP = re.compile(r"^/business/[1-9][0-9]*/blog/sitemap\.xml$")


@dataclass(frozen=True)
class PublicResult:
    status: int
    content_type: str
    body: str
    x_robots: str = ""
    location: str = ""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_public(path: str, agent: str) -> PublicResult:
    if not allowed_public_path(path):
        raise ValueError("Refusing nonpublic or noncanonical path")
    req = Request(ORIGIN + path, headers={"User-Agent": agent, "Accept": "text/html,*/*"})
    opener = build_opener(NoRedirect())
    try:
        with opener.open(req, timeout=12) as res:
            return PublicResult(res.status, res.headers.get("Content-Type", ""),
                                res.read(900_000).decode("utf-8", "replace"),
                                res.headers.get("X-Robots-Tag", ""),
                                res.headers.get("Location", ""))
    except HTTPError as exc:
        return PublicResult(exc.code, exc.headers.get("Content-Type", ""),
                            exc.read(8000).decode("utf-8", "replace"),
                            exc.headers.get("X-Robots-Tag", ""),
                            exc.headers.get("Location", ""))
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"HTTP probe unavailable: {type(exc).__name__}") from None


def allowed_public_path(path: str) -> bool:
    if not isinstance(path, str) or len(path) > 250 or not path.startswith("/") or path.startswith("//"):
        return False
    return path in ("/robots.txt", "/blog", "/blog/sitemap.xml",
                    "/business-blog-sitemap.xml") or bool(
        MAIN_ARTICLE.fullmatch(path) or BUSINESS_ARTICLE.fullmatch(path)
        or BUSINESS_SITEMAP.fullmatch(path)
    )


def sitemap_locs(body: str, expected_root: str) -> list[str]:
    if len(body) > 900_000 or "<!DOCTYPE" in body.upper() or "<!ENTITY" in body.upper():
        raise ValueError("Malformed sitemap XML")
    root = ElementTree.fromstring(body)
    if root.tag.rsplit("}", 1)[-1] != expected_root:
        raise ValueError("Wrong sitemap root")
    return [n.text for n in root.iter()
            if n.tag.rsplit("}", 1)[-1] == "loc" and n.text]


def own_path(url: str, path_regex) -> str | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if (parsed.scheme, parsed.netloc, parsed.query, parsed.fragment) != ("https", "servgo.ru", "", ""):
        return None
    return parsed.path if path_regex.fullmatch(parsed.path) else None


class PageMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonical = []
        self.robots = []
        self.main_depth = 0
        self.h1_depth = 0
        self.main_chars = 0
        self.h1_chars = 0
        self.schema_types = []
        self.in_jsonld = False
        self.jsonld = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link" and "canonical" in (a.get("rel") or "").split():
            self.canonical.append(a.get("href"))
        if tag == "meta" and (a.get("name") or "").lower() == "robots":
            self.robots.append(a.get("content") or "")
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self.in_jsonld = True
            self.jsonld = []
        if tag == "main":
            self.main_depth += 1
        if tag == "h1":
            self.h1_depth += 1

    def handle_endtag(self, tag):
        if tag == "script" and self.in_jsonld:
            try:
                data = json.loads("".join(self.jsonld))
                if isinstance(data, dict):
                    self.schema_types.append(data.get("@type"))
            except (ValueError, TypeError):
                self.schema_types.append("invalid_json")
            self.in_jsonld = False
        if tag == "main":
            self.main_depth = max(0, self.main_depth - 1)
        if tag == "h1":
            self.h1_depth = max(0, self.h1_depth - 1)

    def handle_data(self, data):
        if self.main_depth:
            self.main_chars += len(data.strip())
        if self.h1_depth:
            self.h1_chars += len(data.strip())
        if self.in_jsonld:
            self.jsonld.append(data)


def classify_page(path: str, response: PublicResult) -> tuple[str, list[str]]:
    problems = []
    if response.status != 200:
        problems.append("http_" + str(response.status))
    if "text/html" not in response.content_type.lower():
        problems.append("not_html")
    if "noindex" in response.x_robots.lower() or "nosnippet" in response.x_robots.lower():
        problems.append("indexing_header")
    metadata = PageMetadata()
    metadata.feed(response.body[:900_000])
    if metadata.canonical != [ORIGIN + path]:
        problems.append("canonical_mismatch")
    if any("noindex" in v.lower() or "nosnippet" in v.lower() for v in metadata.robots):
        problems.append("indexing_meta")
    if metadata.main_chars < 100 or not metadata.h1_chars:
        problems.append("thin_or_js_shell")
    if MAIN_ARTICLE.fullmatch(path) and "BlogPosting" not in metadata.schema_types:
        problems.append("missing_blogposting_schema")
    if BUSINESS_ARTICLE.fullmatch(path) and "Article" not in metadata.schema_types:
        problems.append("missing_article_schema")
    if "invalid_json" in metadata.schema_types:
        problems.append("invalid_jsonld")
    return ("fail" if problems else "pass", problems)


def audit(get=get_public, *, region: str = "unknown") -> dict:
    """Run bounded public checks. Never claim true bot identity or AI citations."""
    report = {"measured_at": datetime.now(timezone.utc).isoformat(),
              "origin": ORIGIN, "probe_region": region,
              "region_verified": False, "bot_identity_verified": False,
              "search_indexing": "not_measured", "ai_citations": "not_measured",
              "checks": [], "samples": [], "errors": [],
              "coverage": {"main_article": "not_sampled", "business_article": "not_sampled"}}
    def check(path, agent):
        try:
            return get(path, agent)
        except Exception as exc:
            report["errors"].append({"path": path, "agent": agent,
                                     "code": "network_" + type(exc).__name__})
            return None

    robots = check("/robots.txt", AGENTS[0])
    rules = RobotFileParser()
    if robots and robots.status == 200 and "text/plain" in robots.content_type:
        rules.parse(robots.body.splitlines())
    else:
        report["errors"].append({"path": "/robots.txt", "code": "robots_unavailable"})
        return report

    paths = ["/blog"]
    main_map = check("/blog/sitemap.xml", AGENTS[0])
    if main_map and main_map.status == 200 and "xml" in main_map.content_type:
        try:
            urls = sitemap_locs(main_map.body, "urlset")
            articles = [p for v in urls if (p := own_path(v, MAIN_ARTICLE))]
            if articles:
                paths.append(articles[0])
                report["coverage"]["main_article"] = "sampled"
            else:
                report["coverage"]["main_article"] = "not_sampled_no_published_articles"
        except (ValueError, ElementTree.ParseError):
            report["errors"].append({"path": "/blog/sitemap.xml", "code": "invalid_sitemap"})
    else:
        report["errors"].append({"path": "/blog/sitemap.xml", "code": "sitemap_unavailable"})

    index = check("/business-blog-sitemap.xml", AGENTS[0])
    if index and index.status == 200 and "xml" in index.content_type:
        try:
            links = sitemap_locs(index.body, "sitemapindex")
            maps = [p for v in links if (p := own_path(v, BUSINESS_SITEMAP))]
            if links and len(maps) != len(links):
                report["errors"].append({"path": "/business-blog-sitemap.xml",
                                         "code": "invalid_or_foreign_sitemap_url"})
            if maps:
                business_map = check(maps[0], AGENTS[0])
                if business_map and business_map.status == 200:
                    urls = sitemap_locs(business_map.body, "urlset")
                    articles = [p for v in urls if (p := own_path(v, BUSINESS_ARTICLE))]
                    if articles:
                        paths.append(articles[0])
                        report["coverage"]["business_article"] = "sampled"
                    else:
                        report["coverage"]["business_article"] = "not_sampled_no_published_articles"
                else:
                    report["errors"].append({"path": maps[0], "code": "sitemap_unavailable"})
            else:
                report["coverage"]["business_article"] = "not_sampled_no_published_businesses"
        except (ValueError, ElementTree.ParseError):
            report["errors"].append({"path": "/business-blog-sitemap.xml",
                                     "code": "invalid_sitemap"})
    else:
        report["errors"].append({"path": "/business-blog-sitemap.xml",
                                 "code": "sitemap_unavailable"})
    report["samples"] = paths

    for path in paths:
        for agent in AGENTS:
            allowed = rules.can_fetch(agent, ORIGIN + path)
            entry = {"path": path, "agent": agent, "robots_allowed": allowed}
            if not allowed:
                entry.update(result="fail", issues=["robots_disallow"])
            else:
                response = check(path, agent)
                if response is None:
                    entry.update(result="unknown", issues=["network_unavailable"])
                else:
                    status, issues = classify_page(path, response)
                    entry.update(result=status, issues=issues, http=response.status,
                                 content_type=response.content_type.split(";")[0])
            report["checks"].append(entry)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="unknown", help="Vantage label, not automatically geolocated")
    parser.add_argument("--out", help="Write JSON report to this path")
    args = parser.parse_args()
    report = audit(region=args.region)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(output + "\n", encoding="utf-8")
    print(json.dumps({"samples": report["samples"], "coverage": report["coverage"],
                      "checks": len(report["checks"]),
                      "failed": sum(row["result"] == "fail" for row in report["checks"]),
                      "unknown": sum(row["result"] == "unknown" for row in report["checks"]),
                      "error_codes": [x["code"] for x in report["errors"]]},
                     ensure_ascii=False))
    # A valid report is still useful on a failed probe; its calling workflow
    # must upload the report even when the check fails.
    raise SystemExit(1 if report["errors"] or
                     any(row["result"] != "pass" for row in report["checks"]) else 0)


if __name__ == "__main__":
    main()
