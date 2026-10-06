"""Evidence-backed viral/trend research for video and article content.

This layer intentionally separates observable reach/trend signals from buyer-demand
claims. YouTube public evidence is normalized where possible against a creator's
recent Shorts baseline. Article evidence uses public Google News RSS and is ranked
by freshness and cross-source recurrence, never fake view counts.
"""
from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from statistics import median
from typing import Literal
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from defusedxml import ElementTree as SafeET
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.orm import Session

from app.content.viral_research import (
    ViralVideoCandidate,
    YouTubePublicScout,
    _extract_json_object,
    build_search_queries,
    parse_youtube_search_page,
)
from app.llm.schemas import LLMMessage, LLMRequest
from app.llm.service import LLMService
from app.models.domain import AgentRun, Artifact, CostRecord, Task
from app.privacy import PrivacyGateway
from app.services.artifacts import ArtifactService


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RankedVideoCandidate(Contract):
    source_id: str
    url: AnyHttpUrl
    query: str
    title: str
    channel: str | None = None
    view_count: int | None = Field(default=None, ge=0)
    published_text: str | None = None
    is_short: bool = False
    transcript_excerpt: str | None = Field(default=None, max_length=5000)
    channel_id: str | None = None
    published_at: datetime | None = None
    age_days: float | None = Field(default=None, ge=0)
    channel_baseline_median_views: int | None = Field(default=None, ge=0)
    outlier_ratio: float | None = Field(default=None, ge=0)
    viral_score: float = Field(ge=0, le=100)
    evidence_confidence: Literal["high", "medium", "low"]


class ArticleCandidate(Contract):
    source_id: str
    url: AnyHttpUrl
    query: str
    title: str
    publisher: str | None = None
    published_at: datetime | None = None
    age_days: float | None = Field(default=None, ge=0)
    snippet: str | None = Field(default=None, max_length=1600)
    cross_source_hits: int = Field(default=1, ge=1)
    trend_score: float = Field(ge=0, le=100)
    evidence_confidence: Literal["high", "medium", "low"]


class ContentPattern(Contract):
    pattern: str = Field(min_length=3, max_length=1400)
    why_it_may_work: str = Field(min_length=3, max_length=1800)
    source_ids: list[str] = Field(min_length=1, max_length=10)


class PainOpportunity(Contract):
    pain: str = Field(min_length=5, max_length=900)
    source_ids: list[str] = Field(min_length=1, max_length=10)
    why_it_matters: str = Field(min_length=5, max_length=1400)
    product_match: Literal["verified_match", "hypothesis", "gap"]
    product_fact_id: str | None = Field(default=None, max_length=80)
    product_match_explanation: str = Field(min_length=5, max_length=1400)
    presentation_angle: str = Field(min_length=5, max_length=1400)
    humor_angle: str | None = Field(default=None, max_length=900)

    @model_validator(mode="after")
    def verified_match_needs_fact(self):
        if self.product_match == "verified_match" and not self.product_fact_id:
            raise ValueError("verified_match requires product_fact_id")
        return self


class ViralContentAnalysisDraft(Contract):
    # The evidence ledger can legitimately contain only one source family. Requiring
    # arbitrary list counts made a correct sparse analysis fail validation and threw
    # away useful evidence. Quality is scored separately from schema validity.
    summary: str = Field(min_length=30, max_length=5000)
    audience_pains: list[PainOpportunity] = Field(default_factory=list, max_length=10)
    hook_patterns: list[ContentPattern] = Field(default_factory=list, max_length=10)
    topic_patterns: list[ContentPattern] = Field(default_factory=list, max_length=10)
    article_patterns: list[ContentPattern] = Field(default_factory=list, max_length=10)
    stepps_mechanisms: list[str] = Field(default_factory=list, max_length=10)
    seo_topic_seeds: list[str] = Field(default_factory=list, max_length=18)
    content_angles: list[str] = Field(default_factory=list, max_length=18)
    white_space_angles: list[str] = Field(default_factory=list, max_length=10)
    humor_angles: list[str] = Field(default_factory=list, max_length=10)
    counterexamples: list[str] = Field(default_factory=list, max_length=8)
    cautions: list[str] = Field(default_factory=list, max_length=12)


