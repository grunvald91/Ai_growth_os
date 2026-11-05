"""Public roadmap page data — reads `webapp/roadmap.yaml`.

The file holds the areas, feature cards and their statuses. To change a status,
edit `status` of a card and bump `updated`; the page recounts everything itself.
"""

from __future__ import annotations

import datetime
import os
from typing import Any

import yaml

_PATH = os.path.join(os.path.dirname(__file__), "roadmap.yaml")

_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _date_text(value: str) -> str:
    try:
        d = datetime.date.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def load_roadmap() -> dict[str, Any]:
    with open(_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    statuses = data["statuses"]
    status_labels = {s["id"]: s["label"] for s in statuses}
    known = set(status_labels)
    area_ids = {a["id"] for a in data["areas"]}
    items = data["items"]
    for item in items:
        if item["status"] not in known:
            raise ValueError(f"roadmap: unknown status {item['status']!r} in {item['id']}")
        if item["area"] not in area_ids:
            raise ValueError(f"roadmap: unknown area {item['area']!r} in {item['id']}")
    counts = {sid: sum(1 for i in items if i["status"] == sid) for sid in status_labels}
    return {
        "areas": data["areas"],
        "items": items,
        "goals": data["goals"],
        "statuses": statuses,
        "status_labels": status_labels,
        "counts": counts,
        "updated": data["updated"],
        "updated_text": _date_text(data["updated"]),
    }
