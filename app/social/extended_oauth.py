"""Provider-specific official OAuth flows for VK ID and Meta Threads.

Tokens are never exposed to browsers; a company is connected only after
its existing VK/Threads comment adapter proves account ownership and scopes.
"""
import base64
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from app.comments.contracts import ConnectInput
from app.comments.service import CommentService
from app.comments.secrets import CommentSecrets


def vk_verifier_and_challenge():
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def extended_authorization(platform, app_id, redirect_uri, state, *, challenge=None):
    common = {"client_id": app_id, "redirect_uri": redirect_uri,
              "state": state, "response_type": "code"}
    if platform == "threads":
        common["scope"] = "threads_basic,threads_content_publish,threads_read_replies,threads_manage_replies"
        return "https://threads.net/oauth/authorize?" + urlencode(common)
    if platform in {"vk", "vk_video"}:
        if not challenge:
            raise ValueError("Для VK ID обязателен PKCE")
        # VK ID uses an OAuth 2.1 authorization-code + PKCE exchange.
        # VK API method grants depend on the app's access and VK review.
        common.update({"scope": "groups wall" if platform == "vk" else "groups video",
                       "code_challenge": challenge, "code_challenge_method": "s256"})
        return "https://id.vk.com/authorize?" + urlencode(common)
    raise LookupError("Эта соцсеть не поддерживает выбранный OAuth-процесс")


async def complete_extended(db, settings, platform, row, code, *, state, device_id=None):
    """Consume VK/Threads OAuth response after state validation in oauth.complete."""
    from app.social.oauth import credentials, oauth_http, origin_and_redirect
    client_id, secret = credentials(settings, platform)
    redirect_uri = origin_and_redirect(settings, row.product_id, platform)
    product_id = row.product_id
    if platform == "threads":
        short = await oauth_http(settings, "POST", "https://graph.threads.net/oauth/access_token",
            params={"client_id": client_id, "client_secret": secret, "code": code,
                    "grant_type": "authorization_code", "redirect_uri": redirect_uri})
        token, account_id = short.get("access_token"), short.get("user_id")
        if not token or not str(account_id).isdigit():
            raise ValueError("Threads не выдал права и ID аккаунта")
        data = await oauth_http(settings, "GET", "https://graph.threads.net/access_token",
            params={"grant_type": "th_exchange_token", "client_secret": secret, "access_token": token})
        access = data.get("access_token")
        if not access:
            raise ValueError("Threads не выдал долгосрочный доступ")
        metadata = {"platform": "threads", "expires_at": (
            datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 5184000))).isoformat()}
    elif platform in {"vk", "vk_video"}:
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", device_id or ""):
            raise ValueError("VK ID не вернул корректный device_id")
        if not row.pkce_verifier or not row.target_account_id:
            raise ValueError("Отсутствуют параметры защищённого подключения VK")
        # Follow the exact VK-owned vkid-web-sdk exchange contract.
        data = await oauth_http(settings, "POST", "https://id.vk.com/oauth2/auth",
            params={"grant_type": "authorization_code", "redirect_uri": redirect_uri,
                    "client_id": client_id, "code_verifier": row.pkce_verifier,
                    "state": state, "device_id": device_id},
            data={"code": code})
        if data.get("state") != state:
            raise ValueError("VK ID вернул неподтверждённый state")
        access = data.get("access_token")
        if not access or not re.fullmatch(r"[1-9]\d*", row.target_account_id):
            raise ValueError("VK ID не предоставил доступ к запрошенному сообществу")
        account_id = row.target_account_id
        metadata = {"platform": platform, "device_id": device_id,
                    "refresh_token": data.get("refresh_token"),
                    "expires_at": (datetime.now(timezone.utc) +
                        timedelta(seconds=data.get("expires_in", 3600))).isoformat()}
    else:
        raise LookupError("Платформа не поддерживается")
    # Same security checks as the pre-existing adapters (manager role, comment access).
    service = CommentService(db, settings)
    channel = await service.connect(product_id, ConnectInput(
        platform=platform, account_id=str(account_id), token=access))
    CommentSecrets(settings.products_root).save_oauth_info(channel.id, metadata)
    return channel


async def refresh_extended(settings, platform, access, info):
    from app.social.oauth import credentials, oauth_http, origin_and_redirect
    if platform == "threads":
        result = await oauth_http(settings, "GET", "https://graph.threads.net/refresh_access_token",
            params={"grant_type": "th_refresh_token", "access_token": access})
    elif platform in {"vk", "vk_video"}:
        if not info.get("refresh_token") or not info.get("device_id"):
            raise ValueError("VK не предоставил обновляемый токен — подключите аккаунт заново")
        client_id, _ = credentials(settings, platform)
        refresh_state = secrets.token_urlsafe(24)
        result = await oauth_http(settings, "POST", "https://id.vk.com/oauth2/auth",
            params={"grant_type": "refresh_token", "redirect_uri": origin_and_redirect(
                settings, None, platform), "client_id": client_id,
                "device_id": info["device_id"], "state": refresh_state},
            data={"refresh_token": info["refresh_token"]})
        if result.get("state") != refresh_state:
            raise ValueError("VK ID вернул неподтверждённый ответ обновления доступа")
    else:
        raise ValueError("Площадка не поддерживает обновление доступа")
    token = result.get("access_token")
    if not token:
        raise ValueError("Площадка не обновила доступ")
    new = dict(info)
    new["expires_at"] = (datetime.now(timezone.utc) +
        timedelta(seconds=result.get("expires_in", 3600))).isoformat()
    if result.get("refresh_token"):
        new["refresh_token"] = result["refresh_token"]
    return token, new
