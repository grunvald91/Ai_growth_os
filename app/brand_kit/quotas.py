"""Company-level asset quotas; call inside a locked Product transaction."""
from __future__ import annotations

from sqlalchemy import func, select


def enforce_quota(db, product_id, model, *, additional_bytes: int,
                  max_files: int, max_bytes: int) -> None:
    count, occupied = db.execute(select(
        func.count(), func.coalesce(func.sum(model.byte_size), 0)
    ).where(model.product_id == product_id)).one()
    if count >= max_files or occupied + additional_bytes > max_bytes:
        raise ValueError(
            "Достигнут лимит файлов компании. Удалите ненужные материалы.")
