"""Studio owner connects a ServGo business to a studio company. Only the owner does this."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Product
from app.sso.models import StudioBusinessLink


class LinkError(ValueError):
    """A message the owner can read."""


def list_links(db: Session) -> list[StudioBusinessLink]:
    return list(db.scalars(select(StudioBusinessLink).order_by(StudioBusinessLink.servgo_business_id).limit(500)))


def connect(db: Session, business_id_text: str, product_id: str) -> StudioBusinessLink:
    text = (business_id_text or "").strip()
    if not text.isdigit() or not 0 < int(text) < 2**31:
        raise LinkError("Номер заведения в ServGo — это число, например 12")
    try:
        product = db.get(Product, UUID(product_id))
    except ValueError:
        product = None
    if product is None:
        raise LinkError("Выберите компанию из списка")
    link = db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == int(text)))
    if link is None:
        link = StudioBusinessLink(servgo_business_id=int(text), product_id=product.id)
        db.add(link)
    else:
        link.product_id = product.id
    db.commit()
    return link


def disconnect(db: Session, link_id: str) -> None:
    try:
        link = db.get(StudioBusinessLink, UUID(link_id))
    except ValueError:
        link = None
    if link is None:
        raise LinkError("Привязка уже удалена")
    db.delete(link)
    db.commit()
