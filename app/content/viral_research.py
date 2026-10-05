"""Live viral-video research before editorial planning.

V1 uses public YouTube search/watch pages because they expose verifiable URLs,
view counts and (when available) caption tracks without customer credentials.
The workflow treats reach as a creative/topic signal, never as proof of buyer
demand or product-market fit.
"""
from __future__ import annotations

import asyncio
import html
import json
import math
import re
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlencode

import httpx
from defusedxml import ElementTree as SafeET
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.llm.schemas import LLMMessage, LLMRequest
from app.llm.service import LLMService
from app.models.domain import AgentRun, Artifact, CostRecord, Product, Task
from app.privacy import PrivacyGateway
from app.services.artifacts import ArtifactService


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ViralVideoCandidate(Contract):
    source_id: str = Field(min_length=1, max_length=80)
    platform: Literal["youtube"] = "youtube"
    url: AnyHttpUrl
    query: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    channel: str | None = Field(default=None, max_length=300)
    view_count: int | None = Field(default=None, ge=0)
    view_count_text: str | None = Field(default=None, max_length=120)
    published_text: str | None = Field(default=None, max_length=120)
    duration_text: str | None = Field(default=None, max_length=40)
    is_short: bool = False
    transcript_excerpt: str | None = Field(default=None, max_length=5000)


class ViralPattern(Contract):
    pattern: str = Field(min_length=3, max_length=1200)
    why_it_may_work: str = Field(min_length=3, max_length=1600)
    source_ids: list[str] = Field(min_length=1, max_length=8)


class ViralAnalysisDraft(Contract):
    summary: str = Field(min_length=20, max_length=4000)
    hook_patterns: list[ViralPattern] = Field(default_factory=list, max_length=10)
    topic_patterns: list[ViralPattern] = Field(default_factory=list, max_length=10)
    seo_topic_seeds: list[str] = Field(default_factory=list, max_length=15)
    content_angles: list[str] = Field(default_factory=list, max_length=15)
    cautions: list[str] = Field(default_factory=list, max_length=12)


class ViralVideoResearch(Contract):
    observed_at: datetime
    source_status: Literal["evidence_available", "insufficient_evidence", "source_unavailable"]
    analysis_status: Literal["completed", "not_run", "failed"]
    niche: str = Field(min_length=1, max_length=500)
    search_queries: list[str] = Field(min_length=1, max_length=8)
    virality_basis: str = Field(min_length=10, max_length=1200)
    candidates: list[ViralVideoCandidate] = Field(default_factory=list, max_length=24)
    summary: str = Field(min_length=10, max_length=4000)
    hook_patterns: list[ViralPattern] = Field(default_factory=list, max_length=10)
    topic_patterns: list[ViralPattern] = Field(default_factory=list, max_length=10)
    seo_topic_seeds: list[str] = Field(default_factory=list, max_length=15)
    content_angles: list[str] = Field(default_factory=list, max_length=15)
    cautions: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def grounded_patterns(self):
        allowed = {c.source_id for c in self.candidates}
        for pattern in [*self.hook_patterns, *self.topic_patterns]:
            if set(pattern.source_ids) - allowed:
                raise ValueError("Viral pattern references unknown source")
        if self.source_status == "evidence_available" and not self.candidates:
            raise ValueError("Evidence-available research needs candidates")
        return self


def _compact_text(value) -> str:
    if not isinstance(value, dict):
        return ""
    if isinstance(value.get("simpleText"), str):
        return value["simpleText"].strip()
    runs = value.get("runs")
    if isinstance(runs, list):
        return "".join(str(item.get("text", "")) for item in runs if isinstance(item, dict)).strip()
    return ""


