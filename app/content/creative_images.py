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
from uuid import UUID, uuid4

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select

from app.content.whiteboard.adapters import ClaudeHubGeminiImageProvider
from app.models.domain import AgentRun, Artifact, Product
from app.services.artifacts import ArtifactService

LOG = logging.getLogger(__name__)
OPENAI_MODEL = "gpt-image-2.5-sunburst"
GEMINI_MODEL = "gemini-3.1-flash-image"
LIMIT = 12_000_000
FILE_NAME = re.compile(r"^frame-(?:0[1-9]|10)\.png$")
# Rendering targets are export profiles, not assumptions about remote responses.
OUTPUT_SIZES = {"9:16": (900, 1600), "4:5": (960, 1200), "1:1": (1024, 1024), "16:9": (1600, 900)}
OPENAI_REQUEST_SIZES = {"9:16": "1008x1792", "4:5": "1024x1280",
                        "1:1": "1024x1024", "16:9": "1792x1008"}
GEMINI_REQUEST_SIZES = {"9:16": "1024x1792", "4:5": "1024x1024",
                        "1:1": "1024x1024", "16:9": "1024x1024"}


def image_aspect_ratio(fmt: str, platform: str, *, requested: str | None = None) -> str:
    """Respect explicit user composition; platform defaults apply otherwise."""
    if fmt == "story":
        if requested and requested != "9:16":
            raise ValueError("Stories are always vertical 9:16")
        return "9:16"
    if fmt != "static_image":
        raise ValueError("Only image materials have an image aspect ratio")
    if requested:
        if requested not in OUTPUT_SIZES:
            raise ValueError("Unsupported image aspect ratio")
        return requested
    return "4:5" if platform == "instagram_reels" else "1:1"


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


def image_prompt(draft, frame, number: int, total: int, ratio: str, *,
                 branded: bool = False) -> str:
    if branded:
        return (
            "Create ONLY a photographic or illustrated 9:16 background scene, "
            "not a finished Stories layout. Absolutely NO words, lettering, "
            "numbers, watermarks, logos, fake logos, UI or typography. "
            "Keep the center and safe areas uncluttered for subsequent "
            "programmatic text and the company's real logo. "
            f"Frame {number} of {total}, maintain consistent colors across scenes. "
            f"Art direction: {draft.visual_style[:1600]}. "
            f"Subject: {frame.purpose}. Scene: {frame.visual_prompt}. "
            f"Additional visual context: {frame.action_or_animation}."
        )[:16000]
    return (
        (f"Create ONE finished vertical 9:16 Stories screen for {draft.platform}. "
         "Do not create a horizontal banner. "
         if draft.format == "story" else
         f"Create ONE finished social-media post image for {draft.platform}. ")
        + "Respect company identity as described, but do not invent a brand logo, "
        "endorsement, client quotation, price, statistic, product feature or result. "
        "Text, if present, must reproduce the exact Russian wording below; no extra text. "
        "Keep text readable on a phone and important details within safe margins. "
        f"Aspect ratio {ratio}. Compose for the final full-frame layout; no arbitrary dimensions. "
        f"Frame {number} of {total}. "
        "Maintain a consistent artistic identity across the series. "
        f"Art direction: {draft.visual_style[:1600]}. "
        f"Scene objective: {frame.purpose}. "
        f"Visual composition: {frame.visual_prompt}. "
        f"EXACT visible words: {frame.on_screen_text}. "
        f"Scene action/context: {frame.action_or_animation}."
    )[:16000]


def png_output(raw: bytes, *, ratio: str, branded: bool = False,
               preserve_source: bool = False) -> bytes:
    """For paid backgrounds preserve native pixels; crop only in Story renderer.

    Legacy image deliverables keep the original sizing and padding behavior.
    """
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 16_000_000:
        raise ValueError("Unexpected generated image size")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width < 256 or source.height < 256 or source.width * source.height > 12_000_000:
                raise ValueError("Generated image dimensions are outside limits")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("Image model did not return a usable image") from exc
    try:
        size = OUTPUT_SIZES[ratio]
    except KeyError as exc:
        raise ValueError("Unsupported image aspect ratio") from exc
    target = (image if preserve_source else
              ImageOps.fit(image, size, method=Image.Resampling.LANCZOS)
              if branded else
              ImageOps.pad(image, size, method=Image.Resampling.LANCZOS,
                           color="white", centering=(.5, .5)))
    buffer = io.BytesIO()
    target.save(buffer, format="PNG", optimize=True)
    output = buffer.getvalue()
    if len(output) > LIMIT:
        raise ValueError("Generated PNG exceeds the storage limit")
    return output


