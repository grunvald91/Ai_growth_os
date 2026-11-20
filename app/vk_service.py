"""Operator-owned VK read credential: encrypted and shared by API and worker.

Community credentials remain tenant-scoped and are never replaced with this
service key. The service key is only used by VK read methods.
"""
from __future__ import annotations

from uuid import UUID

from app.comments.gateway import CommentGateway, PlatformError
from app.comments.secrets import CommentSecrets
from app.comments.vk import VKAdapter

# Reserved solely for the operator-owned credential, never a CommentChannel ID.
_SERVICE_KEY_ID = UUID("00000000-0000-4000-8000-000000000001")


def platform_service_token(settings) -> str | None:
    """Environment key takes precedence; otherwise read the encrypted file.

    Reading the file on each request permits secure operator UI setup without
    restarting the API or workers. The integration volume persists deployments.
    """
    configured = (settings.social_vk_service_token or "").strip()
    if configured:
        return configured
    try:
        return CommentSecrets(settings.products_root).load_token(_SERVICE_KEY_ID)
    except ValueError:
        return None


def save_platform_service_token(settings, token: str) -> None:
    if (not isinstance(token, str) or not token or token != token.strip()
            or len(token) > 8000 or any(ord(ch) < 33 for ch in token)):
        raise ValueError("Некорректный сервисный ключ ВКонтакте")
    if settings.social_vk_service_token:
        raise ValueError("Ключ задаётся окружением сервера и не может быть заменён через Studio")
    CommentSecrets(settings.products_root).save_token(_SERVICE_KEY_ID, token)


def delete_platform_service_token(settings) -> None:
    if settings.social_vk_service_token:
        raise ValueError("Сначала отключите ключ в окружении сервера")
    store = CommentSecrets(settings.products_root)
    with store.locked():
        store.path(_SERVICE_KEY_ID).unlink(missing_ok=True)


async def verify_platform_service_token(token: str, community_link: str) -> tuple[str, str, bool]:
    """Prove VK wall access and, where an owned post exists, comment reads.

    A service token is not considered comment-verified on an empty wall.
    This probe is read-only; it never sends a VK post or reply.
    """
    from app.social_vk import resolve_community

    account_id, group_name = await resolve_community(token, community_link)
    adapter = VKAdapter(CommentGateway(), "vk")
    try:
        wall = await adapter.call("wall.get", token, owner_id=-int(account_id), filter="owner", count=5)
        if not isinstance(wall, dict) or not isinstance(wall.get("items"), list):
            raise ValueError("VK не вернул список записей")
        items = [item for item in wall["items"]
                 if isinstance(item, dict) and item.get("owner_id") == -int(account_id)
                 and isinstance(item.get("id"), int)]
        if not items:
            return account_id, group_name, False
        await adapter.call("wall.getComments", token, owner_id=-int(account_id),
                           post_id=items[0]["id"], count=1)
    except (PlatformError, ValueError):
        raise ValueError("Сервисный ключ VK не разрешает чтение стены или комментариев") from None
    return account_id, group_name, True
