"""Validated VK post image + provider upload: image -> VK wall attachment.

Failures before wall.post never fall back to text-only posting. Only VK-owned
HTTPS upload destinations are accepted (VK returns signed transient URLs).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from sqlalchemy import select

from app.content.brand_story_pipeline import verified_gallery_image
from app.content.vk_image_critic import verified_vk_image_approval
from app.models.domain import Artifact


class VKPhotoUploadError(ValueError):
    """No call to wall.post has happened; safe to mark as not sent."""


def prepared_image(db, products_root: Path | None, post: Artifact) -> tuple[Artifact, Path] | None:
    """Return the exact file approved by the visual critic or fail closed.

    A missing/changed/bypassed image is never silently omitted from the post.
    The caller must use this at scheduling time and again before sending.
    """
    try:
        data = json.loads(post.content)
        requested = (data.get("image_model", "none") != "none"
                     or bool(data.get("image_artifact_id")))
        if not requested:
            return None
        if products_root is None or post.task_id is None or not data.get("image_sha256"):
            raise ValueError("Пост содержит иллюстрацию: для публикации нужна её проверенная версия")
        image_id = UUID(str(data["image_artifact_id"]))
        image = db.scalar(select(Artifact).where(
            Artifact.id == image_id,
            Artifact.product_id == post.product_id,
            Artifact.task_id == post.task_id,
            Artifact.kind == "generated_image",
        ))
        if image is None or not verified_vk_image_approval(db, post, image):
            raise ValueError("Критик не подтвердил изображение для этого текста")
        path = verified_gallery_image(
            db, products_root, post.product_id, post.task_id, image,
        )
        if hashlib.sha256(path.read_bytes()).hexdigest() != data["image_sha256"]:
            raise ValueError("Файл изображения изменился после согласования")
        return image, path
    except (KeyError, TypeError, OSError, LookupError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(("Критик", "Файл", "Пост содержит иллюстрацию")):
            raise
        raise ValueError("Иллюстрация повреждена или отсутствует подтверждение критика") from None


def _upload_destination(url: str) -> str:
    """Prevent an upstream upload_url becoming an SSRF/exfiltration endpoint."""
    if not isinstance(url, str) or len(url) > 4096:
        raise VKPhotoUploadError("ВК вернул неверный адрес загрузки фотографии")
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in {"pu.vk.com", "pu.vk.ru"}
                or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.fragment
                or not parsed.path.startswith("/")):
            raise ValueError()
    except ValueError:
        raise VKPhotoUploadError("ВК вернул неподтверждённый адрес загрузки фотографии") from None
    return url


async def upload_wall_photo(adapter, token: str, group_id: str, picture: Path,
                            *, expected_sha256: str | None = None,
                            client: httpx.AsyncClient | None = None) -> str:
    """One upload attempt; token stays in VK method form, never at upload_url."""
    # Check the immutable local PNG before requesting any temporary upload URL.
    if not isinstance(group_id, str) or not re.fullmatch(r"[1-9][0-9]{0,14}", group_id):
        raise VKPhotoUploadError("Некорректный ID сообщества VK")
    try:
        picture_bytes = picture.read_bytes()
    except OSError:
        raise VKPhotoUploadError("Файл картинки отсутствует или недоступен") from None
    if not picture_bytes.startswith(b"\x89PNG\r\n\x1a\n") or not 0 < len(picture_bytes) <= 10 * 1024 * 1024:
        raise VKPhotoUploadError("Файл для VK должен быть корректным PNG до 10 МБ")
    if expected_sha256 is not None and hashlib.sha256(picture_bytes).hexdigest() != expected_sha256:
        raise VKPhotoUploadError("Изображение изменилось непосредственно перед отправкой в VK")
    try:
        server = await adapter.call("photos.getWallUploadServer", token, group_id=int(group_id))
    except Exception:
        # Group keys may allow wall.post while forbidding photo upload (VK error 27).
        raise VKPhotoUploadError(
            "ВК не разрешил загрузку фото этим ключом. Для фото может потребоваться "
            "пользовательский ключ с правами photos и wall."
        ) from None
    try:
        upload_url = _upload_destination(server["upload_url"])
    except (TypeError, KeyError):
        raise VKPhotoUploadError("ВК не вернул сервер загрузки фото") from None
    try:
        if client is None:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(40, connect=10), trust_env=False,
                follow_redirects=False,
            ) as owned:
                response = await owned.post(
                    upload_url, files={"photo": ("servgo-post.png", picture_bytes, "image/png")},
                )
        else:
            response = await client.post(
                upload_url, files={"photo": ("servgo-post.png", picture_bytes, "image/png")},
            )
        if response.status_code != 200:
            raise VKPhotoUploadError("Сервер VK не принял файл изображения")
        upload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, VKPhotoUploadError):
            raise
        raise VKPhotoUploadError("Ошибка соединения при загрузке фотографии в VK") from None
    if not isinstance(upload, dict):
        raise VKPhotoUploadError("Неправильный ответ сервера VK на загрузку")
    try:
        server_id = upload["server"]
        photo = upload["photo"]
        signature = upload["hash"]
        # VK Android SDK reads upload server as a string, whereas API schema
        # 5.199 passes an integer to photos.saveWallPhoto.
        if isinstance(server_id, str):
            if not re.fullmatch(r"[1-9][0-9]{0,18}", server_id):
                raise ValueError()
            server_id = int(server_id)
        if (type(server_id) is not int or not 0 < server_id < 2**63
                or not isinstance(photo, str) or not 0 < len(photo) <= 1_000_000
                or not isinstance(signature, str) or not 0 < len(signature) <= 1000):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise VKPhotoUploadError("ВК не подтвердил загрузку фотографии") from None
    try:
        result = await adapter.call(
            "photos.saveWallPhoto", token, group_id=int(group_id),
            server=server_id, photo=photo, hash=signature,
        )
    except Exception:
        raise VKPhotoUploadError("ВК не подтвердил сохранение фотографии") from None
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        raise VKPhotoUploadError("ВК не вернул идентификатор сохранённого изображения")
    saved = result[0]
    owner_id, media_id = saved.get("owner_id"), saved.get("id")
    if (type(owner_id) is not int or owner_id != -int(group_id)
            or type(media_id) is not int or media_id <= 0):
        raise VKPhotoUploadError(
            "ВК сохранил фотографию не в выбранном сообществе; публикация остановлена"
        )
    return f"photo{owner_id}_{media_id}"
