"""Stage adapters. OpenRouter endpoints follow its documented HTTP APIs.

No requests are made at import or construction. Provider credentials stay in
the caller's worker configuration, never in artifacts or frontend payloads.
"""
from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from app.content.whiteboard.contracts import (
    WhiteboardRegionPlan, WhiteboardRequest, WhiteboardScene, WhiteboardStoryboard,
)
from app.llm.schemas import LLMMessage, LLMRequest
from app.llm.service import LLMService

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class LLMStoryboardProvider:
    """Reuse Growth OS's PrivacyGateway -> LLMService provider boundary."""

    def __init__(self, service: LLMService, *, model: str) -> None:
        self.service, self.model = service, model

    async def plan(self, request: WhiteboardRequest) -> WhiteboardStoryboard:
        prompt = (
            "Write a natural Russian-language whiteboard explainer. "
            "Output precisely three scenes, each with 2-6 short sequential beats. "
            "Each beat requires a short label, one spoken sentence (narration), "
            "and a description of one simple isolated hand-drawn icon (visual). "
            "Use this arc: a question, curiosity gap, visual explanation, and a final insight. The final insight must not invent an unsupported surprising fact. "
            "Aim for approximately 30 seconds total. Avoid unsupported factual claims, "
            "personal data, URLs, and promises. Do not add markdown."
        )
        response = await self.service.generate(LLMRequest(
            model=self.model, system_prompt=prompt,
            messages=[LLMMessage(role="user", content=request.topic)],
            max_tokens=2200,
            response_schema=WhiteboardStoryboard.model_json_schema(),
        ))
        return WhiteboardStoryboard.model_validate_json(response.content)


class OpenRouterBase:
    def __init__(self, *, api_key: str, model: str, client: httpx.AsyncClient | None = None) -> None:
        if not api_key or not model:
            raise ValueError("Explicit provider API key and model are required")
        self.key, self.model, self.client = api_key, model, client

    async def post(self, endpoint: str, payload: dict[str, Any]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.key}"}
        if self.client is not None:
            response = await self.client.post(endpoint, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(
                base_url="https://openrouter.ai/api/v1/",
                timeout=httpx.Timeout(120.0),
            ) as client:
                response = await client.post(endpoint, json=payload, headers=headers)
        response.raise_for_status()
        return response


class OpenRouterImageProvider(OpenRouterBase):
    """Documented POST /images endpoint, PNG or JPEG must be inspected per model."""

    async def generate(self, scene: WhiteboardScene, request: WhiteboardRequest) -> bytes:
        elements = "; ".join(
            f"{index + 1}: {b.visual} (label: {b.label})"
            for index, b in enumerate(scene.beats)
        )
        prompt = (
            "Single frame of a clean hand-drawn whiteboard explainer; pure white "
            "background, dark marker outlines, flat restrained colours, no photos, "
            "no gradients. Separate non-overlapping icons; leave generous margins. "
            "All visible labels are in Russian. "
            f"Scene title: {scene.title}. Icons in narration order: {elements}"
        )
        response = await self.post("images", {
            "model": self.model, "prompt": prompt, "aspect_ratio": request.aspect_ratio,
            "resolution": "1K", "output_format": "png", "n": 1,
        })
        data = response.json().get("data") or []
        if len(data) != 1 or not isinstance(data[0].get("b64_json"), str):
            raise ValueError("Image API did not return one base64 image")
        if data[0].get("media_type") not in (None, "image/png"):
            raise ValueError("Expected PNG: select a PNG-capable model/provider")
        try:
            image = base64.b64decode(data[0]["b64_json"], validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ValueError("Image API returned invalid base64") from exc
        if len(image) > 12_000_000 or not image.startswith(PNG_SIGNATURE):
            raise ValueError("Image API response is not a valid-sized PNG")
        return image


class OpenRouterSpeechProvider(OpenRouterBase):
    """Documented POST /audio/speech. One beat per request for exact segment timing."""

    async def synthesize(self, text: str, *, language: str, voice: str) -> bytes:
        if language != "ru":
            raise ValueError("Only Russian is enabled for this pilot")
        if len(text) > 320:
            raise ValueError("Speech segment exceeds pilot limit")
        response = await self.post("audio/speech", {
            "model": self.model, "input": text, "voice": voice, "response_format": "mp3",
        })
        audio = response.content
        if not (100 <= len(audio) <= 10_000_000):
            raise ValueError("TTS returned empty or oversized audio")
        if not (audio.startswith(b"ID3") or (audio[0] == 0xFF and audio[1] & 0xE0 == 0xE0)):
            raise ValueError("TTS did not return an MP3 stream")
        return audio


class OpenRouterRegionProvider(OpenRouterBase):
    """Image understanding to map each visible element to one spoken beat."""

    async def detect(self, scene: WhiteboardScene, image_png: bytes) -> WhiteboardRegionPlan:
        prompt = (
            "Return JSON object with 'regions': one or more rectangles for each beat. "
            "Each rectangle has beat_index (0-based) and box [xmin,ymin,xmax,ymax] "
            "in normalized 0..1000 image coordinates. "
            "Identify isolated visible icons/labels, avoid overlaps where possible. "
            "Do not fabricate elements. Beat labels, in order: "
            + "; ".join(f"{i}: {b.label}" for i, b in enumerate(scene.beats))
        )
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,"
                        + base64.b64encode(image_png).decode("ascii")}},
                ],
            }],
            "response_format": {"type": "json_object"},
        }
        response = await self.post("chat/completions", payload)
        try:
            content = response.json()["choices"][0]["message"]["content"]
            regions = WhiteboardRegionPlan.model_validate_json(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("Vision provider returned invalid region JSON") from exc
        regions.check_for(scene)
        return regions