class ViralContentResearch(Contract):
    observed_at: datetime
    source_status: Literal["evidence_available", "insufficient_evidence", "source_unavailable"]
    analysis_status: Literal["completed", "not_run", "failed"]
    research_quality: Literal["strong_multisource", "video_led", "article_led", "mixed_weak", "insufficient"]
    niche: str = Field(min_length=1, max_length=500)
    search_queries: list[str] = Field(min_length=1, max_length=10)
    scoring_basis: str = Field(min_length=20, max_length=1800)
    video_candidates: list[RankedVideoCandidate] = Field(default_factory=list, max_length=20)
    article_candidates: list[ArticleCandidate] = Field(default_factory=list, max_length=20)
    approved_product_facts: dict[str, str] = Field(default_factory=dict, max_length=30)
    summary: str = Field(min_length=10, max_length=5000)
    audience_pains: list[PainOpportunity] = Field(default_factory=list, max_length=10)
    hook_patterns: list[ContentPattern] = Field(default_factory=list, max_length=10)
    topic_patterns: list[ContentPattern] = Field(default_factory=list, max_length=10)
    article_patterns: list[ContentPattern] = Field(default_factory=list, max_length=10)
    stepps_mechanisms: list[str] = Field(default_factory=list, max_length=10)
    seo_topic_seeds: list[str] = Field(default_factory=list, max_length=18)
    content_angles: list[str] = Field(default_factory=list, max_length=18)
    white_space_angles: list[str] = Field(default_factory=list, max_length=10)
    humor_angles: list[str] = Field(default_factory=list, max_length=10)
    counterexamples: list[str] = Field(default_factory=list, max_length=8)
    cautions: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def grounded(self):
        source_ids = {x.source_id for x in self.video_candidates} | {x.source_id for x in self.article_candidates}
        for pattern in [*self.hook_patterns, *self.topic_patterns, *self.article_patterns]:
            if set(pattern.source_ids) - source_ids:
                raise ValueError("pattern references unknown source")
        for pain in self.audience_pains:
            if set(pain.source_ids) - source_ids:
                raise ValueError("pain references unknown source")
            if pain.product_fact_id and pain.product_fact_id not in self.approved_product_facts:
                raise ValueError("pain references unknown product fact")
        return self


def _age_days(moment: datetime | None, now: datetime) -> float | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (now - moment.astimezone(timezone.utc)).total_seconds() / 86400.0)


def _freshness(age_days: float | None, half_life_days: float = 30.0) -> float:
    if age_days is None:
        return 0.35
    return max(0.05, math.exp(-math.log(2) * age_days / half_life_days))


def _video_score(*, views: int | None, is_short: bool, outlier_ratio: float | None,
                 age_days: float | None, transcript: bool) -> tuple[float, Literal["high", "medium", "low"]]:
    reach = min(1.0, math.log10(max(1, (views or 0)) + 1) / 6.0) if views is not None else 0.0
    outlier = min(1.0, (outlier_ratio or 0.0) / 4.0) if outlier_ratio is not None else reach * 0.55
    fresh = _freshness(age_days, 35)
    short = 1.0 if is_short else 0.25
    transcript_score = 1.0 if transcript else 0.25
    score = round(100 * (0.34 * outlier + 0.22 * reach + 0.20 * fresh + 0.14 * short + 0.10 * transcript_score), 1)
    if outlier_ratio is not None and age_days is not None and views is not None:
        confidence = "high"
    elif views is not None and (age_days is not None or transcript):
        confidence = "medium"
    else:
        confidence = "low"
    return score, confidence


def _normal_tokens(text: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-zа-яё0-9]{4,}", text.casefold())
        if token not in {"который", "которые", "чтобы", "online", "video", "shorts", "youtube"}
    }


def _similar_title(a: str, b: str) -> bool:
    one, two = _normal_tokens(a), _normal_tokens(b)
    if not one or not two:
        return False
    return len(one & two) / max(1, len(one | two)) >= 0.28


def _duckduckgo_target_url(value: str) -> str | None:
    href = html_lib.unescape(value or "").strip()
    if href.startswith("//"):
        href = "https:" + href
    if not href.startswith(("http://", "https://")):
        return None
    parsed = urlparse(href)
    if (parsed.hostname or "").endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = (parse_qs(parsed.query).get("uddg") or [None])[0]
        href = unquote(target) if target else ""
    if not href.startswith(("http://", "https://")):
        return None
    host = (urlparse(href).hostname or "").casefold()
    if not host or host.endswith("duckduckgo.com"):
        return None
    return href


def parse_duckduckgo_html(page: str, query: str) -> list[ArticleCandidate]:
    """Parse the static DuckDuckGo HTML endpoint without executing page JS."""
    matches = list(re.finditer(
        r'<a[^>]*class=["\'][^"\']*result__a[^"\']*["\'][^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        page,
        re.IGNORECASE | re.DOTALL,
    ))
    rows: list[ArticleCandidate] = []
    seen: set[str] = set()
    for index, match in enumerate(matches):
        target = _duckduckgo_target_url(match.group(1))
        if not target or target in seen:
            continue
        seen.add(target)
        title = re.sub(r'<[^>]+>', ' ', match.group(2))
        title = re.sub(r'\s+', ' ', html_lib.unescape(title)).strip()
        if not title:
            continue
        block_end = matches[index + 1].start() if index + 1 < len(matches) else min(len(page), match.end() + 5000)
        block = page[match.end():block_end]
        snippet_match = re.search(
            r'<a[^>]*class=["\'][^"\']*result__snippet[^"\']*["\'][^>]*>(.*?)</a>',
            block,
            re.IGNORECASE | re.DOTALL,
        )
        snippet = None
        if snippet_match:
            snippet = re.sub(r'<[^>]+>', ' ', snippet_match.group(1))
            snippet = re.sub(r'\s+', ' ', html_lib.unescape(snippet)).strip()[:1600] or None
        host = (urlparse(target).hostname or "").removeprefix("www.")
        source_id = "web:" + hashlib.sha256(target.encode()).hexdigest()[:12]
        rows.append(ArticleCandidate(
            source_id=source_id,
            url=target,
            query=query,
            title=title[:500],
            publisher=host or None,
            published_at=None,
            age_days=None,
            snippet=snippet,
            cross_source_hits=1,
            trend_score=35.0 if snippet else 25.0,
            evidence_confidence="medium" if snippet else "low",
        ))
    return rows


