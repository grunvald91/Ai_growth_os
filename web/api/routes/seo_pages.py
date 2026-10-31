"""Server-rendered, read-only HTML for ServGo company blogs and SEO sitemaps.

This router is mounted under /api/seo-pages in BookingBot. The public
/business/{id}/blog paths require a narrow nginx reverse-proxy mapping during
the future deployment; don't mistake API HTML for a live production route.
"""
from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Business, get_db
from ..seo_referrals import article_booking_link
from .client import _company_blog_data

router = APIRouter(prefix="/seo-pages", tags=["public-seo"], include_in_schema=False)
ORIGIN = "https://servgo.ru"
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PAGE_HEADERS = {
    "Cache-Control": "public, max-age=60",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; "
        "img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'"
    ),
}
XML_HEADERS = {"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"}
CSS = """
:root{color-scheme:light;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;
 color:#17283b;background:#f5f7fb}
*{box-sizing:border-box}body{margin:0}a{color:#2268b2}
.wrap{width:min(790px,100%);margin:auto;padding:28px 18px 100px}
nav{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:20px}
.brand{font-weight:800;color:#2268b2;text-decoration:none;font-size:20px}
.nav-link{font-weight:600;text-decoration:none}
main{background:#fff;border:1px solid #e6eaf1;border-radius:22px;padding:26px;
 box-shadow:0 6px 24px #182e4b0b}
h1{font-size:clamp(26px,5vw,38px);line-height:1.2;margin:10px 0 18px}
h2{font-size:22px;line-height:1.35;margin:30px 0 12px}
h3{font-size:18px}p,li{font-size:16px;line-height:1.75}
.lead{font-size:18px;color:#4d5d70}.muted{color:#617185}
.meta{font-size:14px;color:#667589;margin:0 0 16px}
.article-list{padding:0;list-style:none}.article-list li{border-top:1px solid #e6eaf1;padding:18px 0}
.article-list a{font-size:19px;font-weight:700;text-decoration:none}
.cta{background:#f0f5fe;padding:22px;border-radius:15px;margin-top:28px}
.actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:15px}
.btn{display:inline-block;background:#2268b2;color:#fff;border-radius:12px;
 padding:12px 19px;text-decoration:none;font-weight:700}
.btn.secondary{background:#fff;color:#2268b2;border:1px solid #c7d6ed}
.contact{border-top:1px solid #e6eaf1;margin-top:30px;padding-top:15px}
@media(max-width:560px){.wrap{padding:14px 10px 80px}main{padding:18px;border-radius:16px}}
"""


def _text(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _full_url(path: str) -> str:
    return ORIGIN + path


def _schema(data: dict) -> str:
    # JSON-LD is inert data, but escaping '<' avoids any HTML script break-out.
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def _html_page(*, title: str, description: str, path: str, body: str,
               structured: dict | None = None) -> HTMLResponse:
    canonical = _full_url(path)
    page = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{_text(title)}</title>'
        f'<meta name="description" content="{_text(description)}">'
        '<meta name="robots" content="index,follow,max-snippet:-1,max-image-preview:large">'
        f'<link rel="canonical" href="{_text(canonical)}">'
        f'<meta property="og:title" content="{_text(title)}">'
        f'<meta property="og:description" content="{_text(description)}">'
        f'<meta property="og:url" content="{_text(canonical)}">'
        '<meta property="og:type" content="article">'
        f'<style>{CSS}</style>'
    )
    if structured:
        page += f'<script type="application/ld+json">{_schema(structured)}</script>'
    page += (
        '</head><body><div class="wrap"><nav>'
        '<a class="brand" href="/">ServGo</a>'
        '<a class="nav-link" href="/">Компании</a>'
        '</nav><main>' + body + '</main></div></body></html>'
    )
    return HTMLResponse(page, headers=PAGE_HEADERS)


async def _active_business(db: AsyncSession, business_id: int) -> Business:
    if business_id <= 0:
        raise HTTPException(404, "Company not found")
    business = await db.get(Business, business_id)
    if business is None or not business.is_active:
        raise HTTPException(404, "Company not found")
    return business


def _links(business: Business, *, links: dict | None = None) -> str:
    home = (links or {}).get("company_url") or f"/business/{business.id}"
    booking = (links or {}).get("cta_url") or f"/book/{business.id}"
    return (
        '<section class="cta"><h2>Услуги и запись</h2>'
        f'<p>Вы можете ознакомиться с услугами {_text(business.name)} и выбрать удобное время.</p>'
        '<div class="actions">'
        f'<a class="btn" href="{_text(booking)}">Записаться</a>'
        f'<a class="btn secondary" href="{_text(home)}">О компании</a>'
        '</div></section>'
    )


