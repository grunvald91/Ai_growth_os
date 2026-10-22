"""Owner-level company maintenance: rename and delete."""
from __future__ import annotations

import shutil
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Base
from app.intake.contracts import IntakeError
from app.models.domain import Product, Task

STUDIO_SLUG_PREFIX = "company-"  # Companies added through Growth Studio own their profile folder.


def rename_company(db: Session, product: Product, *, name: str, description: str) -> None:
    name = " ".join(name.split())
    description = description.strip()
    if not 2 <= len(name) <= 200:
        raise IntakeError("Название компании: от 2 до 200 символов.")
    if len(description) > 10000:
        raise IntakeError("Описание слишком длинное: до 10 000 символов.")
    product.name, product.description = name, description or None
    db.commit()


def _safe_rmtree(root: Path, target: Path) -> None:
    root, target = root.resolve(), target.resolve()
    if target != root and target.is_relative_to(root) and target.is_dir() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=True)


def delete_company(db: Session, products_root: Path, product_id: UUID) -> str:
    """Remove the company and everything stored for it; returns its name.

    Rows go child-first in one transaction. Generated files (renders) are removed for every
    company; the profile folder only for companies created in Studio, so versioned product
    knowledge kept in the repository is never touched.
    """
    product = db.get(Product, product_id)
    if product is None:
        raise LookupError("Company not found")
    name, slug = product.name, product.slug
    task_ids = select(Task.id).where(Task.product_id == product_id)
    for table in reversed(Base.metadata.sorted_tables):
        if table.name == "products":
            continue
        if "product_id" in table.c:
            db.execute(delete(table).where(table.c.product_id == product_id))
        elif "task_id" in table.c:
            db.execute(delete(table).where(table.c.task_id.in_(task_ids)))
    db.execute(delete(Product).where(Product.id == product_id))
    db.commit()
    root = Path(products_root)
    _safe_rmtree(root, root / "_content" / str(product_id))
    if slug.startswith(STUDIO_SLUG_PREFIX) and "/" not in slug and ".." not in slug:
        _safe_rmtree(root, root / slug)
    return name
