"""Validated ingestion endpoint for public research evidence collected by a future search adapter."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.domain import Product
from app.services.artifacts import ArtifactService
from app.workflows.research_contracts import ResearchSourceBundle

router = APIRouter()


@router.post("/products/{product_id}/research-sources", status_code=201)
def add_research_sources(
    product_id: UUID,
    body: ResearchSourceBundle,
    db: Session = Depends(get_db),
):
    if db.get(Product, product_id) is None:
        raise HTTPException(404, "Product not found")
    artifact = ArtifactService(db).create(
        product_id=product_id,
        kind="research_source_bundle",
        title=body.label,
        content=body.model_dump_json(),
    )
    db.commit()
    db.refresh(artifact)
    return {
        "artifact_id": str(artifact.id),
        "source_count": len(body.sources),
        "status": "stored",
    }