class DuckDuckGoPublicArticleScout:
    def __init__(self, *, timeout_seconds: float = 20.0):
        self.timeout_seconds = timeout_seconds

    async def collect(self, queries: list[str], max_candidates: int = 16) -> list[ArticleCandidate]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
            "Referer": "https://html.duckduckgo.com/",
        }
        rows: list[ArticleCandidate] = []
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True, headers=headers) as client:
            for query in queries:
                try:
                    response = await client.post(
                        "https://html.duckduckgo.com/html/",
                        data={"q": query, "kl": "ru-ru"},
                    )
                    response.raise_for_status()
                except httpx.HTTPError:
                    continue
                if "bots use DuckDuckGo too" in response.text:
                    continue
                rows.extend(parse_duckduckgo_html(response.text, query))
        return rows[:max_candidates]


class GoogleNewsPublicScout:
    def __init__(self, *, timeout_seconds: float = 20.0):
        self.timeout_seconds = timeout_seconds

    async def collect(self, queries: list[str], max_candidates: int = 16) -> list[ArticleCandidate]:
        now = datetime.now(timezone.utc)
        raw: list[dict] = []
        headers = {"User-Agent": "Mozilla/5.0 (compatible; AiGrowthOS/1.0; +https://github.com/)"}
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True, headers=headers) as client:
            for query in queries:
                try:
                    response = await client.get(
                        "https://news.google.com/rss/search",
                        params={"q": query, "hl": "ru", "gl": "RU", "ceid": "RU:ru"},
                    )
                    response.raise_for_status()
                    root = SafeET.fromstring(response.content)
                except Exception:
                    continue
                for item in root.iter():
                    if not item.tag.endswith("item"):
                        continue
                    fields = {}
                    source = None
                    for child in item:
                        tag = child.tag.rsplit("}", 1)[-1]
                        if tag == "source":
                            source = (child.text or "").strip() or None
                        else:
                            fields[tag] = (child.text or "").strip()
                    title, url = fields.get("title", ""), fields.get("link", "")
                    if not title or not url.startswith("http"):
                        continue
                    published = None
                    try:
                        published = parsedate_to_datetime(fields.get("pubDate", "")).astimezone(timezone.utc)
                    except Exception:
                        pass
                    raw.append({
                        "query": query,
                        "title": re.sub(r"\s+", " ", title).strip(),
                        "url": url,
                        "publisher": source,
                        "published_at": published,
                        "snippet": re.sub(r"<[^>]+>", " ", fields.get("description", ""))[:1600] or None,
                    })
        unique = {}
        for item in raw:
            key = item["url"]
            unique.setdefault(key, item)
        items = list(unique.values())
        for item in items:
            publishers = {
                (other.get("publisher") or urlparse(other["url"]).hostname or "unknown").casefold()
                for other in items if _similar_title(item["title"], other["title"])
            }
            item["cross_source_hits"] = max(1, len(publishers))
        ranked = []
        for index, item in enumerate(items):
            age = _age_days(item["published_at"], now)
            fresh = _freshness(age, 14)
            recurrence = min(1.0, (item["cross_source_hits"] - 1) / 4.0)
            score = round(100 * (0.62 * fresh + 0.38 * recurrence), 1)
            confidence: Literal["high", "medium", "low"] = (
                "high" if item["cross_source_hits"] >= 3 and item["published_at"] else
                "medium" if item["published_at"] else "low"
            )
            ranked.append(ArticleCandidate(
                source_id=f"article:{index + 1}",
                url=item["url"],
                query=item["query"],
                title=item["title"],
                publisher=item["publisher"],
                published_at=item["published_at"],
                age_days=age,
                snippet=item["snippet"],
                cross_source_hits=item["cross_source_hits"],
                trend_score=score,
                evidence_confidence=confidence,
            ))
        ranked.sort(key=lambda x: (x.trend_score, x.cross_source_hits), reverse=True)
        return ranked[:max_candidates]


class PublicArticleScout:
    """Blend fresh news with evergreen search; either source may fail independently."""
    def __init__(self, *, timeout_seconds: float = 20.0):
        self.news = GoogleNewsPublicScout(timeout_seconds=timeout_seconds)
        self.web = DuckDuckGoPublicArticleScout(timeout_seconds=timeout_seconds)

    async def collect(self, queries: list[str], max_candidates: int = 16) -> list[ArticleCandidate]:
        results = await asyncio.gather(
            self.news.collect(queries, max_candidates=max_candidates),
            self.web.collect(queries, max_candidates=max_candidates),
            return_exceptions=True,
        )
        combined: list[ArticleCandidate] = []
        for result in results:
            if not isinstance(result, Exception):
                combined.extend(result)
        deduped: dict[str, ArticleCandidate] = {}
        for item in combined:
            key = str(item.url)
            existing = deduped.get(key)
            if existing is None or item.trend_score > existing.trend_score:
                deduped[key] = item
        items = list(deduped.values())
        # Topic recurrence across independent publishers is useful for both news
        # and evergreen results. It is a content signal, not a demand metric.
        for index, item in enumerate(items):
            publishers = {
                (other.publisher or (urlparse(str(other.url)).hostname or "unknown")).casefold()
                for other in items
                if _similar_title(item.title, other.title)
            }
            hits = max(item.cross_source_hits, len(publishers))
            recurrence_bonus = min(25.0, max(0, hits - 1) * 8.0)
            items[index] = item.model_copy(update={
                "cross_source_hits": hits,
                "trend_score": min(100.0, round(item.trend_score + recurrence_bonus, 1)),
                "evidence_confidence": (
                    "high" if hits >= 3 else
                    "medium" if item.evidence_confidence != "low" or hits >= 2 else "low"
                ),
            })
        items.sort(key=lambda x: (x.trend_score, x.cross_source_hits), reverse=True)
        return items[:max_candidates]


