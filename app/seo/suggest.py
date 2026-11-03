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
    """Use genuine short search prefixes before expanding; long AI questions often return [].

    A long, precise sentence is not itself evidence of search demand. Derive
    overlapping *verbatim* fragments only as probes; only returned search-engine
    suggestions enter the semantic core. Bound probes per seed to include all
    seeds inside the shared request budget.
    """
    seed = " ".join(seed.split())
    words = seed.split()
    if len(words) >= 5:
        probes = [seed]
        weak_start = {"и", "или", "в", "во", "на", "с", "со", "у", "к", "по", "без", "для"}
        for size in (3, 4, 2):
            for offset in range(len(words) - size + 1):
                fragment = words[offset:offset + size]
                if fragment[0].lower() in weak_start or fragment[-1].lower() in weak_start:
                    continue
                probe = " ".join(fragment)
                if len(probe) >= 8 and probe not in probes:
                    probes.append(probe)
                if len(probes) >= 14:
                    return probes
        return probes
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
    empty: int = 0
    invalid: int = 0
    queries: set[str] = field(default_factory=set)
    completed: set[str] = field(default_factory=set)


def _has_suggestion_shape(body: str) -> bool:
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        return False
    return isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list)


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
                  concurrency: int = 4, max_requests: int = 400, total_timeout: float = 120, initial=None, on_batch=None) -> dict[Engine, EngineResult]:
    """Ask every engine about every probe. A failed request is counted, never replaced with guesses."""
    if concurrency < 1 or max_requests < 1 or total_timeout <= 0:
        raise ValueError("Invalid suggestion budget")
    seeds = list(seeds)
    if len(seeds) > 10:
        raise ValueError("At most ten seed queries are allowed")
    probes = list(dict.fromkeys(p for seed in seeds for p in expand_seed(seed)))
    own = client is None
    client = client or httpx.AsyncClient(timeout=10.0, follow_redirects=False,
                                         headers={"User-Agent": "Mozilla/5.0 (compatible; GrowthOS/1.0)"})
    results = {engine: EngineResult(engine) for engine in engines}
    for engine, saved in (initial or {}).items():
        if engine in results:
            results[engine] = EngineResult(engine, requests=saved["requests"], failed=saved["failed"],
                                          empty=saved.get("empty", 0),
                                          invalid=saved.get("invalid", 0),
                                          queries=set(saved["queries"]), completed=set(saved["completed"]))
    gate = asyncio.Semaphore(concurrency)

    async def one(engine: Engine, probe: str) -> None:
        url, fixed, param = ENDPOINTS[engine]
        async with gate:
            result = results[engine]
            result.requests += 1
            try:
                response = await client.get(url, params={**fixed, param: probe})
                response.raise_for_status()
                suggestions = parse_suggestions(response.text)
                if suggestions:
                    result.queries.update(suggestions)
                    result.completed.add(probe)
                elif _has_suggestion_shape(response.text):
                    result.empty += 1
                    result.completed.add(probe)
                else:
                    result.invalid += 1
            except (httpx.HTTPError, ValueError) as exc:
                result.failed += 1
                _logger.info("suggest_failed engine=%s error=%s", engine, type(exc).__name__)
            # Transient and malformed responses remain eligible for retry.

    try:
        # Round-robin engines; create only one bounded batch of coroutines at a time.
        # The request budget is TOTAL per semantic-core run, not an extra
        # allowance on each checkpoint replay. Retry failures within that cap.
        jobs = [(engine, probe) for probe in probes for engine in engines][:max_requests]
        jobs = [(engine, probe) for engine, probe in jobs if probe not in results[engine].completed]
        try:
            async with asyncio.timeout(total_timeout):
                for offset in range(0, len(jobs), concurrency):
                    await asyncio.gather(*(one(*job) for job in jobs[offset:offset + concurrency]))
                    if on_batch is not None:
                        on_batch(results)
        except TimeoutError:
            _logger.info("suggest_budget_exhausted")
    finally:
        if own:
            await client.aclose()
    return results
