"""CLI to import validated public research-source bundles into AI Growth OS."""
import argparse
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.domain import Artifact, Product
from app.services.artifacts import ArtifactService
from app.workflows.research_contracts import ResearchSourceBundle


def ingest(*, product_slug: str, file_path: Path) -> tuple[str, int]:
    bundle = ResearchSourceBundle.model_validate_json(file_path.read_text(encoding="utf-8"))
    with SessionLocal() as db:
        product = db.scalar(select(Product).where(Product.slug == product_slug))
        if product is None:
            raise SystemExit(f"Product not found: {product_slug}")
        existing = db.scalar(
            select(Artifact)
            .where(
                Artifact.product_id == product.id,
                Artifact.kind == "research_source_bundle",
                Artifact.title == bundle.label,
            )
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
            .limit(1)
        )
        content = bundle.model_dump_json()
        if existing is None:
            artifact = ArtifactService(db).create(
                product_id=product.id,
                kind="research_source_bundle",
                title=bundle.label,
                content=content,
            )
            action = "created"
        elif existing.content != content:
            artifact = ArtifactService(db).update(existing.id, content=content)
            action = "updated"
        else:
            artifact = existing
            action = "unchanged"
        db.commit()
        return action, len(bundle.sources)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", required=True)
    parser.add_argument("--file", required=True, type=Path)
    args = parser.parse_args()
    action, count = ingest(product_slug=args.product, file_path=args.file)
    print(f"research sources {action}; source_count={count}")


if __name__ == "__main__":
    main()