def _extract_json_object(text: str, markers: tuple[str, ...]) -> dict | None:
    start = -1
    for marker in markers:
        pos = text.find(marker)
        if pos >= 0:
            brace = text.find("{", pos + len(marker))
            if brace >= 0 and (start < 0 or brace < start):
                start = brace
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:index + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _walk_renderers(value):
    if isinstance(value, dict):
        for key in ("videoRenderer", "reelItemRenderer"):
            renderer = value.get(key)
            if isinstance(renderer, dict):
                yield key, renderer
        for child in value.values():
            yield from _walk_renderers(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_renderers(child)


_VIEW_MULTIPLIERS = {
    "k": 1_000,
    "тыс": 1_000,
    "тыс.": 1_000,
    "m": 1_000_000,
    "млн": 1_000_000,
    "млн.": 1_000_000,
    "b": 1_000_000_000,
    "млрд": 1_000_000_000,
    "млрд.": 1_000_000_000,
}


def parse_view_count(text: str | None) -> int | None:
    if not text:
        return None
    normalized = text.casefold().replace("\u00a0", " ").replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(тыс\.?|млн\.?|млрд\.?|[kmb])?", normalized)
    if not match:
        return None
    try:
        number = float(match.group(1))
    except ValueError:
        return None
    multiplier = _VIEW_MULTIPLIERS.get(match.group(2) or "", 1)
    return int(number * multiplier)


def _duration_seconds(text: str | None) -> int | None:
    if not text or not re.fullmatch(r"\d{1,2}(?::\d{2}){1,2}", text):
        return None
    values = [int(x) for x in text.split(":")]
    seconds = 0
    for value in values:
        seconds = seconds * 60 + value
    return seconds


def parse_youtube_search_page(page: str, query: str) -> list[ViralVideoCandidate]:
    data = _extract_json_object(page, ("var ytInitialData =", "ytInitialData =", "window[\"ytInitialData\"] ="))
    if not data:
        return []
    rows: list[ViralVideoCandidate] = []
    seen: set[str] = set()
    for kind, renderer in _walk_renderers(data):
        video_id = renderer.get("videoId")
        if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id) or video_id in seen:
            continue
        title = _compact_text(renderer.get("title") or renderer.get("headline"))
        if not title:
            continue
        seen.add(video_id)
        views_text = _compact_text(renderer.get("shortViewCountText") or renderer.get("viewCountText")) or None
        duration_text = _compact_text(renderer.get("lengthText")) or None
        seconds = _duration_seconds(duration_text)
        is_short = kind == "reelItemRenderer" or (seconds is not None and seconds <= 60)
        rows.append(ViralVideoCandidate(
            source_id="yt:" + video_id,
            url=f"https://www.youtube.com/{'shorts/' if is_short else 'watch?v='}{video_id}",
            query=query,
            title=title,
            channel=_compact_text(renderer.get("ownerText") or renderer.get("shortBylineText")) or None,
            view_count=parse_view_count(views_text),
            view_count_text=views_text,
            published_text=_compact_text(renderer.get("publishedTimeText")) or None,
            duration_text=duration_text,
            is_short=is_short,
        ))
    return rows


def _profile_terms(context: dict) -> tuple[str, list[str]]:
    company = (context.get("company") or {}).get("profile") or {}
    identity = company.get("identity") or {}
    offer = company.get("offer") or {}
    customers = company.get("customers") or {}
    terms: list[str] = []
    for value in [
        identity.get("category"), identity.get("industry"),
        offer.get("category"), customers.get("ideal_customer"),
    ]:
        if isinstance(value, str) and value.strip():
            terms.append(value.strip())
    for value in customers.get("primary_segments") or []:
        if isinstance(value, str) and value.strip():
            terms.append(value.strip())
    for item in offer.get("products_services") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip():
            terms.append(item["name"].strip())
    deduped = []
    seen = set()
    for term in terms:
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(term)
    niche = deduped[0] if deduped else ((company.get("identity") or {}).get("name") or "business")
    return niche[:500], deduped[:4] or [niche]


def build_search_queries(context: dict, limit: int = 4) -> tuple[str, list[str]]:
    niche, terms = _profile_terms(context)
    queries: list[str] = []
    templates = (
        "{term} shorts",
        "{term} ошибки клиентов shorts",
        "{term} как привлечь клиентов shorts",
        "{term} автоматизация записи shorts",
    )
    for term in terms:
        for template in templates:
            query = template.format(term=term)
            if query.casefold() not in {q.casefold() for q in queries}:
                queries.append(query)
            if len(queries) >= limit:
                return niche, queries
    return niche, queries[:limit]


