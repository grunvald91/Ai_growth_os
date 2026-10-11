"""Shared, bounded source loading for research and business intelligence."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Artifact
from app.workflows.research_contracts import ResearchSourceBundle


def load_source_bundles(db: Session, product_id: UUID) -> list[ResearchSourceBundle]:
    rows = db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.kind == "research_source_bundle",
    ).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(10))
    bundles = []
    count = 0
    for row in rows:
        try:
            bundle = ResearchSourceBundle.model_validate_json(row.content)
        except ValueError:
            continue
        remaining = 50 - count
        if remaining <= 0:
            break
        sources = bundle.sources[:remaining]
        bundles.append(ResearchSourceBundle(label=bundle.label, sources=sources))
        count += len(sources)
    return bundles