async def _youtube_watch_metadata(client: httpx.AsyncClient, candidate: ViralVideoCandidate) -> tuple[str | None, datetime | None]:
    video_id = candidate.source_id.removeprefix("yt:")
    try:
        response = await client.get("https://www.youtube.com/watch", params={"v": video_id, "hl": "ru"})
        response.raise_for_status()
    except httpx.HTTPError:
        return None, None
    player = _extract_json_object(
        response.text,
        ("var ytInitialPlayerResponse =", "ytInitialPlayerResponse =", 'window["ytInitialPlayerResponse"] ='),
    ) or {}
    channel_id = ((player.get("videoDetails") or {}).get("channelId"))
    date_text = (((player.get("microformat") or {}).get("playerMicroformatRenderer") or {}).get("publishDate"))
    published = None
    if isinstance(date_text, str):
        try:
            published = datetime.fromisoformat(date_text).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return channel_id if isinstance(channel_id, str) else None, published


async def _creator_short_baseline(client: httpx.AsyncClient, channel_id: str, candidate_id: str) -> int | None:
    try:
        response = await client.get(f"https://www.youtube.com/channel/{channel_id}/shorts", params={"hl": "ru"})
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    rows = parse_youtube_search_page(response.text, "creator_recent_shorts", discovery_mode="general_search")
    views = [
        row.view_count for row in rows
        if row.is_short and row.view_count is not None and row.source_id != candidate_id
    ][:12]
    if len(views) < 3:
        return None
    return int(median(views))


async def rank_youtube_candidates(candidates: list[ViralVideoCandidate], timeout_seconds: float = 20.0) -> list[RankedVideoCandidate]:
    now = datetime.now(timezone.utc)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AiGrowthOS/1.0; +https://github.com/)",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
    }
    top = candidates[:10]
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True, headers=headers) as client:
        metadata = await asyncio.gather(*(_youtube_watch_metadata(client, c) for c in top), return_exceptions=True)
        baseline_tasks = []
        normalized_meta = []
        for c, item in zip(top, metadata):
            channel_id, published_at = item if isinstance(item, tuple) else (None, None)
            normalized_meta.append((channel_id, published_at))
            baseline_tasks.append(
                _creator_short_baseline(client, channel_id, c.source_id)
                if channel_id and c.is_short else asyncio.sleep(0, result=None)
            )
        baselines = await asyncio.gather(*baseline_tasks, return_exceptions=True)

    ranked: list[RankedVideoCandidate] = []
    for c, meta, baseline in zip(top, normalized_meta, baselines):
        channel_id, published_at = meta
        baseline_value = baseline if isinstance(baseline, int) else None
        outlier = (
            round(c.view_count / baseline_value, 2)
            if c.view_count is not None and baseline_value and baseline_value > 0 else None
        )
        age = _age_days(published_at, now)
        score, confidence = _video_score(
            views=c.view_count,
            is_short=c.is_short,
            outlier_ratio=outlier,
            age_days=age,
            transcript=bool(c.transcript_excerpt),
        )
        ranked.append(RankedVideoCandidate(
            source_id=c.source_id,
            url=c.url,
            query=c.query,
            title=c.title,
            channel=c.channel,
            view_count=c.view_count,
            published_text=c.published_text,
            is_short=c.is_short,
            transcript_excerpt=c.transcript_excerpt,
            channel_id=channel_id,
            published_at=published_at,
            age_days=age,
            channel_baseline_median_views=baseline_value,
            outlier_ratio=outlier,
            viral_score=score,
            evidence_confidence=confidence,
        ))
    ranked.sort(
        key=lambda x: (
            x.outlier_ratio is not None,
            x.outlier_ratio or 0,
            x.viral_score,
            x.view_count or 0,
        ),
        reverse=True,
    )
    return ranked


def _approved_facts(context: dict) -> dict[str, str]:
    profile = ((context.get("company") or {}).get("profile") or {})
    proof = profile.get("proof") or {}
    facts = {}
    for index, item in enumerate(proof.get("approved_facts") or [], 1):
        statement = item.get("statement") if isinstance(item, dict) else None
        if isinstance(statement, str) and statement.strip():
            facts[f"profile_fact:{index}"] = statement.strip()
    return facts


