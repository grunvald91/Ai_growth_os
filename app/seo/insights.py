"""Conservative SEO overview from verifiable local publication rows only.

External impressions, clicks, search positions and AI citations are NOT derived
from the existence of an article. No claim of a connected webmaster account.
"""
from __future__ import annotations

from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.domain import Artifact, Publication


def company_seo_insights(db: Session, product_id: UUID) -> dict:
    status_counts = dict(db.execute(
        select(Publication.status, func.count(Publication.id))
        .join(Artifact, Artifact.id == Publication.artifact_id)
        .where(Publication.product_id == product_id,
               Artifact.product_id == product_id,
               Publication.channel == "own_site",
               Artifact.kind == "article",
               Publication.status.in_(["published", "unpublished"]))
        .group_by(Publication.status)
    ).all())
    return {
        "product_id": str(product_id),
        "published_articles": status_counts.get("published", 0),
        "hidden_articles": status_counts.get("unpublished", 0),
        # These are distinct data feeds. Never substitute zeros for unknowns.
        "search_sources": {
            "google_search_console": {"status": "integration_not_configured",
                                      "clicks": None, "impressions": None},
            "yandex_webmaster": {"status": "integration_not_configured",
                                "clicks": None, "impressions": None},
            "bing_ai_performance": {"status": "integration_not_configured",
                                    "citations": None},
            "chatgpt_search": {"status": "no_verified_citation_feed",
                               "citations": None},
        },
    }
