"""Tenant-scoped, owner-controlled visibility of previously published SEO articles.

Hiding a page removes it from the read-only blog feed and sitemap without
deleting its URL record. Restoring keeps the original permalink and does not
start the SEO worker or spend model credits.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Artifact, ArtifactStatus, Product, Publication
from app.seo.activation import is_enabled

CHANNEL = "own_site"
VISIBLE = "published"
HIDDEN = "unpublished"


class VisibilityError(ValueError):
    pass


def change_article_visibility(db: Session, *, product_id: UUID,
                              article_id: UUID, action: str,
                              actor: str) -> bool:
    """Return True on a state change, False for an idempotent repeat.

    The same company row serializes visibility changes and SEO producers.
    A hidden article remains in the database and can be restored at the
    original public URL; unrelated companies and channels are untouched.
    """
    product = db.scalar(select(Product.id).where(
        Product.id == product_id).with_for_update())
    if product is None:
        raise LookupError("Компания не найдена")
    if action not in ("hide", "restore"):
        raise VisibilityError("Неизвестное действие")
    row = db.scalar(select(Artifact).where(
        Artifact.id == article_id, Artifact.product_id == product_id,
        Artifact.kind == "article").with_for_update())
    publication = db.scalar(select(Publication).where(
        Publication.artifact_id == article_id,
        Publication.product_id == product_id,
        Publication.channel == CHANNEL,
        Publication.status.in_([VISIBLE, HIDDEN]),
    ).with_for_update())
    if row is None or publication is None:
        raise LookupError("Опубликованная статья не найдена")
    if action == "restore" and not is_enabled(db, product_id):
        raise VisibilityError("SEO-завод выключен. Включите его перед возвращением статьи.")
    target = HIDDEN if action == "hide" else VISIBLE
    if publication.status == target:
        return False
    publication.status = target
    row.status = ArtifactStatus.APPROVED if target == HIDDEN else ArtifactStatus.PUBLISHED
    db.add(Artifact(
        product_id=product_id, task_id=row.task_id, kind="seo_visibility_event",
        title="SEO: статья скрыта" if target == HIDDEN else "SEO: статья возвращена",
        content=json.dumps({
            "article_id": str(article_id), "action": action,
            "actor": actor[:200], "at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False),
    ))
    db.commit()
    return True
