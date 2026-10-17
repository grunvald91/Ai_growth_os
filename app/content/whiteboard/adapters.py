"""Stage adapters. OpenRouter endpoints follow its documented HTTP APIs.

No requests are made at import or construction. Provider credentials stay in
the caller's worker configuration, never in artifacts or frontend payloads.
"""
from __future__ import annotations

import base64
import json
import re
import io
import math
from urllib.parse import urlsplit
from typing import Any

import httpx

from app.content.whiteboard.contracts import (
    WhiteboardRegionPlan, WhiteboardRequest, WhiteboardScene, WhiteboardStoryboard,
)
from app.llm.schemas import LLMMessage, LLMRequest
from app.llm.service import LLMService

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def parse_storyboard(content: str) -> WhiteboardStoryboard:
    """Validate JSON or the provider's explicit Russian Scene/Beat/Reply/Icon blocks.

    This is a deterministic format conversion, never another model call. Unknown
    lines, missing fields and out-of-order blocks fail rather than being guessed.
    """
    value = content.strip()
    if value.startswith('```json\n') and value.endswith('\n```'):
        value = value[8:-4].strip()
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        pass
    else:
        return WhiteboardStoryboard.model_validate(decoded)
    scenes = []
    scene = beat = None
    for line in value.splitlines():
        line = line.strip()
        if not line:
            continue
        heading = re.fullmatch(r'Сцена\s+(\d+)\s*:\s*(.+)', line)
        if heading:
            if int(heading[1]) != len(scenes) + 1:
                raise ValueError('Storyboard scene order invalid')
            scene = {'title': heading[2], 'beats': []}
            scenes.append(scene); beat = None
            continue
        heading = re.fullmatch(r'Бит\s+(\d+)\s*:\s*(.+)', line)
        if heading and scene is not None:
            if int(heading[1]) != len(scene['beats']) + 1:
                raise ValueError('Storyboard beat order invalid')
            beat = {'label': heading[2]}
            scene['beats'].append(beat)
            continue
        field = re.fullmatch(r'(Реплика|Иконка)\s*:\s*(.+)', line)
        if field and beat is not None:
            key = 'narration' if field[1] == 'Реплика' else 'visual'
            if key in beat:
                raise ValueError('Duplicate storyboard field')
            beat[key] = field[2]
            continue
        raise ValueError('Unsupported storyboard response format')
    return WhiteboardStoryboard.model_validate({'scenes': scenes})


class LLMStoryboardProvider:
    """Reuse Growth OS's PrivacyGateway -> LLMService provider boundary."""

    def __init__(self, service: LLMService, *, model: str) -> None:
        self.service, self.model = service, model

    async def plan(self, request: WhiteboardRequest) -> WhiteboardStoryboard:
        prompt = (
            'Return exactly one JSON object: {"scenes":[{"title":"...","beats":[{"label":"...","narration":"...","visual":"..."}]}]}. '
            "Use these exact keys; no headings, prose or Markdown outside the JSON. "
            "Write a natural Russian-language whiteboard explainer. "
            "Output precisely three scenes, each with 2-6 short sequential beats. "
            "Each beat requires a short label, one spoken sentence (narration), "
            "and a precise description of one clean educational diagram (visual). "
            "Specify essential attachment points, orientation, relative position and motion direction for technical objects. "
            "Simplify shapes, never the underlying mechanism. Describe visible evidence, not abstract labels alone. "
            "Use this arc: a question, curiosity gap, visual explanation, and a final insight. The final insight must not invent an unsupported surprising fact. "
            "Use exactly two beats per scene, at most 60 spoken words across all scenes. "
            "Aim for approximately 25 seconds of narration, leaving a final hold. Avoid unsupported factual claims, "
            "personal data, URLs, and promises. Do not add markdown."
        )
        response = await self.service.generate(LLMRequest(
            model=self.model, system_prompt=prompt,
            messages=[LLMMessage(role="user", content=request.topic)],
            max_tokens=2200,
            response_schema=WhiteboardStoryboard.model_json_schema(),
        ))
        return parse_storyboard(response.content)


