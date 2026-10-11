"""Official OAuth authorization-code login for supported social integrations."""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit
from uuid import UUID
import httpx
from fastapi import HTTPException
from sqlalchemy import select

from app.comments.contracts import ConnectInput
from app.comments.service import CommentService
from app.comments.secrets import CommentSecrets
from app.social.models import SocialOAuthAttempt


def origin_and_redirect(settings, product_id, platform):
    origin = (settings.social_oauth_redirect_base or "").rstrip("/")
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Настройте SOCIAL_OAUTH_REDIRECT_BASE как публичный HTTPS-адрес без пути")
    return origin + f"/social/oauth/{platform}/callback"


def credentials(settings, platform):
    if platform == "youtube":
        client_id, secret = settings.social_google_client_id, settings.social_google_client_secret
    elif platform == "instagram":
        client_id, secret = settings.social_instagram_client_id, settings.social_instagram_client_secret
    elif platform == "threads":
        client_id, secret = settings.social_threads_client_id, settings.social_threads_client_secret
    elif platform in {"vk", "vk_video"}:
        client_id, secret = settings.social_vk_client_id, ""
    else:
        raise LookupError("Для этой площадки вход через OAuth пока не настроен")
    if not client_id or (not secret and platform not in {"vk", "vk_video"}):
        raise ValueError("Администратор ServGo ещё не настроил OAuth для этой соцсети")
    if platform not in {"vk", "vk_video"} and not settings.comment_platform_proxy_url:
        raise ValueError("Не настроен зарубежный HTTPS-прокси для авторизации")
    return client_id, secret


def state_hash(state):
    return hashlib.sha256(state.encode("ascii")).hexdigest()


def authorize(db, settings, product_id, platform, account_id=None):
    credentials(settings, platform)
    redirect_uri = origin_and_redirect(settings, product_id, platform)
    from app.models.domain import Product
    if not db.get(Product, product_id):
        raise LookupError("Бизнес не найден")
    state = secrets.token_urlsafe(32)
    verifier, challenge = None, None
    if platform in {"vk", "vk_video"}:
        if not re.fullmatch(r"[1-9]\d{0,14}", account_id or ""):
            raise ValueError("Перед подключением укажите положительный ID сообщества VK")
        from app.social.extended_oauth import vk_verifier_and_challenge
        verifier, challenge = vk_verifier_and_challenge()
    db.add(SocialOAuthAttempt(product_id=product_id, platform=platform,
        state_digest=state_hash(state), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        status="pending", target_account_id=account_id, pkce_verifier=verifier))
    db.commit()
    app_id, _ = credentials(settings, platform)
    if platform == "youtube":
        authorization = "https://accounts.google.com/o/oauth2/v2/auth"
        query = {"client_id": app_id, "redirect_uri": redirect_uri, "response_type": "code",
            "state": state, "scope": "https://www.googleapis.com/auth/youtube.force-ssl",
            "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"}
    elif platform == "instagram":
        authorization = "https://www.instagram.com/oauth/authorize"
        query = {"client_id": app_id, "redirect_uri": redirect_uri, "response_type": "code",
            "state": state, "scope": "instagram_business_basic,instagram_business_manage_comments",
            "enable_fb_login": "0"}
    else:
        from app.social.extended_oauth import extended_authorization
        return extended_authorization(platform, app_id, redirect_uri, state, challenge=challenge)
    return authorization + "?" + urlencode(query)


async def oauth_http(settings, method, url, **kwargs):
    """Only call hardcoded platform hosts. Never log access tokens or provider response bodies."""
    if urlsplit(url).hostname not in {"oauth2.googleapis.com", "api.instagram.com", "graph.instagram.com",
                                             "graph.threads.net", "id.vk.com"}:
        raise ValueError("Недопустимый адрес OAuth")
    try:
        proxy = None if urlsplit(url).hostname == "id.vk.com" else settings.comment_platform_proxy_url
        async with httpx.AsyncClient(proxy=proxy,
                                     trust_env=False, follow_redirects=False, timeout=25) as client:
            response = await client.request(method, url, **kwargs)
        value = response.json()
        if response.status_code != 200 or not isinstance(value, dict) or "error" in value:
            raise ValueError("Площадка отказала в авторизации. Проверьте разрешения приложения")
        return value
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("Площадка отказала"):
            raise
        raise ValueError("Не удалось завершить авторизацию через платформу") from None