def _contacts(business: Business) -> str:
    items: list[str] = []
    if business.address:
        items.append('<p>Адрес: ' + _text(business.address) + '</p>')
    if business.phone:
        phone = re.sub(r"[^\d+]", "", business.phone)
        if re.fullmatch(r"\+?\d{5,16}", phone):
            items.append(f'<p>Телефон: <a href="tel:{_text(phone)}">{_text(business.phone)}</a></p>')
        else:
            items.append('<p>Телефон: ' + _text(business.phone) + '</p>')
    return '<footer class="contact">' + "".join(items) + '</footer>' if items else ""


def _paragraphs(block: dict) -> str:
    output = []
    for part in block.get("paragraphs", []):
        if isinstance(part, str) and part.strip():
            output.append(f"<p>{_text(part)}</p>")
    bullets = block.get("bullets", [])
    if isinstance(bullets, list) and bullets:
        output.append("<ul>" + "".join(f"<li>{_text(item)}</li>" for item in bullets) + "</ul>")
    return "".join(output)


def _article_body(article: dict, business: Business, *, links: dict | None = None) -> str:
    output = [
        f'<p class="meta">{_text(business.name)} · Статьи и советы</p>',
        f'<h1>{_text(article["title"])}</h1>',
    ]
    published = article.get("published_at")
    if published:
        output.append(f'<p class="meta">Опубликовано: <time datetime="{_text(published)}">'
                      f'{_text(str(published)[:10])}</time></p>')
    if article.get("lead"):
        output.append(f'<p class="lead">{_text(article["lead"])}</p>')
    if article.get("direct_answer"):
        output.append(f'<p>{_text(article["direct_answer"])}</p>')
    for section in article.get("sections", []):
        if isinstance(section, dict):
            output.append(f'<section><h2>{_text(section.get("title"))}</h2>{_paragraphs(section)}</section>')
    company_facts = article.get("company_facts") or []
    if isinstance(company_facts, list) and company_facts:
        paragraphs = "".join(f"<p>{_text(fact)}</p>" for fact in company_facts
                             if isinstance(fact, str) and fact.strip())
        if paragraphs:
            output.append(f'<section><h2>О {_text(business.name)}</h2>{paragraphs}</section>')
    faq = article.get("faq") or []
    if faq:
        output.append('<section><h2>Частые вопросы</h2>')
        for item in faq:
            if isinstance(item, dict):
                output.append(f'<h3>{_text(item.get("question"))}</h3><p>{_text(item.get("answer"))}</p>')
        output.append("</section>")
    if article.get("closing"):
        output.append(f'<p>{_text(article["closing"])}</p>')
    output.append(_links(business, links=links))
    output.append(_contacts(business))
    output.append(f'<p><a href="/business/{business.id}/blog">Все статьи компании</a></p>')
    return "".join(output)


@router.get("/business/{business_id}/blog", response_class=HTMLResponse)
async def business_blog_html(business_id: int, db: AsyncSession = Depends(get_db)):
    business = await _active_business(db, business_id)
    feed = await _company_blog_data(business_id, db)
    articles = feed.get("articles")
    if not isinstance(articles, list) or not articles:
        # Do not publish empty/thin index pages as if they were SEO destinations.
        raise HTTPException(404, "No published articles")
    items = []
    for article in articles:
        if not isinstance(article, dict) or not SLUG.fullmatch(str(article.get("slug", ""))):
            continue
        items.append(
            f'<li><a href="/business/{business_id}/blog/{_text(article["slug"])}">'
            f'{_text(article.get("title"))}</a>'
            f'<p class="muted">{_text(article.get("description") or article.get("lead"))}</p></li>'
        )
    if not items:
        raise HTTPException(404, "No published articles")
    body = (
        f'<p class="meta"><a href="/business/{business_id}">{_text(business.name)}</a></p>'
        f'<h1>Статьи и советы — {_text(business.name)}</h1>'
        f'<p class="lead">Полезные материалы компании {_text(business.name)}.</p>'
        '<ul class="article-list">' + "".join(items) + "</ul>"
        + _links(business) + _contacts(business)
    )
    return _html_page(title=f"Статьи и советы — {business.name} | ServGo",
                      description=f"Полезные статьи и советы компании {business.name}.",
                      path=f"/business/{business_id}/blog", body=body)