def _expanded_queries(context: dict, limit: int) -> tuple[str, list[str]]:
    # Reserve part of the query budget for pain-specific discovery instead of
    # spending every query on generic niche keywords.
    base_limit = max(1, min(2, limit))
    niche, base = build_search_queries(context, base_limit)
    profile = ((context.get("company") or {}).get("profile") or {})
    customers = profile.get("customers") or {}
    segments = [x for x in customers.get("primary_segments") or [] if isinstance(x, str) and x.strip()]
    pains = [x for x in customers.get("pains") or [] if isinstance(x, str) and x.strip()]
    queries = list(base)
    for segment in segments[:2]:
        for pain in pains[:2]:
            candidate = f"{segment} {pain}"
            if candidate.casefold() not in {q.casefold() for q in queries}:
                queries.append(candidate)
            if len(queries) >= limit:
                break
        if len(queries) >= limit:
            break
    return niche, queries[:limit]


def _analysis_missing_fields(parsed: ViralContentAnalysisDraft, evidence_count: int) -> list[str]:
    if evidence_count < 6:
        return []
    missing = []
    if len(parsed.audience_pains) < 2:
        missing.append("audience_pains")
    if len(parsed.hook_patterns) < 2:
        missing.append("hook_patterns")
    return missing


def _analysis_completeness_score(parsed: ViralContentAnalysisDraft) -> int:
    return sum([
        min(len(parsed.audience_pains), 2) * 4,
        min(len(parsed.hook_patterns), 2) * 3,
        min(len(parsed.topic_patterns), 2) * 2,
        min(len(parsed.article_patterns), 1) * 2,
        min(len(parsed.seo_topic_seeds), 3),
        min(len(parsed.content_angles), 3),
        min(len(parsed.white_space_angles), 2),
        min(len(parsed.humor_angles), 2) * 2,
        min(len(parsed.stepps_mechanisms), 1),
    ])


def _enrich_marketing_lists(parsed: ViralContentAnalysisDraft, niche: str) -> ViralContentAnalysisDraft:
    """Keep evidence-bound observations strict while making creative output useful."""
    data = parsed.model_dump(mode="json")

    def extend_unique(key: str, candidates: list[str], target: int):
        values = [x for x in data.get(key, []) if isinstance(x, str) and x.strip()]
        seen = {" ".join(x.casefold().split()) for x in values}
        for candidate in candidates:
            normalized = " ".join(candidate.casefold().split())
            if normalized not in seen:
                values.append(candidate)
                seen.add(normalized)
            if len(values) >= target:
                break
        data[key] = values

    clean_niche = " ".join((niche or "сервисного бизнеса").split())
    extend_unique("seo_topic_seeds", [
        f"как организовать запись клиентов в {clean_niche}",
        f"ошибки ручной записи клиентов в {clean_niche}",
        "как сократить переписку при записи клиента",
    ], 3)
    extend_unique("content_angles", [
        "Сколько ручных сообщений отделяет интерес клиента от подтверждённой записи",
        "Когда обычный чат незаметно становится CRM, календарём и диспетчерской",
        "Где клиентский путь буксует между «хочу записаться» и выбранным слотом",
    ], 3)
    extend_unique("humor_angles", [
        "Администратор как CRM на человеческой тяге: работает отлично, пока человек не занят, не спит и не отошёл от телефона.",
        "Запись как мини-квест: клиент ищет свободное окно, а владелец — сообщение, которое уже уехало вверх по чату.",
    ], 2)
    return ViralContentAnalysisDraft.model_validate(data)


def _analysis_prompt() -> str:
    return (
        "You are viral_content_researcher. Analyze ONLY the supplied evidence ledger. Return ONLY JSON. "
        "The research combines short-form video and public article/news signals. Treat view count, trend score, "
        "cross-source recurrence and creator outlier ratio as commercially useful attention/topic signals. Several "
        "independent signals may justify a marketing inference that a theme is visibly recurring or promising; do not "
        "rename that inference as measured keyword volume, conversion or total market demand unless a supplied source "
        "actually reports it. Use creator-baseline outlier_ratio when available; raw views alone are weaker. For articles, "
        "do not fabricate views: use freshness, recurrence and wording/topic structure. "
        "Find repeated audience pains, hooks, POV clusters, counterexamples and white-space angles. Apply STEPPS "
        "(social currency, triggers, emotion, public, practical value, stories) only when supported by title/snippet/"
        "transcript; do not force every category. Every observed pain/pattern must cite exact source_ids. "
        "Map pains to the supplied approved_product_facts. product_match=verified_match ONLY when the exact supplied fact "
        "directly supports the match and then product_fact_id is required. Otherwise use hypothesis or gap. "
        "Do not invent ServGo capabilities or factual statistics. Third-party numbers and case results already "
        "present in the evidence MAY be surfaced because marketing needs concrete material: preserve their source scope "
        "and never turn them into a guaranteed ServGo result. Strong marketing interpretation is welcome when it is a "
        "reasonable synthesis of several independent signals. Generate original SEO seeds and content angles rather than "
        "copying creators. Aim for 3-8 SEO seeds and content angles when evidence supports them. "
        "Identify white-space angles where the evidence supports a fresh combination. Humor is a required marketing "
        "ingredient: with a normal business ledger, produce at least two tasteful humor angles that make an operational "
        "pain recognizable; humor must punch at the situation/process, "
        "not at the customer, employee or vulnerable person. Avoid cringe, mockery and fake quotes. "
        "For presentation_angle use tension: recognizable scene -> cost/friction -> clearer mechanism -> grounded next step. "
        "Write natural Russian. Schema: " + json.dumps(ViralContentAnalysisDraft.model_json_schema(), ensure_ascii=False)
    )