async def request_image(settings, model: str, prompt: str, ratio: str, *,
                        client=None, preserve_source: bool = False) -> bytes:
    """Call the chosen model exactly once; reject remote image URLs and malformed responses."""
    if model == GEMINI_MODEL:
        provider = ClaudeHubGeminiImageProvider(
            api_key=settings.xai_api_key, base_url=settings.xai_base_url,
            model=GEMINI_MODEL, client=client)
        response = await provider.post("images", {
            "model": GEMINI_MODEL, "prompt": prompt,
            "size": GEMINI_REQUEST_SIZES[ratio],
        })
    elif model == OPENAI_MODEL:
        body = {"model": OPENAI_MODEL, "prompt": prompt,
                "size": OPENAI_REQUEST_SIZES[ratio],
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
        item = data[0]
        if not isinstance(item, dict):
            raise ValueError("Expected image object")
        # Accept base64 or a data URI containing PNG/JPEG/WebP/etc. Reject external
        # URLs: downloading arbitrary provider URLs would add SSRF risk.
        encoded = item.get("b64_json")
        if encoded is None:
            image_url = item.get("image_url")
            uri = item.get("url") or (
                image_url.get("url") if isinstance(image_url, dict) else image_url)
            if not isinstance(uri, str) or not uri.startswith("data:image/"):
                raise ValueError("Image API did not return inline image data")
            media, delimiter, encoded = uri.partition(",")
            if delimiter != "," or ";base64" not in media:
                raise ValueError("Expected inline base64 image")
        if not isinstance(encoded, str) or len(encoded) > 23_000_000:
            raise ValueError("Invalid base64 image")
        raw = base64.b64decode(encoded, validate=True)
    except (KeyError, IndexError, TypeError, base64.binascii.Error) as exc:
        raise ValueError("Image API returned invalid data") from exc
    return png_output(raw, ratio=ratio, preserve_source=preserve_source)


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


class PaidImageOutcomeUnknown(ValueError):
    """Provider may have charged a background frame; human resolution required."""


def _frame_intents(db, product_id: UUID, task_id: UUID,
                   number: int) -> list[Artifact]:
    """Read all durable paid attempts including after worker restarts."""
    rows = db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id == task_id,
        Artifact.kind == "creative_image_intent"))
    result = []
    for row in rows:
        try:
            if int(json.loads(row.content)["frame_number"]) == number:
                result.append(row)
        except (KeyError, ValueError, TypeError):
            raise ValueError("Не удалось проверить журнал платной генерации") from None
    return result


def _mark_image_intent_complete(intent: Artifact, *, digest: str) -> None:
    meta = json.loads(intent.content)
    meta["state"] = "completed"
    meta["sha256"] = digest
    intent.content = json.dumps(meta, ensure_ascii=False)


