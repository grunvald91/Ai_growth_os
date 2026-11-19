"""Read-only catalog of curated ServGo expert chats.

Prompts are original ServGo adaptations, not imported agent code.
Only server-side allowlisted files can become system instructions.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS = Path(__file__).resolve().parent / "specialist_prompts"

SPECIALISTS: dict[str, dict] = {
    "business_consultant": {
        "id": "business_consultant",
        "name": "Бизнес-консультант",
        "description": "Стратегия, прибыль и решения для малого бизнеса",
        "icon": "briefcase",
        "starter_prompts": [
            "Помоги найти причины низкой прибыли в моём бизнесе.",
            "Как проверить спрос на новую услугу с минимальным бюджетом?",
            "Стоит ли нанимать ещё одного сотрудника?",
        ],
        "filename": "business_consultant.md",
    },
    "marketer": {
        "id": "marketer",
        "name": "Маркетолог",
        "description": "Привлечение клиентов, продвижение и рост",
        "icon": "target",
        "starter_prompts": [
            "Предложи три способа привлечь клиентов в мой бизнес.",
            "Помоги сформулировать позиционирование компании.",
            "Составь реалистичный маркетинговый план на 30 дней.",
        ],
        "filename": "marketer.md",
    },
    "smm_specialist": {
        "id": "smm_specialist",
        "name": "SMM-специалист",
        "description": "Соцсети, публикации, Reels и контент-планы",
        "icon": "megaphone",
        "starter_prompts": [
            "Создай пост для Instagram о моей услуге.",
            "Составь контент-план для Telegram на неделю.",
            "Придумай сценарий короткого видео для моей компании.",
        ],
        "filename": "smm_specialist.md",
    },
}


def specialist_catalog() -> list[dict]:
    """Public metadata only, without the instruction text or file paths."""
    return [
        {key: value for key, value in spec.items() if key != "filename"}
        for spec in SPECIALISTS.values()
    ]


@lru_cache(maxsize=3)
def get_specialist_prompt(specialist_id: str | None) -> str | None:
    if specialist_id is None:
        return None
    spec = SPECIALISTS.get(specialist_id)
    if spec is None:
        raise ValueError("Unknown ServGo specialist")
    return (_PROMPTS / spec["filename"]).read_text(encoding="utf-8").strip()
