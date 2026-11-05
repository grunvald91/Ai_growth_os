"""Small copies of Telegram photos kept on the server disk.

Lists and cards only need a small picture. It is cut once from the Telegram original and then
served from disk, so the usual page never waits for Telegram. Full-size photos are still
fetched from Telegram on demand by the caller. Losing the cache folder is harmless: copies
are made again on the next request.
"""
import asyncio
import hashlib
import io
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

THUMB_MAX_SIDE = 320  # 68 px card at 3x pixel density is 204 px; this leaves headroom
THUMB_QUALITY = 78
MAX_ORIGINAL_BYTES = 20 * 1024 * 1024  # Telegram's own download limit
_locks: dict[str, asyncio.Lock] = {}


def cache_dir() -> Path:
    wanted = Path(os.getenv("SERVGO_PHOTO_CACHE_DIR", "/var/lib/servgo-photo-cache"))
    for candidate in (wanted, Path(tempfile.gettempdir()) / "servgo-photo-cache"):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            if os.access(candidate, os.W_OK):
                return candidate
        except OSError:
            continue
    raise OSError("no writable photo cache folder")


def thumb_path(file_id: str) -> Path:
    digest = hashlib.sha256(file_id.encode("utf-8")).hexdigest()
    return cache_dir() / f"{digest}-{THUMB_MAX_SIDE}.webp"


def make_thumbnail(original: bytes) -> bytes:
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(original)) as source:
        image = ImageOps.exif_transpose(source)
        image.thumbnail((THUMB_MAX_SIDE, THUMB_MAX_SIDE))
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        out = io.BytesIO()
        image.save(out, format="WEBP", quality=THUMB_QUALITY, method=4)
        return out.getvalue()


async def get_thumbnail(file_id: str, download) -> bytes | None:
    """Cached small copy, or cut it from `download()` (async, returns original bytes or None).

    Returns None when no small copy can be made; the caller then serves the original."""
    try:
        path = thumb_path(file_id)
    except OSError:
        logger.warning("photo_cache_unavailable")
        return None
    try:
        return path.read_bytes()
    except FileNotFoundError:
        pass
    lock = _locks.setdefault(path.name, asyncio.Lock())
    async with lock:
        try:
            return path.read_bytes()  # another request made it while we waited
        except FileNotFoundError:
            pass
        try:
            original = await download()
            if not original or len(original) > MAX_ORIGINAL_BYTES:
                return None
            small = await asyncio.to_thread(make_thumbnail, original)
            temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
            temporary.write_bytes(small)
            os.replace(temporary, path)
            return small
        except Exception as exc:
            # No traceback or message: httpx errors carry the Telegram URL with the bot token.
            logger.warning("photo_thumbnail_failed: %s", type(exc).__name__)
            return None
        finally:
            _locks.pop(path.name, None)