async def generate_images(db, settings, request, draft, *, task_id: UUID, provider=None) -> bool:
    """Persist each successful image; return false on partial or total failure.

    Never regenerate an already-stored frame in case the worker resumes after a
    transient interruption. One failure doesn't delete previously saved images.
    """
    if request.format not in {"story", "static_image"} or request.image_model == "none":
        return True
    model = resolve_model(settings, request.image_model)
    ratio = image_aspect_ratio(request.format, request.platform,
                               requested=getattr(request, "aspect_ratio", None))
    directory = image_folder(settings.products_root, request.product_id, task_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing = {}
    branded = getattr(request, "brand_mode", "classic") == "brand"
    palette_hint = ""
    if branded:
        from app.content.brand_story_pipeline import validate_pinned_request
        from app.brand_kit.contracts import BrandStyle
        version = validate_pinned_request(db, request, task_id)
        style = BrandStyle.model_validate(version.config)
        palette = style.palette
        palette_hint = (
            "Company visual identity MUST inform the background color treatment. "
            f"Primary {palette.primary}; secondary {palette.secondary}; "
            f"accent {palette.accent}; base background {palette.background}; "
            f"visual character {style.visual_character}. "
            "Background-only: never draw text, logos, letters or glyphs."
        )
    for record in generated_images(db, request.product_id, task_id):
        metadata = json.loads(record.content)
        number = int(metadata["frame_number"])
        if number in existing or number not in {frame.number for frame in draft.frames}:
            raise ValueError("Duplicate or unexpected creative image checkpoint")
        # A DB record is NOT proof that a paid image file survived the crash.
        saved = verified_file(settings.products_root, request.product_id, task_id, record)
        if hashlib.sha256(saved.read_bytes()).hexdigest() != metadata["sha256"]:
            raise ValueError("Creative image checkpoint checksum mismatch")
        if metadata.get("model") != model or metadata.get("ratio") != ratio:
            raise ValueError("Creative image checkpoint model or aspect changed")
        existing[number] = record
    completed = 0
    for frame in draft.frames:
        if frame.number in existing:
            completed += 1
            continue
        filename = f"frame-{frame.number:02d}.png"
        intent = None
        prompt = image_prompt(draft, frame, frame.number, len(draft.frames), ratio,
                              branded=branded)
        if branded:
            prompt = (prompt + " " + palette_hint)[:16000]
        try:
            # A process may crash after the atomic file rename but before the DB
            # commit. Reconcile that file without requesting/billing the model again.
            path = directory / filename
            if path.exists() or path.is_symlink():
                if path.is_symlink() or not path.is_file() or path.stat().st_size > LIMIT:
                    raise ValueError("Unsafe unrecorded creative image file")
                picture = path.read_bytes()
                with Image.open(io.BytesIO(picture)) as cached_image:
                    cached_image.load()
                    if (cached_image.format != "PNG" or
                            cached_image.size != OUTPUT_SIZES[ratio]):
                        raise ValueError("Unrecorded creative image file failed validation")
            else:
                if branded:
                    # Persist before contacting the provider. On timeout, a
                    # charge may have happened even though no PNG arrived.
                    db.scalar(select(Product.id).where(
                        Product.id == request.product_id).with_for_update())
                    if _frame_intents(db, request.product_id, task_id, frame.number):
                        raise PaidImageOutcomeUnknown(
                            "Исход платной генерации неизвестен; "
                            "автоматический повтор запрещён")
                    intent = ArtifactService(db).create(
                        product_id=request.product_id, task_id=task_id,
                        kind="creative_image_intent",
                        title=f"Платная генерация фона · кадр {frame.number}",
                        content=json.dumps({
                            "token": str(uuid4()), "frame_number": frame.number,
                            "model": model, "ratio": ratio,
                            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                            "state": "pending",
                        }, ensure_ascii=False))
                    db.commit()
                picture = await (provider.generate(prompt, model=model, ratio=ratio)
                                 if provider is not None else request_image(
                                     settings, model, prompt, ratio,
                                     preserve_source=branded))
                picture = png_output(picture, ratio=ratio, preserve_source=branded)
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
            if branded:
                # A file recovered after rename-before-DB crash also finalizes
                # its original intent without a second paid API call.
                if intent is None:
                    pending = _frame_intents(
                        db, request.product_id, task_id, frame.number)
                    intent = pending[-1] if pending else None
                if intent is not None:
                    _mark_image_intent_complete(
                        intent, digest=hashlib.sha256(picture).hexdigest())
            db.commit()
            completed += 1
        except Exception as exc:
            db.rollback()
            LOG.warning("Image generation failed at task=%s frame=%s: %s",
                        task_id, frame.number, type(exc).__name__)
            ArtifactService(db).create(
                product_id=request.product_id, task_id=task_id,
                kind="creative_image_error", title=f"Кадр {frame.number}: ошибка",
                content=json.dumps({
                    "frame_number": frame.number, "code": type(exc).__name__,
                    "stage": "creative_image", "model": model,
                    "http_status": (exc.response.status_code
                                    if isinstance(exc, httpx.HTTPStatusError) else None),
                }),
            )
            db.commit()
    return completed == len(draft.frames)
