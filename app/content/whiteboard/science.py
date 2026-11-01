"""Scientific claim checks before visual media generation.

This review only uses curated scientific anchors. It never invents sources.
"""
from __future__ import annotations

import json
import re
from typing import Literal
from pydantic import Field, model_validator
from app.llm.schemas import LLMMessage, LLMRequest
from .contracts import Contract, WhiteboardRequest, WhiteboardStoryboard

SCIENCE_TERMS = re.compile(
    r"науч|физик|хими|биолог|математ|температур|молекул|атом|"
    r"электрон|квант|генет|энерги|аккумулятор|масс|"
    r"относительност|гравитац|скорост|светов|космос|"
    r"горяч|нагрет|холодн|теплот|излучени", re.I,
)


def requires_science_review(request: WhiteboardRequest) -> bool:
    return request.science_mode == "scientific" or (
        request.science_mode == "auto"
        and bool(SCIENCE_TERMS.search(request.topic + " " + request.source_script[:800]))
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


class FactFinding(Contract):
    claim: str = Field(min_length=6, max_length=320)
    verdict: Literal["supported", "contradicted", "unverified"]
    explanation: str = Field(min_length=12, max_length=600)
    reference_id: str | None = None


class FactReview(Contract):
    status: Literal["supported", "blocked"]
    findings: list[FactFinding] = Field(min_length=1, max_length=30)
    summary: str = Field(min_length=15, max_length=800)

    @model_validator(mode="after")
    def fail_closed(self):
        if self.status == "supported" and any(
            f.verdict != "supported" for f in self.findings
        ):
            raise ValueError("Unverified facts cannot pass")
        return self


def evidence_supported(review: FactReview, request: WhiteboardRequest) -> bool:
    valid = {ref["id"] for ref in reference_anchors(request)}
    return bool(valid) and review.status == "supported" and all(
        f.verdict == "supported" and f.reference_id in valid for f in review.findings
    )


class FactReviewer:
    def __init__(self, service, model: str):
        self.service, self.model = service, model

    async def review(self, storyboard: WhiteboardStoryboard, request: WhiteboardRequest) -> FactReview:
        data = {"topic": request.topic,
                "narration": [b.narration for s in storyboard.scenes for b in s.beats],
                "trusted_references": reference_anchors(request),
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
        return FactReview.model_validate_json(response.content)
