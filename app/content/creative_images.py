"""Image production for owner-approved Stories and single-image creatives.

Two explicit providers only; no automatic dual generation or silent provider retry.
PNG files live outside public static directories and are only served by owner-scoped APIs.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
from pathlib import Path
from uuid import UUID

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select

from app.content.whiteboard.adapters import ClaudeHubGeminiImageProvider
from app.models.domain import AgentRun, Artifact
from app.services.artifacts import ArtifactService

LOG = logging.getLogger(__name__)
OPENAI_MODEL = "gpt-image-2.5-sunburst"
GEMINI_MODEL = "gemini-3.1-flash-image"
LIMIT = 12_000_000
FILE_NAME = re.compile(r"^frame-(?:0[1-9]|10)\.png$")


class ImageProviderUnavailable(ValueError):
    pass


def resolve_model(settings, choice: str) -> str:
    """One provider per task; never silently switch from a selected paid model."""
    if choice not in {"auto", "gemini", "openai"}:
        raise ValueError("Unknown creative image model")
    if choice == "auto":
        choice = "gemini" if settings.xai_api_key else "openai"
    if choice == "gemini":
        if not settings.xai_api_key:
            raise ImageProviderUnavailable("Gemini: ключ ClaudeHub не настроен")
        return GEMINI_MODEL
    if not settings.openai_api_key:
        raise ImageProviderUnavailable("ChatGPT Image: OPENAI_API_KEY не настроен")
    return OPENAI_MODEL


def image_folder(root: Path, product_id: UUID, task_id: UUID) -> Path:
    return Path(root) / "_creative_images" / str(UUID(str(product_id))) / str(UUID(str(task_id)))


def image_prompt(draft, frame, number: int, total: int, ratio: str) -> str:
    return (
        "Create ONE complete professional Russian-language social-media image. "
        "Respect company identity as described, but do not invent a brand logo, "
        "endorsement, client quotation, price, statistic, product feature or result. "
        "Text, if present, must reproduce the exact Russian wording below; no extra text. "
        "Keep text readable on a phone and important details within safe margins. "
        f"Aspect ratio {ratio}. Frame {number} of {total}. "
        "Maintain a consistent artistic identity across the series. "
        f"Art direction: {draft.visual_style[:1600]}. "
        f"Scene objective: {frame.purpose}. "
        f"Visual composition: {frame.visual_prompt}. "
        f"EXACT visible words: {frame.on_screen_text}. "
        f"Scene action/context: {frame.action_or_animation}."
    )[:16000]


def png_output(raw: bytes, *, ratio: str) -> bytes:
    """Bounded decode, fit into intended ratio without stretching or cropping."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 16_000_000:
        raise ValueError("Unexpected generated image size")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width < 256 or source.height < 256 or source.width * source.height > 12_000_000:
                raise ValueError("Generated image dimensions are outside limits")
            source.load()
            image = source.convert("RGB")
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("Image model did not return a usable image") from exc
    size = (900, 1600) if ratio == "9:16" else (1024, 1024)
    target = ImageOps.pad(image, size, method=Image.Resampling.LANCZOS, color="white", centering=(.5, .5))
    buffer = io.BytesIO()
    target.save(buffer, format="PNG", optimize=True)
    output = buffer.getvalue()
    if len(output) > LIMIT:
        raise ValueError("Generated PNG exceeds the storage limit")
    return output