class OpenRouterBase:
    def __init__(self, *, api_key: str, model: str, client: httpx.AsyncClient | None = None, proxy_url: str | None = None) -> None:
        if not api_key or not model:
            raise ValueError("Explicit provider API key and model are required")
        self.key, self.model, self.client = api_key, model, client
        self.proxy_url = proxy_url

    async def post(self, endpoint: str, payload: dict[str, Any]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.key}"}
        if self.client is not None:
            response = await self.client.post(endpoint, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(
                base_url="https://openrouter.ai/api/v1/",
                # Slow providers must not abort a call that may already be billed.
                timeout=httpx.Timeout(600.0, connect=15.0),
                proxy=self.proxy_url, trust_env=False, follow_redirects=False,
            ) as client:
                response = await client.post(endpoint, json=payload, headers=headers)
        response.raise_for_status()
        return response


class OpenRouterImageProvider(OpenRouterBase):
    """Documented POST /images endpoint, PNG or JPEG must be inspected per model."""

    async def generate(self, scene: WhiteboardScene, request: WhiteboardRequest, *, corrections=()) -> bytes:
        elements = "; ".join(
            f"{index + 1}: {b.visual} (label: {b.label})"
            for index, b in enumerate(scene.beats)
        )
        prompt = (
            "Single frame of a polished educational whiteboard explainer; pure white "
            "background, dark marker outlines, flat restrained colours, no photos, "
            "no gradients. Separate non-overlapping icons; leave generous margins. "
            "Confident smooth precise linework, consistent stroke width, coherent proportions, "
            "clear silhouettes and legible Russian typography; no wobbly scribbles or malformed geometry. "
            "Simple diagrams must preserve correct attachment points, orientation and physical mechanisms "
            "described in the specification. Keep the entire objects visible and separate in narration order. "
            "All visible labels are in Russian. "
            f"Scene title: {scene.title}. Icons in narration order: {elements}"
        )
        if len(scene.beats) == 2:
            prompt += (' Title in the top 15% of the canvas. Below it, place the complete first diagram '
                       'in the left half and the complete second diagram in the right half, with a clear '
                       'white gutter. Keep every part, arrow and label inside its own diagram area.')
        if corrections:
            prompt += ('\nThe primary author accepted ONLY these necessary corrections. Redraw this same scene, '
                       'preserving topic, labels, diagram order and other correct elements: ' +
                       json.dumps([{'beat_index': i.beat_index, 'visible_defect': i.evidence,
                                    'correction': i.correction} for i in corrections], ensure_ascii=False))
        payload = {
            "model": self.model, "prompt": prompt, "aspect_ratio": request.aspect_ratio,
            "resolution": "1K", "output_format": "png", "n": 1,
        }
        # This multimodal Gemini model is served via Chat Completions. The
        # dedicated Image API currently returns 404 despite advertising it.
        # Select the transport before calling; never retry or change the model.
        if self.model == "google/gemini-3.1-flash-image":
            response = await self.post("chat/completions", {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "modalities": ["image", "text"],
                "image_config": {"aspect_ratio": request.aspect_ratio, "image_size": "1K"},
            })
            try:
                images = response.json()["choices"][0]["message"]["images"]
                if len(images) != 1:
                    raise ValueError("Expected one generated image")
                url = images[0]["image_url"]["url"]
                prefix = "data:image/png;base64,"
                if not isinstance(url, str) or not url.startswith(prefix):
                    raise ValueError("Expected inline PNG; remote image URLs are rejected")
                data = [{"b64_json": url[len(prefix):], "media_type": "image/png"}]
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError("Gemini did not return one inline PNG") from exc
        else:
            if self.model == "black-forest-labs/flux.2-pro":
                # FLUX.2 Pro advertises aspect_ratio and output_format, but
                # no resolution tier. Use its default dimensions and validate
                # the bounded 9:16 PNG in the renderer.
                payload.pop("resolution")
            response = await self.post("images", payload)
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

    async def revise(self, scene, request, corrections):
        if not corrections:
            raise ValueError('Explicit accepted corrections required')
        return await self.generate(scene, request, corrections=corrections)


IMAGE_TIMEOUT = httpx.Timeout(600.0, connect=15.0)


class ClaudeHubGeminiImageProvider(OpenRouterImageProvider):
    """Only Gemini 3.1 Flash Image, through ClaudeHub's documented Image API."""

    def __init__(self, *, api_key, base_url, model='gemini-3.1-flash-image', client=None):
        url = urlsplit(base_url)
        if (model != 'gemini-3.1-flash-image' or url.scheme != 'https' or url.hostname != 'api.claudehub.fun'
                or url.port not in (None, 443) or url.path.rstrip('/') != '/v1'
                or url.username or url.password or url.query or url.fragment):
            raise ValueError('Gemini 3.1 Flash Image at the trusted ClaudeHub API is required')
        super().__init__(api_key=api_key, model=model, client=client)
        self.base_url = base_url.rstrip('/') + '/'

    async def post(self, endpoint, payload):
        if endpoint != 'images' or payload['model'] != 'gemini-3.1-flash-image':
            raise ValueError('No alternative image model or transport is permitted')
        # ClaudeHub documents model/prompt/size, not OpenRouter's resolution/image_config.
        body = {'model': self.model, 'prompt': payload['prompt'], 'size': '1024x1792'}
        headers = {'Authorization': 'Bearer ' + self.key}
        if self.client is not None:
            response = await self.client.post('images/generations', json=body, headers=headers)
        else:
            # Gemini image generation at ClaudeHub regularly takes longer than two
            # minutes; a client-side ReadTimeout loses an image that is still billed.
            async with httpx.AsyncClient(base_url=self.base_url, timeout=IMAGE_TIMEOUT,
                    trust_env=False, follow_redirects=False) as client:
                response = await client.post('images/generations', json=body, headers=headers)
        response.raise_for_status()
        return response

    async def generate(self, scene, request, *, corrections=()):
        image = await super().generate(scene, request, corrections=corrections)
        from PIL import Image
        with Image.open(io.BytesIO(image)) as source:
            width, height = source.size
            if source.format != 'PNG' or width * height > 10_000_000:
                raise ValueError('ClaudeHub must return a bounded PNG')
            # Its documented portrait size is 1024x1792. Pad to exact 9:16,
            # preserving geometry and every source pixel, never crop/stretch objects.
            unit = math.ceil(max(width / 9, height / 16))
            target = (9 * unit, 16 * unit)
            if target[0] * target[1] > 10_000_000:
                raise ValueError('Padded image exceeds bounds')
            canvas = Image.new('RGBA', target, 'white')
            canvas.alpha_composite(source.convert('RGBA'), ((target[0]-width)//2, (target[1]-height)//2))
            output = io.BytesIO(); canvas.convert('RGB').save(output, format='PNG')
        result = output.getvalue()
        if len(result) > 12_000_000:
            raise ValueError('Padded PNG exceeds size limit')
        return result


class OpenRouterSpeechProvider(OpenRouterBase):
    """Documented POST /audio/speech. One beat per request for exact segment timing."""

    async def synthesize(self, text: str, *, language: str, voice: str) -> bytes:
        if language != "ru":
            raise ValueError("Only Russian is enabled for this pilot")
        if len(text) > 320:
            raise ValueError("Speech segment exceeds pilot limit")
        payload = {
            "model": self.model, "input": text, "voice": voice, "response_format": "mp3",
        }
        if self.model == "x-ai/grok-voice-tts-1.0":
            # xAI's built-in voices are multilingual; explicitly select Russian
            # through OpenRouter's documented provider-options passthrough.
            payload["provider"] = {"options": {"xai": {"language": "ru"}}}
        response = await self.post("audio/speech", payload)
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
            "Each rectangle must contain the ENTIRE relevant diagram, not only its caption: "
            "all parts, tails/hooks, arrows, connecting lines and labels belonging to that beat. "
            "Use the narration and visual specification to assign the whole object to its spoken beat. "
            "Include the scene heading as a separate beat-0 region. Avoid overlaps between different beats "
            "and do not reveal a later diagram during an earlier beat. Do not fabricate elements. "
            "Beats, in order: " + json.dumps([{'beat_index': i, **b.model_dump()} for i, b in enumerate(scene.beats)], ensure_ascii=False)
        )
        payload = {
            "model": self.model,
            "max_tokens": 1400,
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
