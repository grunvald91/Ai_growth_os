"""Read-only public feed of published articles. The blog on servgo.ru renders pages from it."""
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.domain import Artifact, ArtifactStatus, Product, Publication
from app.sso.models import StudioBusinessLink
from app.workflows.article_contracts import ArticleArtifact

router = APIRouter(prefix="/public/blog", tags=["public-blog"])
_BULLET = re.compile(r"^\s*(?:(\d{1,2})[.)]|[-*•])\s+(.*\S)\s*$")
_INLINE_ENUM = re.compile(r"(?<![\w.])([1-9]\d?)[.)]\s+(?=\S)")
_INLINE_DASH = re.compile(r"(?<=[:;])\s*[—•]\s+(?=\S)")
_SENTENCES = re.compile(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z«])")
_CATEGORY = {"awareness": "Советы", "consideration": "Сравнение и выбор", "decision": "Запуск"}


_INLINE_MARK = re.compile(r"(\*\*\*[^*\n]+\*\*\*|\*\*[^*\n]+\*\*|\*[^*\n]+\*)")
_SUBHEADING = re.compile(r"^#{3,4}\s+(.+?)\s*$")
_QUOTE = re.compile(r"^>\s+(.+?)\s*$")


def inline_spans(text: str) -> list[dict] | None:
    """Safe formatting tokens. Never return HTML from content controlled by an LLM.

    The consuming template auto-escapes each text span, including HTML-looking
    input; only the template creates <strong>/<em> tags.
    """
    parts = []
    last = 0
    for match in _INLINE_MARK.finditer(text):
        if match.start() > last:
            parts.append({"text": text[last:match.start()], "style": "text"})
        token = match.group()
        if token.startswith("***"):
            style, inner = "strong_em", token[3:-3]
        elif token.startswith("**"):
            style, inner = "strong", token[2:-2]
        else:
            style, inner = "em", token[1:-1]
        parts.append({"text": inner, "style": style})
        last = match.end()
    if not parts:
        return None
    if last < len(text):
        parts.append({"text": text[last:], "style": "text"})
    return parts


def body_blocks(body: str) -> list[dict]:
    """Typed paragraphs, H3 subheadings, quotes and lists with safe inline spans.

    Both old plain-text articles and new Markdown-lite articles use exactly
    the same public API; no raw HTML or trust-based |safe rendering.
    """
    blocks: list[dict] = []

    def paragraph(text: str) -> None:
        text = text.strip()
        if not text:
            return
        sentences = _SENTENCES.split(text) if len(text) > 650 else [text]
        current = ""
        for sentence in sentences:
            if current and len(current) + len(sentence) > 540:
                block = {"type": "paragraph", "text": current}
                spans = inline_spans(current)
                if spans:
                    block["spans"] = spans
                blocks.append(block)
                current = sentence
            else:
                current = (current + " " + sentence).strip()
        if current:
            block = {"type": "paragraph", "text": current}
            spans = inline_spans(current)
            if spans:
                block["spans"] = spans
            blocks.append(block)

    def item(text: str, ordered: bool) -> None:
        if blocks and blocks[-1]["type"] == "list" and blocks[-1]["ordered"] == ordered:
            block = blocks[-1]
            block["items"].append(text)
        else:
            block = {"type": "list", "ordered": ordered, "items": [text]}
            blocks.append(block)
        spans = inline_spans(text)
        if spans or "item_spans" in block:
            if "item_spans" not in block:
                block["item_spans"] = [
                    [{"text": v, "style": "text"}] for v in block["items"][:-1]
                ]
            block["item_spans"].append(spans or [{"text": text, "style": "text"}])

    for raw in body.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        subheading = _SUBHEADING.match(line)
        if subheading:
            blocks.append({"type": "subheading", "text": subheading.group(1)})
            continue
        quote = _QUOTE.match(line)
        if quote:
            block = {"type": "quote", "text": quote.group(1)}
            spans = inline_spans(block["text"])
            if spans:
                block["spans"] = spans
            blocks.append(block)
            continue
        markers = list(_INLINE_ENUM.finditer(line))
        if len(markers) >= 2 and [int(m.group(1)) for m in markers] == list(range(1, len(markers) + 1)):
            paragraph(line[:markers[0].start()])
            for i, marker in enumerate(markers):
                end = markers[i + 1].start() if i + 1 < len(markers) else len(line)
                item(line[marker.end():end].strip(), ordered=True)
            continue
        match = _BULLET.match(line)
        if match:
            item(match.group(2), ordered=match.group(1) is not None)
            continue
        dashes = list(_INLINE_DASH.finditer(line))
        if len(dashes) >= 2:
            paragraph(line[:dashes[0].start()])
            for i, marker in enumerate(dashes):
                end = dashes[i + 1].start() if i + 1 < len(dashes) else len(line)
                item(line[marker.end():end].strip(" ;"), ordered=False)
            continue
        paragraph(line)
    return blocks


