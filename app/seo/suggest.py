"""Search suggestions from Yandex and Google: free, public, and the honest signal of what people type."""
import asyncio
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

import httpx

from app.seo.contracts import Engine

_logger = logging.getLogger(__name__)

RUSSIAN_LETTERS = "абвгдежзиклмнопрстуфхцчшщэюя"
QUESTION_PREFIXES = (
    "как", "что такое", "зачем", "почему", "сколько стоит", "где", "какой", "какие", "можно ли",
    "лучший", "лучшие", "как выбрать", "как сделать", "как начать",
)
SUFFIXES = ("для", "или", "с", "без", "цена", "отзывы", "сравнение", "программа", "сервис", "онлайн", "бесплатно", "своими руками")

ENDPOINTS: dict[Engine, tuple[str, dict[str, str], str]] = {
    "yandex": ("https://suggest.yandex.ru/suggest-ff.cgi", {"uil": "ru", "v": "4"}, "part"),
    "google": ("https://suggestqueries.google.com/complete/search", {"client": "firefox", "hl": "ru"}, "q"),
}


def expand_seed(seed: str) -> list[str]:
    """One seed becomes many partial queries so suggestions reveal the long tail."""
    seed = " ".join(seed.split())
    probes = [seed]
    probes += [f"{prefix} {seed}" for prefix in QUESTION_PREFIXES]
    probes += [f"{seed} {word}" for word in SUFFIXES]
    probes += [f"{seed} {letter}" for letter in RUSSIAN_LETTERS]
    return list(dict.fromkeys(probes))


@dataclass
class EngineResult:
    engine: Engine
    requests: int = 0
    failed: int = 0
    queries: set[str] = field(default_factory=set)


def parse_suggestions(body: str) -> list[str]:
    """Both engines answer with ["query", ["suggestion", ...]]; anything else is treated as no data."""
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list):
        return []
    out = []
    for item in data[1]:
        # Yandex may return ["text", {...meta}] pairs for some suggestion kinds.
        text = item[0] if isinstance(item, list) and item else item
        if isinstance(text, str) and text.strip():
            out.append(text.strip())
    return out


async def collect(seeds: Iterable[str], *, client: httpx.AsyncClient | None = None,
                  engines: tuple[Engine, ...] = ("yandex", "google"),
                  concurrency: int = 4) -> dict[Engine, EngineResult]:
    """Ask every engine about every probe. A failed request is counted, never replaced with guesses."""
    probes = list(dict.fromkeys(p for seed in seeds for p in expand_seed(seed)))
    own = client is None
    client = client or httpx.AsyncClient(timeout=10.0, follow_redirects=False,
                                         headers={"User-Agent": "Mozilla/5.0 (compatible; GrowthOS/1.0)"})
    results = {engine: EngineResult(engine) for engine in engines}
    gate = asyncio.Semaphore(concurrency)

    async def one(engine: Engine, probe: str) -> None:
        url, fixed, param = ENDPOINTS[engine]
        async with gate:
            result = results[engine]
            result.requests += 1
            try:
                response = await client.get(url, params={**fixed, param: probe})
                response.raise_for_status()
                result.queries.update(parse_suggestions(response.text))
            except (httpx.HTTPError, ValueError) as exc:
                result.failed += 1
                _logger.info("suggest_failed engine=%s error=%s", engine, type(exc).__name__)

    try:
        await asyncio.gather(*(one(engine, probe) for engine in engines for probe in probes))
    finally:
        if own:
            await client.aclose()
    return results
