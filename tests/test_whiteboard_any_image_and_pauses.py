"""Any readable picture is accepted and stored as PNG; automatic retries wait a short, fixed time."""
import base64
import io
import json

import httpx
import pytest
from PIL import Image

from app.content.whiteboard.adapters import ClaudeHubGeminiImageProvider, OpenRouterImageProvider, as_png
from app.content.whiteboard.jobs import OTHER_RESUME_DELAY, PROVIDER_RESUME_DELAY, resume_delay
from test_whiteboard import request, scene


def encoded(fmt, size=(64, 112), color=(40, 90, 160), **options):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt, **options)
    return buffer.getvalue()


@pytest.mark.parametrize("fmt", ["JPEG", "WEBP", "GIF", "BMP", "TIFF"])
def test_any_readable_format_becomes_a_png(fmt):
    raw = encoded(fmt)
    png = as_png(raw)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (64, 112)
        red, green, blue = image.convert("RGB").getpixel((10, 10))
        assert abs(red - 40) < 12 and abs(green - 90) < 12 and abs(blue - 160) < 12  # lossy sources drift slightly


def test_png_passes_through_untouched_and_transparency_survives():
    raw = encoded("PNG")
    assert as_png(raw) == raw
    buffer = io.BytesIO()
    Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(buffer, format="WEBP", lossless=True)
    with Image.open(io.BytesIO(as_png(buffer.getvalue()))) as image:
        assert image.mode == "RGBA" and image.getpixel((1, 1))[3] == 0


def test_heavy_png_is_recompressed_losslessly_below_the_storage_limit():
    raw = encoded("PNG", size=(300, 300))
    padded = raw + b"\0" * 12_000_001
    again = as_png(padded)
    assert len(again) < 12_000_000
    with Image.open(io.BytesIO(again)) as image:
        assert image.size == (300, 300)


def test_data_that_is_not_an_image_says_what_arrived():
    with pytest.raises(ValueError) as caught:
        as_png(b"<html>Rate limit</html>")
    message = str(caught.value)
    assert "not a readable image" in message and "23 bytes" in message and "3c68746d6c3e" in message


@pytest.mark.asyncio
async def test_gemini_chat_answer_with_jpeg_is_accepted():
    jpeg = encoded("JPEG")
    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"images": [{"image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}]}}]})
    async with httpx.AsyncClient(base_url="https://openrouter.ai/api/v1/", transport=httpx.MockTransport(handler)) as client:
        result = await OpenRouterImageProvider(api_key="test", model="google/gemini-3.1-flash-image",
                                               client=client).generate(scene(), request())
    assert result.startswith(b"\x89PNG")


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["JPEG", "WEBP"])
async def test_claudehub_answer_in_another_format_is_padded_like_a_png(fmt):
    raw = encoded(fmt, size=(1024, 1792))
    def handler(req):
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(raw).decode(),
                                                   "media_type": f"image/{fmt.lower()}"}]})
    async with httpx.AsyncClient(base_url="https://api.claudehub.fun/v1/", transport=httpx.MockTransport(handler)) as client:
        output = await ClaudeHubGeminiImageProvider(api_key="test", base_url="https://api.claudehub.fun/v1",
                                                    client=client).generate(scene(), request())
    with Image.open(io.BytesIO(output)) as image:
        assert image.format == "PNG" and image.width * 16 == image.height * 9


def test_pause_is_five_minutes_for_provider_trouble_and_short_otherwise():
    assert PROVIDER_RESUME_DELAY == 300 and OTHER_RESUME_DELAY == 30
    assert resume_delay("provider_server_error", {}) == 300
    assert resume_delay("openrouter_unavailable", {}) == 300
    assert resume_delay("generation_failed", {"diagnostic": {"http_status": 504}}) == 300
    assert resume_delay("generation_failed", {"diagnostic": {"exception_type": "ReadTimeout"}}) == 300
    # Replies that missed our format: waiting does not help, so retry soon.
    assert resume_delay("generation_failed", {"diagnostic": {"exception_type": "ValueError"}}) == 30
    assert resume_delay("generation_failed", {"diagnostic": {"exception_type": "ValidationError"}}) == 30
    assert resume_delay("interrupted", {}) == 30


def test_the_pause_does_not_grow_with_attempts():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from app.content.whiteboard.jobs import schedule_auto_resume

    class Db:
        def add(self, row):
            pass

    task = SimpleNamespace(product_id="p", status=None)
    source = SimpleNamespace(id="s")
    payload = SimpleNamespace(visual_repair_from=None, max_api_calls=25, inherited_calls=0)
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    gaps = []
    state = {"calls": 3, "failed_stage": "image", "diagnostic": {"exception_type": "ValueError"}}
    for _ in range(5):
        assert schedule_auto_resume(Db(), task, source, state, payload, "generation_failed", attempts=5, now=now)
        gaps.append(datetime.fromisoformat(state["retry_at"]) - now)
    assert gaps == [timedelta(seconds=30)] * 5
    assert not schedule_auto_resume(Db(), task, source, state, payload, "generation_failed", attempts=5, now=now)


@pytest.mark.asyncio
async def test_storyboard_prompt_demands_complete_fields():
    from app.content.whiteboard.adapters import LLMStoryboardProvider
    from app.llm.fake import FakeLLMProvider
    from app.llm.schemas import LLMResponse
    from app.llm.service import LLMService
    from app.privacy import PrivacyGateway
    provider = FakeLLMProvider(LLMResponse(content="{}", model="m"))
    with pytest.raises(Exception):
        await LLMStoryboardProvider(LLMService(privacy_gateway=PrivacyGateway(), provider=provider),
                                    model="m").plan(request())
    text = provider.last_request.system_prompt
    assert "EXACTLY two beats" in text and "at least six words" in text and "No empty strings" in text