def split_body(body: str) -> tuple[list[str], list[str]]:
    """Backward-compatible fields for older blog clients."""
    blocks = body_blocks(body)
    return ([block["text"] for block in blocks if block["type"] == "paragraph"],
            [item for block in blocks if block["type"] == "list" for item in block["items"]])


def _article(row: Artifact, publication: Publication) -> dict:
    article = ArticleArtifact.model_validate_json(row.content)
    reader = article.reader_article
    sections = []
    for section in reader.sections:
        blocks = body_blocks(section.body)
        paragraphs, bullets = split_body(section.body)
        sections.append({"title": section.heading, "blocks": blocks,
                         "paragraphs": paragraphs, "bullets": bullets})
    return {
        "slug": publication.external_reference.removeprefix("/blog/"),
        "title": reader.title,
        "description": reader.meta_description,
        "category": _CATEGORY[article.seo_brief.stage] if article.seo_brief else "Советы",
        "stage": article.seo_brief.stage if article.seo_brief else None,
        "primary_query": article.seo.primary_query,
        "lead": reader.lead,
        "direct_answer": reader.direct_answer or "",
        "sections": sections,
        # Only text rendered from verified facts; no internal source IDs in public HTML.
        "company_facts": [fact.text for fact in reader.fact_renderings],
        "closing": reader.closing,
        "faq": [item.model_dump() for item in reader.faq],
        "cta": article.conversion.cta,
        "published_at": publication.published_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _published_for_product(db: Session, product_id):
    return db.execute(
        select(Artifact, Publication).join(Publication, Publication.artifact_id == Artifact.id)
        .where(Publication.product_id == product_id, Publication.channel == "own_site", Publication.status == "published",
               Artifact.status == ArtifactStatus.PUBLISHED, Artifact.kind == "article")
        .order_by(Publication.published_at.desc())).all()


def _published(db: Session, product_slug: str):
    product = db.scalar(select(Product).where(Product.slug == product_slug))
    if product is None:
        raise HTTPException(404, "Not found")
    return _published_for_product(db, product.id)


def _business_product(db: Session, business_id: int):
    """Use the verified ServGo/Studio link, never a guessed slug or matching company name."""
    if business_id <= 0:
        raise HTTPException(404, "Not found")
    link = db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == business_id))
    if link is None:
        raise HTTPException(404, "Not found")
    return link.product_id


def _business_urls(business_id: int) -> dict:
    return {"company_url": f"/business/{business_id}", "booking_url": f"/book/{business_id}"}


@router.get("/businesses/with-articles")
def businesses_with_articles(db: Session = Depends(get_db)) -> dict:
    """Public sitemap discovery: only company IDs with at least one published article."""
    ids = db.scalars(
        select(StudioBusinessLink.servgo_business_id)
        .join(Publication, Publication.product_id == StudioBusinessLink.product_id)
        .join(Artifact, Artifact.id == Publication.artifact_id)
        .where(Publication.channel == "own_site", Publication.status == "published",
               Artifact.status == ArtifactStatus.PUBLISHED, Artifact.kind == "article")
        .distinct().order_by(StudioBusinessLink.servgo_business_id)
    ).all()
    return {"business_ids": ids}


@router.get("/businesses/{business_id}/articles")
def list_business_articles(business_id: int, db: Session = Depends(get_db)) -> dict:
    """Published articles for a ServGo business, ready for its existing public profile."""
    product_id = _business_product(db, business_id)
    articles = []
    for row, publication in _published_for_product(db, product_id):
        full = _article(row, publication)
        articles.append({k: full[k] for k in
                         ("slug", "title", "description", "category", "stage", "lead", "published_at", "updated_at")})
    return {"business_id": business_id, **_business_urls(business_id), "articles": articles}


@router.get("/businesses/{business_id}/articles/{slug}")
def get_business_article(business_id: int, slug: str, db: Session = Depends(get_db)) -> dict:
    """Article CTA falls back to that company's actual booking route, not a fabricated service."""
    product_id = _business_product(db, business_id)
    for row, publication in _published_for_product(db, product_id):
        if publication.external_reference == f"/blog/{slug}":
            return {**_article(row, publication), **_business_urls(business_id),
                    "cta_url": f"/book/{business_id}"}
    raise HTTPException(404, "Not found")


@router.get("/{product_slug}/articles")
def list_articles(product_slug: str, db: Session = Depends(get_db)) -> dict:
    items = []
    for row, publication in _published(db, product_slug):
        full = _article(row, publication)
        items.append({k: full[k] for k in ("slug", "title", "description", "category", "stage", "lead", "published_at", "updated_at")})
    return {"articles": items}


@router.get("/{product_slug}/articles/{slug}")
def get_article(product_slug: str, slug: str, db: Session = Depends(get_db)) -> dict:
    for row, publication in _published(db, product_slug):
        if publication.external_reference == f"/blog/{slug}":
            return _article(row, publication)
    raise HTTPException(404, "Not found")
