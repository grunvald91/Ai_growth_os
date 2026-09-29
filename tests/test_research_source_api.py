from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Artifact, Product


def test_research_source_ingestion_is_bounded_and_persisted():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        product = Product(name="ServGo", slug="servgo")
        db.add(product)
        db.commit()
        app.dependency_overrides[get_db] = lambda: db
        try:
            with TestClient(app) as client:
                response = client.post(
                    f"/products/{product.id}/research-sources",
                    json={
                        "label": "Public competitor reviews",
                        "sources": [{
                            "url": "https://reviews.example/1",
                            "title": "Review",
                            "platform": "ExampleReviews",
                            "competitor": "ExampleBook",
                            "excerpt": "The integration is limited.",
                            "rating": 3,
                        }],
                    },
                )
                assert response.status_code == 201, response.text
                assert response.json()["source_count"] == 1
                artifact = db.scalar(select(Artifact).where(Artifact.kind == "research_source_bundle"))
                assert artifact is not None
                assert "https://reviews.example/1" in artifact.content
        finally:
            app.dependency_overrides.clear()
    engine.dispose()
