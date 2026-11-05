"""Small photo copies live on the server disk; Telegram is only asked once per photo."""
import asyncio
import io

import pytest
from PIL import Image

from web.api import photo_cache


def jpeg(width=1870, height=841):
    out = io.BytesIO()
    Image.effect_noise((width, height), 40).convert("RGB").save(out, format="JPEG", quality=92)
    return out.getvalue()


@pytest.fixture(autouse=True)
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVGO_PHOTO_CACHE_DIR", str(tmp_path / "cache"))
    photo_cache._locks.clear()


def test_thumbnail_is_small_webp_and_keeps_proportions():
    original = jpeg()
    small = photo_cache.make_thumbnail(original)
    image = Image.open(io.BytesIO(small))
    assert image.format == "WEBP" and max(image.size) == photo_cache.THUMB_MAX_SIDE
    assert abs(image.size[0] / image.size[1] - 1870 / 841) < 0.03
    assert len(small) < len(original) / 5


def test_telegram_is_asked_once_then_copy_comes_from_disk():
    calls = []

    async def download():
        calls.append(1)
        return jpeg(800, 600)

    async def run():
        first = await photo_cache.get_thumbnail("file-1", download)
        second = await photo_cache.get_thumbnail("file-1", download)
        return first, second

    first, second = asyncio.run(run())
    assert first == second and first and len(calls) == 1


def test_parallel_requests_for_one_photo_download_it_once():
    calls = []

    async def download():
        calls.append(1)
        await asyncio.sleep(0.05)
        return jpeg(800, 600)

    async def run():
        return await asyncio.gather(*[photo_cache.get_thumbnail("file-2", download) for _ in range(5)])

    results = asyncio.run(run())
    assert len(calls) == 1 and all(results)


def test_unreadable_original_gives_none_so_the_caller_serves_the_original():
    async def download():
        return b"not an image"

    assert asyncio.run(photo_cache.get_thumbnail("file-3", download)) is None


def test_failed_download_gives_none_and_leaves_no_partial_file(tmp_path):
    async def download():
        raise RuntimeError("telegram down")

    assert asyncio.run(photo_cache.get_thumbnail("file-4", download)) is None
    assert not list((tmp_path / "cache").glob("*"))


def test_cache_key_never_contains_the_file_id():
    path = photo_cache.thumb_path("AgACAgIAAxkDAAMu-secret-looking-id")
    assert "AgACAgIAAxkDAAMu" not in path.name and path.name.endswith("-320.webp")


def test_frontend_helper_adds_thumb_only_for_telegram_file_ids():
    source = open("web/frontend/src/pages/client/airPhotos.ts", encoding="utf-8").read()
    assert "?size=thumb" in source and "startsWith('/api/telegram-photo/')" in source


def test_endpoint_second_thumb_request_does_not_touch_telegram(monkeypatch):
    from web.api.routes import client
    asked = []

    async def fake_url(file_id):
        asked.append(file_id)
        return "https://api.telegram.org/file/botX/photos/a.jpg"

    class FakeResponse:
        content = jpeg(800, 600)
        def raise_for_status(self): pass

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return FakeResponse()

    monkeypatch.setattr(client, "_telegram_file_url", fake_url)
    monkeypatch.setattr(client, "create_photo_client", lambda: FakeClient())

    async def run():
        first = await client.get_telegram_photo("file-5", size="thumb")
        second = await client.get_telegram_photo("file-5", size="thumb")
        return first, second

    first, second = asyncio.run(run())
    assert len(asked) == 1 and first.body == second.body
    assert first.media_type == "image/webp" and "immutable" in first.headers["cache-control"]


def test_failure_log_never_contains_the_telegram_url(caplog):
    async def download():
        raise RuntimeError("Client error for url 'https://api.telegram.org/file/bot123:SECRET/photos/a.jpg'")

    with caplog.at_level("WARNING"):
        assert asyncio.run(photo_cache.get_thumbnail("file-6", download)) is None
    assert "SECRET" not in caplog.text and "api.telegram.org" not in caplog.text
