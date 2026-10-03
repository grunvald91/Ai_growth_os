"""Explicit launch of the first Issue #8 package; idempotent across deployments."""
from sqlalchemy import select
from app.content.service import queue_content
from app.db.session import SessionLocal
from app.models.domain import Artifact, Product

def main():
    with SessionLocal() as db:
        product = db.scalar(select(Product).where(Product.slug == 'servgo').with_for_update())
        if product is None:
            raise LookupError('ServGo not found')
        existing = db.scalar(select(Artifact.id).where(Artifact.product_id == product.id,
            Artifact.kind == 'content_request').limit(1))
        if existing:
            print('Issue #8 content package already requested; not duplicated')
        else:
            task = queue_content(db, product.id)
            print('Issue #8 first content task queued:', task.id)

if __name__ == '__main__':
    main()
