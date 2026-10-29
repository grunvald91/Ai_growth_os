"""Builds and stores the semantic core as a versioned artifact of the product."""
from datetime import datetime, timezone
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Artifact, Product
from app.seo.clustering import cluster_queries
from app.seo.contracts import SemanticCore, SourceStatus
from app.seo.suggest import collect
from app.services.artifacts import ArtifactService

KIND = "semantic_core"


async def build_semantic_core(db: Session, product_id: UUID, seeds: list[str], *,
                              client: httpx.AsyncClient | None = None, task_id: UUID | None = None) -> Artifact:
    product = db.get(Product, product_id)
    if product is None:
        raise LookupError("Product not found")
    seeds = list(dict.fromkeys(" ".join(s.split()) for s in seeds if s and s.strip()))
    if not seeds:
        raise ValueError("At least one seed query is required")
    results = await collect(seeds, client=client)
    if not any(r.queries for r in results.values()):
        raise RuntimeError("Search engines did not answer; no semantic core was built")
    core = SemanticCore(
        seeds=seeds, collected_at=datetime.now(timezone.utc),
        sources=[SourceStatus(engine=r.engine, requests=r.requests, failed_requests=r.failed,
                              queries_found=len(r.queries)) for r in results.values()],
        clusters=cluster_queries({engine: r.queries for engine, r in results.items()}),
    )
    artifact = ArtifactService(db).create(product_id=product_id, task_id=task_id, kind=KIND,
                                          title=f"Семантическое ядро: {', '.join(seeds)[:200]}",
                                          content=core.model_dump_json())
    db.commit()
    return artifact


def latest_semantic_core(db: Session, product_id: UUID) -> SemanticCore | None:
    row = db.scalar(select(Artifact).where(Artifact.product_id == product_id, Artifact.kind == KIND)
                    .order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(1))
    return SemanticCore.model_validate_json(row.content) if row else None