class YouTubePublicScout:
    def __init__(self, *, timeout_seconds: float = 20.0):
        self.timeout_seconds = timeout_seconds

    async def collect(self, queries: list[str], max_candidates: int = 16) -> list[ViralVideoCandidate]:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; AiGrowthOS/1.0; +https://github.com/)",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        }
        found: dict[str, ViralVideoCandidate] = {}
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True, headers=headers) as client:
            for query in queries:
                try:
                    response = await client.get("https://www.youtube.com/results", params={
                        "search_query": query, "hl": "ru", "gl": "RU",
                    })
                    response.raise_for_status()
                except httpx.HTTPError:
                    continue
                for candidate in parse_youtube_search_page(response.text, query):
                    current = found.get(candidate.source_id)
                    if current is None or (candidate.view_count or -1) > (current.view_count or -1):
                        found[candidate.source_id] = candidate
        ranked = sorted(
            found.values(),
            key=lambda item: (item.view_count is not None, item.view_count or -1, item.is_short),
            reverse=True,
        )[:max_candidates]
        if not ranked:
            return []
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True, headers=headers) as client:
            transcript_targets = ranked[: min(8, len(ranked))]
            transcripts = await asyncio.gather(
                *(self._transcript(client, str(item.url).rsplit("/", 1)[-1].split("v=")[-1]) for item in transcript_targets),
                return_exceptions=True,
            )
        enriched = []
        transcript_by_id = {}
        for item, transcript in zip(transcript_targets, transcripts):
            if isinstance(transcript, str) and transcript:
                transcript_by_id[item.source_id] = transcript[:5000]
        for item in ranked:
            enriched.append(item.model_copy(update={"transcript_excerpt": transcript_by_id.get(item.source_id)}))
        return enriched

    async def _transcript(self, client: httpx.AsyncClient, video_id: str) -> str | None:
        try:
            response = await client.get("https://www.youtube.com/watch", params={"v": video_id, "hl": "ru"})
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        player = _extract_json_object(
            response.text,
            ("var ytInitialPlayerResponse =", "ytInitialPlayerResponse =", "window[\"ytInitialPlayerResponse\"] ="),
        )
        if not player:
            return None
        tracks = (((player.get("captions") or {}).get("playerCaptionsTracklistRenderer") or {}).get("captionTracks") or [])
        if not tracks:
            return None
        track = next((x for x in tracks if str(x.get("languageCode", "")).startswith("ru")), tracks[0])
        base_url = html.unescape(str(track.get("baseUrl") or ""))
        if not base_url.startswith("https://"):
            return None
        try:
            transcript = await client.get(base_url)
            transcript.raise_for_status()
            root = SafeET.fromstring(transcript.content)
        except Exception:
            return None
        chunks = []
        for node in root.iter():
            if node.tag.endswith("text") and node.text:
                chunks.append(html.unescape(node.text).replace("\n", " ").strip())
        result = " ".join(x for x in chunks if x)
        return re.sub(r"\s+", " ", result).strip() or None


def _analysis_prompt() -> str:
    return (
        "You are viral_video_researcher. Analyze only the supplied public video evidence. "
        "Return ONLY JSON matching the schema. The goal is to identify reusable audience pains, "
        "topic patterns, opening-hook mechanics and content angles for an editorial/SEO plan. "
        "A high public view count is a reach signal, NOT proof of buyer demand, conversion, product-market fit "
        "or causation. Never invent video content that is absent from title/transcript_excerpt. "
        "Every pattern must cite exact source_ids from the supplied candidates. Do not copy creators' wording "
        "or scripts; abstract the mechanism and propose original topic seeds. Write in natural Russian. "
        "SEO topic seeds should be useful search/content themes, not fabricated keyword-volume claims. "
        "Schema: " + json.dumps(ViralAnalysisDraft.model_json_schema(), ensure_ascii=False)
    )