@router.get("/business/{business_id}/blog/sitemap.xml")
async def business_article_sitemap(business_id: int, db: AsyncSession = Depends(get_db)):
    await _active_business(db, business_id)
    feed = await _company_blog_data(business_id, db)
    articles = feed.get("articles") if isinstance(feed, dict) else None
    if not isinstance(articles, list) or not articles:
        raise HTTPException(404, "No published articles")

    def lastmod(raw: object) -> str:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return ""

    urls = [f'<url><loc>{_full_url(f"/business/{business_id}/blog")}</loc></url>']
    for article in articles:
        if not isinstance(article, dict) or not SLUG.fullmatch(str(article.get("slug", ""))):
            continue
        loc = _full_url(f'/business/{business_id}/blog/{article["slug"]}')
        date = lastmod(article.get("updated_at") or article.get("published_at"))
        urls.append(f'<url><loc>{xml_escape(loc)}</loc>'
                    + (f'<lastmod>{date}</lastmod>' if date else "") + "</url>")
    if len(urls) == 1:
        raise HTTPException(404, "No published articles")
    xml = '<?xml version="1.0" encoding="UTF-8"?>' + (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(urls) + "</urlset>"
    )
    return Response(xml, media_type="application/xml", headers=XML_HEADERS)


@router.get("/business/{business_id}/blog/{slug}", response_class=HTMLResponse)
async def business_article_html(business_id: int, slug: str, db: AsyncSession = Depends(get_db)):
    business = await _active_business(db, business_id)
    if not SLUG.fullmatch(slug):
        raise HTTPException(404, "Article not found")
    article = await _company_blog_data(business_id, db, slug)
    if not isinstance(article.get("title"), str) or not isinstance(article.get("sections"), list):
        raise HTTPException(502, "Article unavailable")
    path = f"/business/{business_id}/blog/{slug}"
    meta = str(article.get("description") or article.get("lead") or article["title"])
    data = {
        "@context": "https://schema.org",
        "@type": "Article",
        "@id": _full_url(path) + "#article",
        "url": _full_url(path),
        "mainEntityOfPage": _full_url(path),
        "inLanguage": "ru",
        "isAccessibleForFree": True,
        "headline": article["title"],
        "description": meta,
        "author": {"@type": "Organization", "name": business.name,
                   "url": _full_url(f"/business/{business_id}")},
        "publisher": {"@type": "Organization", "name": business.name,
                      "url": _full_url(f"/business/{business_id}")},
        "isPartOf": {"@type": "Blog", "url": _full_url(f"/business/{business_id}/blog"),
                     "name": f"Статьи и советы — {business.name}"},
        "datePublished": article.get("published_at"),
        "dateModified": article.get("updated_at") or article.get("published_at"),
    }
    # A genuine collected search question helps define what this page answers.
    # Do not invent expert bylines, review ratings, sources or FAQ schema.
    query = article.get("primary_query")
    if isinstance(query, str) and query.strip():
        data["about"] = {"@type": "Thing", "name": query.strip()}
    booking_links = await article_booking_link(db, business_id, article)
    return _html_page(title=f'{article["title"]} — {business.name} | ServGo',
                      description=meta, path=path,
                      body=_article_body(article, business, links=booking_links), structured=data)


async def _published_business_ids(db: AsyncSession) -> list[int]:
    # Growth decides which businesses have published content; BookingBot checks
    # whether those businesses are active. One upstream call, not one per company.
    url = os.getenv("GROWTH_PUBLIC_BLOG_URL", "https://growth.servgo.ru").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            response = await client.get(url + "/public/blog/businesses/with-articles",
                                        headers={"Accept": "application/json"})
    except httpx.RequestError:
        raise HTTPException(503, "Sitemap feed unavailable") from None
    if response.status_code != 200:
        raise HTTPException(503, "Sitemap feed unavailable")
    try:
        data = response.json()
    except ValueError:
        raise HTTPException(502, "Invalid sitemap source") from None
    raw = data.get("business_ids") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise HTTPException(502, "Invalid sitemap source")
    ids = {value for value in raw if type(value) is int and value > 0}
    if not ids:
        return []
    active = await db.scalars(select(Business.id).where(Business.id.in_(ids), Business.is_active.is_(True)))
    return sorted(active.all())


@router.get("/sitemap.xml")
async def company_blog_sitemap_index(db: AsyncSession = Depends(get_db)):
    ids = await _published_business_ids(db)
    entries = "".join(
        f'<sitemap><loc>{_full_url(f"/business/{business_id}/blog/sitemap.xml")}</loc></sitemap>'
        for business_id in ids
    )
    content = '<?xml version="1.0" encoding="UTF-8"?>' + (
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + entries + "</sitemapindex>"
    )
    return Response(content, media_type="application/xml", headers=XML_HEADERS)

