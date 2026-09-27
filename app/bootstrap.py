"""Idempotent local ServGo bootstrap. No invented metrics or external calls."""
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.domain import Product, Goal


def bootstrap(db):
    product = db.scalar(select(Product).where(Product.slug == 'servgo'))
    if product is None:
        product = Product(name='ServGo', slug='servgo', description='Online service booking/reservation system for small service businesses.')
        db.add(product)
        db.flush()
    if db.scalar(select(Goal).where(Goal.product_id == product.id, Goal.name == 'Validate organic customer acquisition')) is None:
        db.add(Goal(product_id=product.id, name='Validate organic customer acquisition',
            description='Test evidence-backed SEO, articles and video briefs; measure traffic to leads to demo/registration/booking to customers. No paid ads.',
            target_metric='qualified_leads'))
    db.commit()
    return product.id

if __name__ == '__main__':
    with SessionLocal() as db:
        print(bootstrap(db))