async def run_viral_content_research(
    *,
    db: Session,
    settings,
    llm: LLMService,
    task: Task,
    context: dict,
    video_scout: YouTubePublicScout | None = None,
    article_scout: GoogleNewsPublicScout | None = None,
) -> Artifact:
    run = AgentRun(
        task_id=task.id,
        agent_name="viral_content_researcher",
        provider=settings.llm_provider,
        model=settings.llm_model,
        status="running",
        started_at=datetime.now(timezone.utc),
        input_references=[{"kind": "product", "id": str(task.product_id)}, {"kind": "task", "id": str(task.id)}],
    )
    db.add(run)
    db.flush()
    run_id = run.id
    db.commit()

    niche, queries = _expanded_queries(context, max(2, min(int(settings.viral_research_max_queries), 8)))
    privacy = PrivacyGateway()
    facts = _approved_facts(context)
    response = None
    analysis = None
    analysis_responses = []
    analysis_failure_code = None
    cautions = [
        "Охват и трендовые сигналы не считаются доказательством покупательского спроса или продаж.",
        "Чужие формулировки и сценарии не копируются: используются только абстрактные механики.",
    ]
    try:
        yt = video_scout or YouTubePublicScout(timeout_seconds=float(settings.viral_research_timeout_seconds))
        news = article_scout or PublicArticleScout(timeout_seconds=float(settings.viral_research_timeout_seconds))
        source_timeout = max(15.0, float(settings.viral_research_timeout_seconds) * 2.5)
        collected = await asyncio.gather(
            asyncio.wait_for(
                yt.collect(queries, max_candidates=int(settings.viral_research_max_candidates)),
                timeout=source_timeout,
            ),
            asyncio.wait_for(
                news.collect(queries, max_candidates=int(settings.viral_research_max_candidates)),
                timeout=source_timeout,
            ),
            return_exceptions=True,
        )
        raw_video_result, raw_article_result = collected
        if isinstance(raw_video_result, Exception):
            video_raw = []
            cautions.append("YouTube-источник временно недоступен; исследование продолжено по статье/новостному слою.")
        else:
            video_raw = [
                ViralVideoCandidate.model_validate_json(privacy.sanitize_text(item.model_dump_json()))
                for item in raw_video_result
            ]
        if isinstance(raw_article_result, Exception):
            articles = []
            cautions.append("Статейный источник временно недоступен; исследование продолжено по видео-слою.")
        else:
            articles = [
                ArticleCandidate.model_validate_json(privacy.sanitize_text(item.model_dump_json()))
                for item in raw_article_result
            ]
        videos = []
        if video_raw:
            try:
                videos = await asyncio.wait_for(
                    rank_youtube_candidates(
                        video_raw, timeout_seconds=float(settings.viral_research_timeout_seconds)
                    ),
                    timeout=source_timeout,
                )
            except Exception:
                cautions.append(
                    "Нормализация видео относительно недавних Shorts автора не завершилась; "
                    "сохранён более слабый слой публичного охвата без outlier-baseline."
                )
                for item in video_raw[:10]:
                    score, confidence = _video_score(
                        views=item.view_count,
                        is_short=item.is_short,
                        outlier_ratio=None,
                        age_days=None,
                        transcript=bool(item.transcript_excerpt),
                    )
                    videos.append(RankedVideoCandidate(
                        source_id=item.source_id,
                        url=item.url,
                        query=item.query,
                        title=item.title,
                        channel=item.channel,
                        view_count=item.view_count,
                        published_text=item.published_text,
                        is_short=item.is_short,
                        transcript_excerpt=item.transcript_excerpt,
                        viral_score=score,
                        evidence_confidence=confidence,
                    ))
                videos.sort(key=lambda x: (x.viral_score, x.view_count or 0), reverse=True)

        strong_video = sum(
            1 for x in videos
            if x.is_short and x.view_count is not None and (
                (x.outlier_ratio is not None and x.outlier_ratio >= 1.5)
                or x.viral_score >= 70
            )
        )
        strong_article = sum(
            1 for x in articles
            if x.cross_source_hits >= 2 and x.evidence_confidence != "low"
        )
        if strong_video >= 3 and strong_article >= 3:
            quality = "strong_multisource"
        elif strong_video >= 3:
            quality = "video_led"
        elif strong_article >= 3:
            quality = "article_led"
        elif videos or articles:
            quality = "mixed_weak"
        else:
            quality = "insufficient"
        source_status = (
            "evidence_available" if strong_video >= 3 or strong_article >= 3
            else "insufficient_evidence" if videos or articles
            else "source_unavailable"
        )
        analysis_status: Literal["completed", "not_run", "failed"] = "not_run"
        if videos or articles:
            payload = {
                "niche": niche,
                "queries": queries,
                "approved_product_facts": facts,
                "video_candidates": [x.model_dump(mode="json") for x in videos[:12]],
                "article_candidates": [x.model_dump(mode="json") for x in articles[:12]],
                "scoring_notes": {
                    "video": "creator recent Shorts median when available + reach + freshness + actual-short + transcript",
                    "article": "freshness + cross-source recurrence; no fabricated engagement",
                },
            }
            analysis_request = LLMRequest(
                system_prompt=_analysis_prompt(),
                messages=[LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False, default=str))],
                model=settings.llm_model,
                max_tokens=6500,
                response_schema=ViralContentAnalysisDraft.model_json_schema(),
                metadata={
                    "team_role": "viral_content_researcher",
                    "reasoning_effort": "low",
                    "task_type": "viral_content_research",
                    "provider_timeout_seconds": 90,
                },
            )

            allowed_sources = {x.source_id for x in videos} | {x.source_id for x in articles}

            def validate_integrity(parsed: ViralContentAnalysisDraft) -> None:
                for pattern in [*parsed.hook_patterns, *parsed.topic_patterns, *parsed.article_patterns]:
                    if set(pattern.source_ids) - allowed_sources:
                        raise ValueError("analysis referenced unknown source")
                for pain in parsed.audience_pains:
                    if set(pain.source_ids) - allowed_sources:
                        raise ValueError("pain referenced unknown source")
                    if pain.product_fact_id and pain.product_fact_id not in facts:
                        raise ValueError("pain referenced unknown product fact")

            async def generate_and_validate(active_llm, request):
                generated = await asyncio.wait_for(active_llm.generate(request), timeout=180)
                analysis_responses.append(generated)
                parsed = ViralContentAnalysisDraft.model_validate_json(generated.content)
                validate_integrity(parsed)
                parsed = _enrich_marketing_lists(parsed, niche)
                return generated, parsed

            fallback_model = next(
                (
                    item.strip()
                    for item in str(getattr(settings, "llm_fallback_models", "")).split(",")
                    if item.strip() and item.strip() != settings.llm_model
                ),
                None,
            )
            fallback_llm = None
            if getattr(settings, "app_env", "development") == "production" and fallback_model:
                from app.core.composition import build_llm_provider, SAFE_LLM_METADATA_FIELDS
                fallback_settings = settings.model_copy(update={
                    "llm_model": fallback_model,
                    "llm_fallback_models": "",
                })
                fallback_llm = LLMService(
                    privacy_gateway=PrivacyGateway(allowed_context_fields=SAFE_LLM_METADATA_FIELDS),
                    provider=build_llm_provider(fallback_settings),
                )

            try:
                response, analysis = await generate_and_validate(llm, analysis_request)
                missing = _analysis_missing_fields(analysis, len(videos) + len(articles))

                # A structurally valid but thin answer is still valuable. In production,
                # ask the configured ClaudeHub fallback to repair only the missing
                # evidence-bound blocks. If repair fails, keep the useful primary
                # analysis instead of throwing it away.
                if missing and fallback_llm is not None and fallback_model:
                    repair_payload = {
                        **payload,
                        "prior_analysis": analysis.model_dump(mode="json"),
                        "missing_fields": missing,
                    }
                    repair_request = analysis_request.model_copy(update={
                        "model": fallback_model,
                        "system_prompt": (
                            _analysis_prompt()
                            + " This is a repair pass. Preserve every valid populated field from prior_analysis. "
                            + "Fill the listed missing_fields only when the supplied evidence supports them. "
                            + "Do not delete useful topic patterns, summary, angles or cautions merely because another "
                            + "field is sparse. Return the complete repaired JSON object."
                        ),
                        "messages": [
                            LLMMessage(
                                role="user",
                                content=json.dumps(repair_payload, ensure_ascii=False, default=str),
                            )
                        ],
                    })
                    try:
                        repaired_response, repaired = await generate_and_validate(fallback_llm, repair_request)
                        if _analysis_completeness_score(repaired) >= _analysis_completeness_score(analysis):
                            response, analysis = repaired_response, repaired
                        missing = _analysis_missing_fields(analysis, len(videos) + len(articles))
                        cautions.append(
                            "Неполный первичный разбор был дополнен настроенной fallback-моделью через repair-проход."
                        )
                    except (ValidationError, ValueError, asyncio.TimeoutError) as repair_exc:
                        analysis_failure_code = "repair:" + type(repair_exc).__name__
                        cautions.append(
                            "Repair-проход не завершился; сохранён полезный валидный первичный анализ вместо его потери."
                        )

                analysis_status = "completed"
                final_missing = _analysis_missing_fields(analysis, len(videos) + len(articles))
                if final_missing:
                    cautions.append(
                        "Часть evidence-bound блоков осталась разреженной: "
                        + ", ".join(final_missing)
                        + ". Креативные SEO/content/humor углы сохранены и могут использоваться как идеи, а не как доказанные факты."
                    )
            except (ValidationError, ValueError, asyncio.TimeoutError) as exc:
                analysis_failure_code = type(exc).__name__
                # Malformed or integrity-invalid primary output cannot be salvaged.
                # Retry once through the configured ClaudeHub fallback; a valid but
                # sparse fallback remains usable rather than being discarded.
                if fallback_llm is not None and fallback_model:
                    try:
                        fallback_request = analysis_request.model_copy(update={"model": fallback_model})
                        response, analysis = await generate_and_validate(fallback_llm, fallback_request)
                        analysis_status = "completed"
                        cautions.append(
                            "Первичный структурированный разбор не прошёл проверку; evidence повторно разобран fallback-моделью."
                        )
                        final_missing = _analysis_missing_fields(analysis, len(videos) + len(articles))
                        if final_missing:
                            cautions.append(
                                "Fallback-разбор валиден, но часть evidence-bound блоков разрежена: "
                                + ", ".join(final_missing)
                                + ". Полезные маркетинговые углы сохранены."
                            )
                    except Exception as fallback_exc:
                        analysis_failure_code = (
                            type(exc).__name__ + "->" + type(fallback_exc).__name__
                        )[:120]
                if analysis is None:
                    analysis_status = "failed"
                    cautions.append(
                        "Содержательный разбор не завершился (" + analysis_failure_code
                        + "); в артефакте сохранён проверяемый evidence ledger."
                    )
        report = ViralContentResearch(
            observed_at=datetime.now(timezone.utc),
            source_status=source_status,
            analysis_status=analysis_status,
            research_quality=quality,
            niche=niche,
            search_queries=queries,
            scoring_basis=(
                "Видео: относительный выброс к медиане недавних Shorts автора, когда baseline доступен; "
                "иначе публичный охват + свежесть + полнота evidence. Статьи: свежесть и повторяемость темы "
                "у разных источников; просмотры статей не выдумываются."
            ),
            video_candidates=videos,
            article_candidates=articles,
            approved_product_facts=facts,
            summary=analysis.summary if analysis else (
                "Evidence ledger собран, но содержательный разбор недоступен."
                if videos or articles else
                "Публичные источники не дали достаточного evidence для trend/viral research."
            ),
            audience_pains=analysis.audience_pains if analysis else [],
            hook_patterns=analysis.hook_patterns if analysis else [],
            topic_patterns=analysis.topic_patterns if analysis else [],
            article_patterns=analysis.article_patterns if analysis else [],
            stepps_mechanisms=analysis.stepps_mechanisms if analysis else [],
            seo_topic_seeds=analysis.seo_topic_seeds if analysis else [],
            content_angles=analysis.content_angles if analysis else [],
            white_space_angles=analysis.white_space_angles if analysis else [],
            humor_angles=analysis.humor_angles if analysis else [],
            counterexamples=analysis.counterexamples if analysis else [],
            cautions=[*cautions, *(analysis.cautions if analysis else [])],
        )
        artifact = ArtifactService(db).create(
            product_id=task.product_id,
            task_id=task.id,
            agent_run_id=run_id,
            kind="viral_content_research",
            title="Viral/trend content research before editorial plan",
            content=report.model_dump_json(),
        )
        run = db.get(AgentRun, run_id)
        run.status = "completed"
        run.finished_at = datetime.now(timezone.utc)
        run.output_references = [{"kind": "artifact", "id": str(artifact.id)}]
        if response is not None:
            run.model = response.model
        for generated in analysis_responses:
            if generated.usage is not None:
                db.add(CostRecord(
                    product_id=task.product_id,
                    agent_run_id=run_id,
                    category="llm",
                    provider=settings.llm_provider,
                    amount=0,
                    currency="USD",
                    input_tokens=max(0, generated.usage.input_tokens),
                    output_tokens=max(0, generated.usage.output_tokens),
                    incurred_at=datetime.now(timezone.utc),
                    details={
                        "cost_known": False,
                        "task_type": "viral_content_research",
                        "model": generated.model,
                    },
                ))
        db.commit()
        return artifact
    except asyncio.CancelledError:
        db.rollback()
        run = db.get(AgentRun, run_id)
        run.status, run.error, run.finished_at = "failed", "viral_content_research_cancelled", datetime.now(timezone.utc)
        db.commit()
        raise
    except Exception as exc:
        db.rollback()
        run = db.get(AgentRun, run_id)
        run.status = "failed"
        run.error = ("viral_content_research_failed:" + type(exc).__name__)[:1000]
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        fallback = ViralContentResearch(
            observed_at=datetime.now(timezone.utc),
            source_status="source_unavailable",
            analysis_status="failed",
            research_quality="insufficient",
            niche=niche,
            search_queries=queries,
            scoring_basis="No verified public trend sample was available.",
            video_candidates=[],
            article_candidates=[],
            approved_product_facts=facts,
            summary="Viral/trend research не дал проверяемых источников; редактор не должен ссылаться на вирусность как на основание темы.",
            cautions=[*cautions, "Ошибка внешнего источника сохранена без необработанных данных."],
        )
        artifact = ArtifactService(db).create(
            product_id=task.product_id,
            task_id=task.id,
            kind="viral_content_research",
            title="Viral/trend content research unavailable",
            content=fallback.model_dump_json(),
        )
        db.commit()
        return artifact


async def run_viral_video_research(**kwargs) -> Artifact:
    """Backward-compatible entrypoint; the implementation now researches videos and articles."""
    return await run_viral_content_research(**kwargs)
