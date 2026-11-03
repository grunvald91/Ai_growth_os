"""Статьи блога, опубликованные платформой AI Growth OS (growth.servgo.ru).

Платформа отдаёт только опубликованные статьи через публичную read-only ленту.
Блог берёт их оттуда, кэширует в памяти и, если лента недоступна, продолжает
показывать последнюю удачную копию или встроенные статьи. Ленту нельзя использовать
для подмены встроенных статей: при совпадении slug побеждает встроенная.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime

import aiohttp

logger = logging.getLogger(__name__)

GROWTH_FEED_BASE = os.getenv(
    "BLOG_FEED_URL", "https://growth.servgo.ru/public/blog/servgo/articles"
).rstrip("/")
FEED_TTL_SECONDS = int(os.getenv("BLOG_FEED_TTL", "300"))
FEED_TIMEOUT_SECONDS = 5
_SLUG = re.compile(r"^[a-z0-9-]{1,120}$")
_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_TOPIC = {"Советы": "marketing", "Сравнение и выбор": "growth", "Запуск": "booking"}

_cache: dict = {"at": 0.0, "articles": [], "details": {}}
_lock = asyncio.Lock()


def _date(value: str) -> tuple[str, str]:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.date().isoformat(), f"{parsed.day} {_MONTHS[parsed.month - 1]} {parsed.year}"


_SPAN_STYLES = frozenset({"text", "strong", "em", "strong_em"})


def _safe_spans(value: object) -> list[dict]:
    """Accept only text + enumerated marks, never raw HTML from external feeds."""
    if not isinstance(value, list) or len(value) > 120:
        return []
    result = []
    for span in value:
        if (not isinstance(span, dict)
                or not isinstance(span.get("text"), str)
                or len(span["text"]) > 8000
                or span.get("style") not in _SPAN_STYLES):
            return []
        result.append({"text": span["text"], "style": span["style"]})
    return result


def normalize(raw: dict) -> dict | None:
    """Приводит запись ленты к форме встроенных статей; битые записи отбрасывает."""
    try:
        slug = str(raw["slug"])
        if not _SLUG.match(slug):
            return None
        title, lead = str(raw["title"]).strip(), str(raw["lead"]).strip()
        updated, label = _date(str(raw.get("updated_at") or raw["published_at"]))
        sections = []
        for section in raw.get("sections", []):
            blocks = []
            for block in section.get("blocks", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") in {"paragraph", "quote", "subheading"} and isinstance(block.get("text"), str):
                    parsed = {"type": block["type"], "text": block["text"]}
                    spans = _safe_spans(block.get("spans"))
                    if spans and block["type"] != "subheading":
                        parsed["spans"] = spans
                    blocks.append(parsed)
                elif block.get("type") == "list" and isinstance(block.get("items"), list):
                    items = [str(item) for item in block["items"] if str(item).strip()]
                    if items:
                        parsed = {"type": "list", "ordered": bool(block.get("ordered")), "items": items}
                        marked = block.get("item_spans")
                        if isinstance(marked, list) and len(marked) == len(items):
                            item_spans = [_safe_spans(value) for value in marked]
                            if all(item_spans):
                                parsed["item_spans"] = item_spans
                        blocks.append(parsed)
            sections.append({
                "title": str(section["title"]),
                "paragraphs": [str(p) for p in section.get("paragraphs", [])],
                "bullets": [str(p) for p in section.get("bullets", [])],
                "blocks": blocks,
            })
        faq = [
            {"question": str(i["question"]), "answer": str(i["answer"])}
            for i in raw.get("faq", [])
        ]
        category = str(raw.get("category") or "Советы")
        facts = raw.get("company_facts")
        company_facts = ([fact.strip() for fact in facts
                          if isinstance(fact, str) and fact.strip()][:24]
                         if isinstance(facts, list) else [])
        if not title or not lead or not sections:
            return None
        return {
            "slug": slug,
            "from_feed": True,
            "title": title,
            "description": str(raw.get("description") or lead)[:300],
            "lead": lead,
            "category": category,
            "topic": _TOPIC.get(category, "growth"),
            "updated": updated,
            "published": _date(str(raw.get("published_at") or updated))[0],
            "date_label": label,
            "direct_answer": str(raw.get("direct_answer") or "").strip(),
            "company_facts": company_facts,
            "sections": sections,
            "closing": str(raw.get("closing") or "").strip(),
            "faq": faq,
            "primary_query": str(raw.get("primary_query") or ""),
            "cta_text": str(raw.get("cta") or "") or None,
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


async def _download(session: aiohttp.ClientSession) -> list[dict]:
    """Список берётся целиком, а полный текст — только для новых или изменённых статей."""
    async with session.get(GROWTH_FEED_BASE) as response:
        response.raise_for_status()
        items = (await response.json()).get("articles", [])
    known: dict = _cache["details"]
    fresh: dict = {}
    articles = []
    for item in items:
        slug = str(item.get("slug", ""))
        if not _SLUG.match(slug):
            continue
        version = (slug, str(item.get("updated_at")))
        article = known.get(version)
        if article is None:
            async with session.get(f"{GROWTH_FEED_BASE}/{slug}") as response:
                if response.status != 200:
                    continue
                article = normalize(await response.json())
        if article:
            fresh[version] = article
            articles.append(article)
    _cache["details"] = fresh
    return articles


async def feed_articles(*, now: float | None = None) -> list[dict]:
    """Опубликованные платформой статьи; при сбое — последняя удачная копия или пусто."""
    if not GROWTH_FEED_BASE:  # лента выключена (например, в тестах)
        return []
    moment = time.monotonic() if now is None else now
    if _cache["at"] and moment - _cache["at"] < FEED_TTL_SECONDS:
        return list(_cache["articles"])
    async with _lock:  # одновременные посетители не скачивают ленту по нескольку раз
        if _cache["at"] and moment - _cache["at"] < FEED_TTL_SECONDS:
            return list(_cache["articles"])
        try:
            timeout = aiohttp.ClientTimeout(total=FEED_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                _cache["articles"] = await _download(session)
        except Exception as exc:  # лента не должна ронять блог
            logger.warning("blog feed unavailable: %s", type(exc).__name__)
        _cache["at"] = moment
    return list(_cache["articles"])


def reset_cache() -> None:
    _cache.update(at=0.0, articles=[], details={})


async def all_articles(builtin: tuple[dict, ...]) -> list[dict]:
    """Встроенные + платформенные статьи, новые первыми; slug встроенных не перекрывается."""
    taken = {a["slug"] for a in builtin}
    extra = [a for a in await feed_articles() if a["slug"] not in taken]
    return sorted([*builtin, *extra], key=lambda a: a["updated"], reverse=True)
