"""What an ability receives and returns. Kept apart so every ability module can import it without cycles."""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.orm import Session

from app.dispatcher.registry import page_href, page_label
from app.models.domain import Product, Task


@dataclass
class Context:
    db: Session
    product: Product
    settings: object
    account: str


@dataclass
class Confirmation:
    """A card asking before something with consequences. `params` come back when it is confirmed."""
    title: str
    details: list[str]
    params: dict
    paid: bool = False
    confirm_label: str = "Подтвердить"


@dataclass
class Outcome:
    text: str
    spoken: str | None = None
    blocks: list[dict] = field(default_factory=list)
    task_id: UUID | None = None
    confirmation: Confirmation | None = None
    ok: bool = True
    # Nothing was done because something is missing (for example no data yet).
    no_data: bool = False


def task_block(ctx: Context, task: Task, title: str, href: str) -> dict:
    return {"type": "task", "task_id": str(task.id), "title": title,
            "status": getattr(task.status, "value", str(task.status)), "href": href}


def links_block(ctx: Context, *keys: str) -> dict:
    return {"type": "links", "items": [{"label": page_label(k), "href": page_href(k, ctx.product.id)} for k in keys]}