async def complete(db, settings, platform: str, state: str, code: str, *, device_id: str | None = None):
    credentials(settings, platform)
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", state) or not code or len(code) > 4000:
        raise ValueError("Некорректный ответ авторизации")
    row = db.scalar(select(SocialOAuthAttempt).where(
        SocialOAuthAttempt.state_digest == state_hash(state),
        SocialOAuthAttempt.platform == platform).with_for_update())
    if not row or row.status != "pending":
        raise ValueError("Авторизация уже использована или не начиналась")
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise ValueError("Срок авторизации истёк. Подключите заново")
    # Irrevocably consume state before any network request to prevent callback replay.
    row.status = "used"
    product_id = row.product_id
    db.commit()
    app_id, secret = credentials(settings, platform)
    redirect_uri = origin_and_redirect(settings, product_id, platform)
    if platform in {"vk", "vk_video", "threads"}:
        from app.social.extended_oauth import complete_extended
        return await complete_extended(db, settings, platform, row, code, state=state, device_id=device_id)
    if platform == "youtube":
        result = await oauth_http(settings, "POST", "https://oauth2.googleapis.com/token",
            data={"client_id": app_id, "client_secret": secret, "code": code,
                  "grant_type": "authorization_code", "redirect_uri": redirect_uri})
        access, refresh = result.get("access_token"), result.get("refresh_token")
        if not access or not refresh:
            raise ValueError("Google не выдал долгосрочное разрешение. Повторите подключение")
        svc = CommentService(db, settings)
        mine = await svc.gateway.request("GET", "https://www.googleapis.com/youtube/v3/channels",
            access, params={"part": "snippet", "mine": "true", "maxResults": 50})
        channels = mine.get("items", [])
        if len(channels) != 1:
            raise ValueError("Google показал не один канал. Выберите аккаунт с одним управляемым каналом")
        account_id = channels[0]["id"]
        metadata = {"platform": "youtube", "refresh_token": refresh, "expires_at": (
            datetime.now(timezone.utc) + timedelta(seconds=result.get("expires_in", 3600))).isoformat()}
    else:
        result = await oauth_http(settings, "POST", "https://api.instagram.com/oauth/access_token",
            data={"client_id": app_id, "client_secret": secret, "code": code,
                  "grant_type": "authorization_code", "redirect_uri": redirect_uri})
        short_token, account_id = result.get("access_token"), result.get("user_id")
        if not short_token or not account_id:
            raise ValueError("Instagram не вернул аккаунт и токен")
        long_lived = await oauth_http(settings, "GET", "https://graph.instagram.com/access_token",
            params={"grant_type": "ig_exchange_token", "client_secret": secret, "access_token": short_token})
        access = long_lived.get("access_token")
        if not access:
            raise ValueError("Instagram не разрешил долгосрочный доступ")
        metadata = {"platform": "instagram", "expires_at": (
            datetime.now(timezone.utc) + timedelta(seconds=long_lived.get("expires_in", 5184000))).isoformat()}
    svc = CommentService(db, settings)
    channel = await svc.connect(product_id, ConnectInput(platform=platform,
        account_id=str(account_id), token=access))
    CommentSecrets(settings.products_root).save_oauth_info(channel.id, metadata)
    return channel


async def valid_token(secrets_store, settings, channel):
    """Refresh provider tokens early, preserving the previous token on errors."""
    info = secrets_store.load_oauth_info(channel.id)
    access = secrets_store.load_token(channel.id)
    if not info or channel.platform not in {"instagram", "youtube", "threads", "vk", "vk_video"}:
        return access
    expires = datetime.fromisoformat(info["expires_at"])
    if expires > datetime.now(timezone.utc) + timedelta(minutes=15):
        return access
    if channel.platform in {"threads", "vk", "vk_video"}:
        from app.social.extended_oauth import refresh_extended
        new_token, updated_info = await refresh_extended(settings, channel.platform, access, info)
        secrets_store.save_token(channel.id, new_token)
        secrets_store.save_oauth_info(channel.id, updated_info)
        return new_token
    if channel.platform == "youtube":
        _, client_secret = credentials(settings, "youtube")
        result = await oauth_http(settings, "POST", "https://oauth2.googleapis.com/token",
            data={"client_id": settings.social_google_client_id, "client_secret": client_secret,
                  "refresh_token": info["refresh_token"], "grant_type": "refresh_token"})
    else:
        result = await oauth_http(settings, "GET", "https://graph.instagram.com/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": access})
    new_token = result.get("access_token")
    if not new_token:
        raise ValueError("Соцсеть не продлила доступ. Подключите аккаунт заново")
    info["expires_at"] = (datetime.now(timezone.utc) +
        timedelta(seconds=result.get("expires_in", 3600))).isoformat()
    # Refresh data stays in protected encrypted files, never in the web response.
    secrets_store.save_token(channel.id, new_token)
    secrets_store.save_oauth_info(channel.id, info)
    return new_token
