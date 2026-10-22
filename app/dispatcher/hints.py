"""Hint chips under the input line. Computed from state and files, never by a model call.

Each chip is a complete request: pressing it sends it at once. Priority: what needs attention,
then everyday actions, then concrete topics from the company's own file.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.dispatcher.blocks import Chip
from app.dispatcher.readers import attention, plans
from app.dispatcher.registry import CAPABILITIES
from app.models.domain import Product
from app.schedule.source import linked_business_ids


def topic_ideas(products_root: Path, slug: str) -> list[Chip]:
    """Concrete post and video topics kept with the company's versioned files."""
    path = Path(products_root) / slug / "dispatcher_topics.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    chips: list[Chip] = []
    for item in data if isinstance(data, list) else []:
        try:
            kind, topic = item["kind"], str(item["topic"]).strip()
            if kind == "post":
                chips.append(Chip(label="Пост: " + topic, capability="create_post",
                                  params={"topic": topic, "channel": item.get("channel", "telegram")}))
            elif kind == "video":
                chips.append(Chip(label="Видео: " + topic, capability="whiteboard_video", params={"topic": topic}))
        except (KeyError, TypeError, ValueError):
            continue
    return chips


def compute_hints(db: Session, product: Product, products_root: Path) -> list[Chip]:
    chips: list[Chip] = []
    notes = attention(db, product.id)
    active, draft = plans(db, product.id)
    review = [n for n in notes if n["kind"] == "review"]
    errors = [n for n in notes if n["kind"] == "error"]
    if draft is not None:
        chips.append(Chip(label="Утвердить контент-план", capability="approve_plan"))
    if review:
        chips.append(Chip(label=f"Проверить готовое ({len(review)})", capability="show_review"))
    if errors:
        chips.append(Chip(label="Что пошло не так", capability="show_work"))
    chips.append(Chip(label="Что в работе", capability="show_work"))
    if active is not None:
        chips.append(Chip(label="Показать контент-план", capability="show_plan"))
    if active is None and draft is None:
        chips.append(Chip(label="Составить контент-план", capability="make_plan"))
    if linked_business_ids(db, product.id):
        chips.append(Chip(label="Записи на сегодня", capability="show_schedule",
                          params={"view": "bookings", "period": "today"}))
        chips.append(Chip(label="Свободные окна на неделю", capability="show_schedule",
                          params={"view": "free", "period": "week"}))
    chips.extend(topic_ideas(products_root, product.slug))
    seen, unique = set(), []
    for chip in chips:
        key = (chip.capability, tuple(sorted(chip.params.items())))
        if key not in seen and chip.capability in CAPABILITIES:
            seen.add(key)
            unique.append(chip)
    return unique