async def request_image(settings, model: str, prompt: str, ratio: str, *, client=None) -> bytes:
    """Call the chosen model exactly once; reject remote image URLs and malformed responses."""
    if model == GEMINI_MODEL:
        provider = ClaudeHubGeminiImageProvider(
            api_key=settings.xai_api_key, base_url=settings.xai_base_url,
            model=GEMINI_MODEL, client=client)
        response = await provider.post("images", {"model": GEMINI_MODEL, "prompt": prompt})
    elif model == OPENAI_MODEL:
        body = {"model": OPENAI_MODEL, "prompt": prompt,
                "size": "1024x1536" if ratio == "9:16" else "1024x1024",
                "quality": "medium", "output_format": "png", "n": 1}
        headers = {"Authorization": "Bearer " + settings.openai_api_key}
        if client is not None:
            response = await client.post("images/generations", json=body, headers=headers)
        else:
            async with httpx.AsyncClient(
                base_url="https://api.openai.com/v1/",
                timeout=httpx.Timeout(300, connect=15), trust_env=False,
                follow_redirects=False) as transport:
                response = await transport.post("images/generations", json=body, headers=headers)
        response.raise_for_status()
    else:
        raise ValueError("Unsupported model")
    try:
        data = response.json()["data"]
        if len(data) != 1:
            raise ValueError("Expected exactly one image")
        encoded = data[0]["b64_json"]
        if not isinstance(encoded, str) or len(encoded) > 23_000_000:
            raise ValueError("Invalid base64 image")
        raw = base64.b64decode(encoded, validate=True)
    except (KeyError, IndexError, TypeError, ValueError, base64.binascii.Error) as exc:
        raise ValueError("Image API returned invalid data") from exc
    return png_output(raw, ratio=ratio)


def generated_images(db, product_id: UUID, task_id: UUID) -> list[Artifact]:
    return list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id,
        Artifact.task_id == task_id,
        Artifact.kind == "generated_image",
    ).order_by(Artifact.created_at, Artifact.id)))


def verified_file(root: Path, product_id: UUID, task_id: UUID, record: Artifact) -> Path:
    """Only a file named by a real company-scoped generated_image artifact is servable."""
    meta = json.loads(record.content)
    filename = meta.get("filename", "")
    if not isinstance(filename, str) or not FILE_NAME.fullmatch(filename):
        raise LookupError("Invalid image asset")
    base = image_folder(root, product_id, task_id).resolve()
    path = (base / filename).resolve()
    if not path.is_relative_to(base) or not path.is_file() or path.is_symlink():
        raise LookupError("Image not found")
    if path.stat().st_size > LIMIT:
        raise LookupError("Image exceeds limit")
    return path


async def generate_images(db, settings, request, draft, *, task_id: UUID, provider=None) -> bool:
    """Persist each successful image; return false on partial or total failure.

    Never regenerate an already-stored frame in case the worker resumes after a
    transient interruption. One failure doesn't delete previously saved images.
    """
    if request.format not in {"story", "static_image"} or request.image_model == "none":
        return True
    model = resolve_model(settings, request.image_model)
    ratio = "9:16" if request.format == "story" else "1:1"
    directory = image_folder(settings.products_root, request.product_id, task_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing = {int(json.loads(a.content)["frame_number"])
                for a in generated_images(db, request.product_id, task_id)}
    completed = 0
    for frame in draft.frames:
        if frame.number in existing:
            completed += 1
            continue
        filename = f"frame-{frame.number:02d}.png"
        prompt = image_prompt(draft, frame, frame.number, len(draft.frames), ratio)
        try:
            picture = await (provider.generate(prompt, model=model, ratio=ratio)
                             if provider is not None else request_image(settings, model, prompt, ratio))
            picture = png_output(picture, ratio=ratio)
            path = directory / filename
            temporary = directory / (filename + ".partial")
            temporary.write_bytes(picture)
            temporary.replace(path)
            ArtifactService(db).create(
                product_id=request.product_id, task_id=task_id,
                kind="generated_image", title=f"Кадр {frame.number}: {draft.title[:120]}",
                content=json.dumps({
                    "frame_number": frame.number, "filename": filename,
                    "model": model, "ratio": ratio, "sha256": hashlib.sha256(picture).hexdigest(),
                    "prompt": prompt,
                }, ensure_ascii=False),
            )
            db.commit()
            completed += 1
        except Exception as exc:
            db.rollback()
            LOG.warning("Image generation failed at task=%s frame=%s: %s",
                        task_id, frame.number, type(exc).__name__)
            ArtifactService(db).create(
                product_id=request.product_id, task_id=task_id,
                kind="creative_image_error", title=f"Кадр {frame.number}: ошибка",
                content=json.dumps({"frame_number": frame.number, "code": type(exc).__name__}),
            )
            db.commit()
    return completed == len(draft.frames)
