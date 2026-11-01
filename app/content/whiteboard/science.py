"""Scientific claim checks before visual media generation.

This review only uses curated scientific anchors. It never invents sources.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import Field, model_validator
from app.llm.schemas import LLMMessage, LLMRequest
from .contracts import Contract, WhiteboardRequest, WhiteboardStoryboard

SCIENCE_TERMS = re.compile(
    r"науч|физик|хими|биолог|математ|температур|молекул|атом|"
    r"электрон|квант|генет|энерги|аккумулятор|масс(?:а|ы|е|у|ой|ив)|"
    r"относительност|гравитац|скорост|светов|космос|"
    r"горяч|нагрет|холодн|теплот|излучени", re.I,
)


def requires_science_review(request: WhiteboardRequest) -> bool:
    return request.science_mode == "scientific" or (
        request.science_mode == "auto"
        and bool(SCIENCE_TERMS.search(request.topic))
    )


def reference_anchors(request: WhiteboardRequest) -> list[dict[str, str]]:
    topic = (request.topic + " " + request.source_script[:500]).casefold()
    if ("масс" in topic or "тяжел" in topic) and any(
        x in topic for x in ("горяч", "нагрет", "холод", "теплот", "энерги")
    ):
        return [
            {"id": "SEP", "url": "https://plato.stanford.edu/entries/equivME/",
             "claim": "For a closed system receiving energy, mass increases by delta E/c^2."},
            {"id": "NIST", "url": "https://physics.nist.gov/cuu/Constants/Value/c.html",
             "claim": "Speed of light in vacuum is exactly 299792458 m/s."},
        ]
    return []

# Only bounded, non-redirecting HTTPS reads from known scientific publishers.
TRUSTED_HOSTS = (
    "nist.gov", "nasa.gov", "nih.gov", "who.int", "cern.ch",
    "plato.stanford.edu", "britannica.com", "nature.com",
    "science.org", "openstax.org", "royalsociety.org",
    "aps.org", "pnas.org",
)
URLS = re.compile(r"https://[^\s<>\\\"']{1,600}")


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer"):
            self.skip = max(self.skip - 1, 0)

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.text.append(data.strip())


async def retrieved_references(request: WhiteboardRequest) -> list[dict[str, str]]:
    """Public evidence only. No private-network URLs, redirects or arbitrary downloads."""
    sources = reference_anchors(request)
    seen = {item["url"] for item in sources}
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        for raw in URLS.findall(request.reference_materials)[:5]:
            url = raw.rstrip(".,;)]")
            parsed = urlsplit(url)
            host = (parsed.hostname or "").casefold()
            if (url in seen or parsed.username or parsed.password or
                parsed.port not in (None, 443) or
                not any(host == h or host.endswith("." + h) for h in TRUSTED_HOSTS)):
                continue
            seen.add(url)
            if len(sources) >= 5:
                break
            try:
                response = await client.get(url, headers={"Accept": "text/html"})
                if (response.status_code != 200 or
                    "text/html" not in response.headers.get("content-type", "").lower() or
                    len(response.content) > 200_000):
                    continue
                parser = _HTMLText()
                parser.feed(response.text)
                excerpt = " ".join(parser.text)[:9000]
                if len(excerpt) < 100:
                    continue
                sources.append({"id": "WEB" + str(len(sources) + 1), "url": url,
                                "claim": excerpt})
            except (httpx.HTTPError, ValueError, UnicodeError):
                continue
    return sources


class FactFinding(Contract):
    claim: str = Field(min_length=6, max_length=320)
    verdict: Literal["supported", "contradicted", "unverified"]
    explanation: str = Field(min_length=12, max_length=600)
    reference_id: str | None = None


class FactReview(Contract):
    status: Literal["supported", "blocked"]
    findings: list[FactFinding] = Field(min_length=1, max_length=30)
    summary: str = Field(min_length=15, max_length=800)
    # Populated by server AFTER checking actual retrieved sources, never trusted from LLM.
    verified_source_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def fail_closed(self):
        if self.status == "supported" and any(
            f.verdict != "supported" for f in self.findings
        ):
            raise ValueError("Unverified facts cannot pass")
        return self


def evidence_supported(review: FactReview, request: WhiteboardRequest) -> bool:
    valid = set(review.verified_source_ids)
    return bool(valid) and review.status == "supported" and all(
        f.verdict == "supported" and f.reference_id in valid for f in review.findings
    )


class FactReviewer:
    def __init__(self, service, model: str):
        self.service, self.model = service, model

    async def review(self, storyboard: WhiteboardStoryboard, request: WhiteboardRequest) -> FactReview:
        sources = await retrieved_references(request)
        data = {"topic": request.topic,
                "narration": [b.narration for s in storyboard.scenes for b in s.beats],
                "trusted_references": sources,
                "owner_notes_unverified": request.reference_materials}
        prompt = (
            "Ты научный рецензент. Проверь ВСЕ фактические утверждения озвучки. "
            "Используй только trusted_references, не выдумывай источники. "
            "Каждый тезис должен иметь отдельный finding с точной цитатой. "
            "Если утверждение противоречит источнику или не подтверждено, "
            "верни status=blocked и verdict=contradicted/unverified. "
            "Не путай изменение массы и возможность измерить его обычными весами. "
            "Не включай рекламную просьбу подписаться в факты. "
            "Верни JSON status, findings, summary."
        )
        response = await self.service.generate(LLMRequest(
            model=self.model, system_prompt=prompt,
            messages=[LLMMessage(role="user", content=json.dumps(data, ensure_ascii=False))],
            max_tokens=3800, response_schema=FactReview.model_json_schema(),
        ))
        assessment = FactReview.model_validate_json(response.content)
        return assessment.model_copy(update={
            "verified_source_ids": [item["id"] for item in sources],
        })
