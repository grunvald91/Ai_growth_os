"""Read-only public feed of published articles. The blog on servgo.ru renders pages from it."""
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.domain import Artifact, ArtifactStatus, Product, Publication
from app.workflows.article_contracts import ArticleArtifact

router = APIRouter(prefix="/public/blog", tags=["public-blog"])
_BULLET = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+(.*\S)\s*$")
_CATEGORY = {"awareness": "Советы", "consideration": "Сравнение и выбор", "decision": "Запуск"}


def split_body(body: str) -> tuple[list[str], list[str]]:
    paragraphs, bullets = [], []
    for line in body.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            continue
        match = _BULLET.match(line)
        (bullets if match else paragraphs).append(match.group(1) if match else line.strip())
    return paragraphs, bullets


def _article(row: Artifact, publication: Publication) -> dict:
    article = ArticleArtifact.model_validate_json(row.content)
    reader = article.reader_article
    sections = []
    for section in reader.sections:
        paragraphs, bullets = split_body(section.body)
        sections.append({"title": section.heading, "paragraphs": paragraphs, "bullets": bullets})
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
        "closing": reader.closing,
        "faq": [item.model_dump() for item in reader.faq],
        "cta": article.conversion.cta,
        "published_at": publication.published_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _published(db: Session, product_slug: str):
    product = db.scalar(select(Product).where(Product.slug == product_slug))
    if product is None:
        raise HTTPException(404, "Not found")
    return db.execute(
        select(Artifact, Publication).join(Publication, Publication.artifact_id == Artifact.id)
        .where(Publication.product_id == product.id, Publication.channel == "own_site", Publication.status == "published",
               Artifact.status == ArtifactStatus.PUBLISHED, Artifact.kind == "article")
        .order_by(Publication.published_at.desc())).all()


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