async def run_viral_video_research(
    *,
    db: Session,
    settings,
    llm: LLMService,
    task: Task,
    context: dict,
    scout: YouTubePublicScout | None = None,
) -> Artifact:
    run = AgentRun(
        task_id=task.id,
        agent_name="viral_video_researcher",
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

    niche, queries = build_search_queries(context, max(1, min(int(settings.viral_research_max_queries), 8)))
    privacy = PrivacyGateway()
    candidates: list[ViralVideoCandidate] = []
    analysis = None
    response = None
    source_status: Literal["evidence_available", "insufficient_evidence", "source_unavailable"] = "source_unavailable"
    analysis_status: Literal["completed", "not_run", "failed"] = "not_run"
    cautions = [
        "Просмотры используются как сигнал охвата и формата, а не как доказательство покупательского спроса.",
        "Паттерны нельзя копировать дословно: используем механику хука и тему, а не чужой сценарий.",
    ]
    try:
        collector = scout or YouTubePublicScout(timeout_seconds=float(settings.viral_research_timeout_seconds))
        candidates = await collector.collect(queries, max_candidates=int(settings.viral_research_max_candidates))
        candidates = [ViralVideoCandidate.model_validate(privacy.sanitize_text(c.model_dump_json())) for c in candidates]
        with_views = [c for c in candidates if c.view_count is not None]
        if candidates:
            source_status = "evidence_available" if len(with_views) >= 3 else "insufficient_evidence"
            payload = {
                "niche": niche,
                "queries": queries,
                "virality_basis": "public YouTube search discovery + public view counts; creator-baseline normalization is unavailable",
                "candidates": [c.model_dump(mode="json") for c in candidates],
            }
            try:
                response = await asyncio.wait_for(
                    llm.generate(LLMRequest(
                        system_prompt=_analysis_prompt(),
                        messages=[LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False, default=str))],
                        model=settings.llm_model,
                        max_tokens=5000,
                        response_schema=ViralAnalysisDraft.model_json_schema(),
                        metadata={"team_role": "viral_video_researcher", "reasoning_effort": "low", "task_type": "viral_video_research"},
                    )),
                    timeout=180,
                )
                analysis = ViralAnalysisDraft.model_validate_json(response.content)
                allowed = {c.source_id for c in candidates}
                for pattern in [*analysis.hook_patterns, *analysis.topic_patterns]:
                    if set(pattern.source_ids) - allowed:
                        raise ValueError("analysis referenced unknown source")
                analysis_status = "completed"
            except Exception:
                analysis_status = "failed"
                cautions.append("Автоматический разбор собранных роликов не завершился; планировщик видит только проверяемые исходные ролики.")
        else:
            source_status = "source_unavailable"
            cautions.append("Публичный поиск YouTube не вернул проверяемых роликов; темы нельзя выдавать за основанные на вирусных примерах.")

        report = ViralVideoResearch(
            observed_at=datetime.now(timezone.utc),
            source_status=source_status,
            analysis_status=analysis_status,
            niche=niche,
            search_queries=queries,
            virality_basis=(
                "Кандидаты ранжируются по публично отображаемым просмотрам в выдаче YouTube. "
                "Это не нормализованный коэффициент вирусности относительно аудитории автора."
            ),
            candidates=candidates,
            summary=analysis.summary if analysis else (
                "Собраны проверяемые кандидаты, но автоматический содержательный разбор недоступен."
                if candidates else
                "Проверяемые вирусные ролики из публичного источника не получены; контент-план должен явно считать это пробелом."
            ),
            hook_patterns=analysis.hook_patterns if analysis else [],
            topic_patterns=analysis.topic_patterns if analysis else [],
            seo_topic_seeds=analysis.seo_topic_seeds if analysis else [],
            content_angles=analysis.content_angles if analysis else [],
            cautions=[*cautions, *(analysis.cautions if analysis else [])],
        )
        artifact = ArtifactService(db).create(
            product_id=task.product_id,
            task_id=task.id,
            agent_run_id=run_id,
            kind="viral_video_research",
            title="Viral video research before editorial plan",
            content=report.model_dump_json(),
        )
        run = db.get(AgentRun, run_id)
        run.status = "completed"
        run.finished_at = datetime.now(timezone.utc)
        run.output_references = [{"kind": "artifact", "id": str(artifact.id)}]
        if response is not None:
            run.model = response.model
            if response.usage is not None:
                db.add(CostRecord(
                    product_id=task.product_id,
                    agent_run_id=run_id,
                    category="llm",
                    provider=settings.llm_provider,
                    amount=0,
                    currency="USD",
                    input_tokens=max(0, response.usage.input_tokens),
                    output_tokens=max(0, response.usage.output_tokens),
                    incurred_at=datetime.now(timezone.utc),
                    details={"cost_known": False, "task_type": "viral_video_research"},
                ))
        db.commit()
        return artifact
    except asyncio.CancelledError:
        db.rollback()
        run = db.get(AgentRun, run_id)
        run.status, run.error, run.finished_at = "failed", "viral_video_research_cancelled", datetime.now(timezone.utc)
        db.commit()
        raise
    except Exception as exc:
        db.rollback()
        run = db.get(AgentRun, run_id)
        run.status = "failed"
        run.error = ("viral_video_research_failed:" + type(exc).__name__)[:1000]
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        # Planning remains fail-open for source availability, but never fabricates viral evidence.
        fallback = ViralVideoResearch(
            observed_at=datetime.now(timezone.utc),
            source_status="source_unavailable",
            analysis_status="failed",
            niche=niche,
            search_queries=queries,
            virality_basis="No verified reach sample was available.",
            candidates=[],
            summary="Вирусный ресерч не дал проверяемых источников; редактор не должен ссылаться на вирусность как на основание тем.",
            cautions=[*cautions, "Ошибка внешнего источника сохранена без необработанных данных."],
        )
        artifact = ArtifactService(db).create(
            product_id=task.product_id,
            task_id=task.id,
            kind="viral_video_research",
            title="Viral video research unavailable",
            content=fallback.model_dump_json(),
        )
        db.commit()
        return artifact
