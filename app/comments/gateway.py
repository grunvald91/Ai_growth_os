"""Fixed-host HTTP adapters. Credentials exist only at this boundary."""
import re
from urllib.parse import quote
from urllib.parse import urlsplit
import httpx


class PlatformError(ValueError):
    pass


class CommentGateway:
    def __init__(self, client=None, *, proxy_url=None):
        self.client = client
        self.proxy_url = proxy_url

    async def request(self, method, url, token, *, telegram=False, **kwargs):
        headers = {} if telegram else {"Authorization": f"Bearer {token}"}
        direct = urlsplit(url).hostname == "api.vk.com"
        if self.client is None and not direct and not self.proxy_url:
            raise PlatformError("Настройте доступ через зарубежный сервер. Прямое подключение отключено.")
        if not direct and self.proxy_url and urlsplit(self.proxy_url).scheme not in {"http", "https"}:
            raise PlatformError("Для зарубежного сервера нужен HTTP или HTTPS proxy")
        try:
            if self.client is not None:
                response = await self.client.request(method, url, headers=headers, **kwargs)
            else:
                async with httpx.AsyncClient(timeout=25, follow_redirects=False,
                                            proxy=None if direct else self.proxy_url, trust_env=False) as client:
                    response = await client.request(method, url, headers=headers, **kwargs)
            if response.status_code == 429:
                raise PlatformError("Лимит площадки исчерпан. Повторите позже.")
            if response.status_code in {401, 403}:
                raise PlatformError("Проверьте срок действия токена и разрешения аккаунта.")
            if response.status_code >= 300:
                raise PlatformError("Площадка отклонила запрос. Проверьте доступ и комментарий.")
            data = response.json()
            if not isinstance(data, dict) or data.get("error") or (telegram and not data.get("ok")):
                raise PlatformError("Площадка отклонила запрос. Проверьте настройки подключения.")
            return data.get("result") if telegram else data
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, PlatformError):
                raise
            # Never surface HTTP exceptions: Telegram URLs contain credentials.
            raise PlatformError("Не удалось получить подтверждение площадки.") from None

    def tg_url(self, token, method):
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
            raise PlatformError("Некорректный токен Telegram")
        return f"https://api.telegram.org/bot{token}/{method}"

    def adapter(self, platform):
        if platform == "threads":
            from app.comments.threads import ThreadsAdapter
            return ThreadsAdapter(self)
        if platform in {"vk", "vk_video"}:
            from app.comments.vk import VKAdapter
            return VKAdapter(self, platform)
        return None

    async def check(self, platform, account_id, token):
        if adapter := self.adapter(platform):
            return await adapter.check(account_id, token)
        if platform == "youtube":
            data = await self.request("GET", "https://www.googleapis.com/youtube/v3/channels", token,
                params={"part": "snippet", "mine": "true", "maxResults": 50})
            account = next((x for x in data.get("items", []) if x["id"] == account_id), None)
            if not account:
                raise PlatformError("Канал не принадлежит авторизованному аккаунту")
            # Reading moderation data verifies more than a public-channel lookup.
            await self.request("GET", "https://www.googleapis.com/youtube/v3/commentThreads", token,
                params={"part": "snippet", "allThreadsRelatedToChannelId": account_id, "moderationStatus": "published", "maxResults": 1})
            return account["snippet"]["title"]
        if platform == "instagram":
            if not account_id.isdigit():
                raise PlatformError("Укажите числовой ID Instagram")
            data = await self.request("GET", "https://graph.instagram.com/v22.0/me", token,
                params={"fields": "user_id,username"})
            if str(data.get("user_id") or data.get("id")) != account_id:
                raise PlatformError("Токен относится к другому Instagram-аккаунту")
            await self.request("GET", f"https://graph.instagram.com/v22.0/{account_id}/media", token,
                params={"fields": "id,comments.limit(1){id,text}", "limit": 1})
            return data.get("username", account_id)
        if platform == "telegram":
            if not re.fullmatch(r"-100\d+", account_id):
                raise PlatformError("Укажите ID группы обсуждения, начинающийся с −100")
            me = await self.request("GET", self.tg_url(token, "getMe"), token, telegram=True)
            webhook = await self.request("GET", self.tg_url(token, "getWebhookInfo"), token, telegram=True)
            if webhook.get("url"):
                raise PlatformError("Бот уже использует webhook. Создайте отдельного бота.")
            chat = await self.request("POST", self.tg_url(token, "getChat"), token, telegram=True, json={"chat_id": account_id})
            member = await self.request("POST", self.tg_url(token, "getChatMember"), token, telegram=True,
                json={"chat_id": account_id, "user_id": me["id"]})
            if chat.get("type") != "supergroup" or not chat.get("linked_chat_id") or member.get("status") not in {"administrator", "creator"}:
                raise PlatformError("Бот должен быть администратором связанной группы обсуждения")
            if chat.get("permissions", {}).get("can_send_messages") is False and member.get("status") != "creator":
                raise PlatformError("Проверьте возможность отправлять сообщения в группе")
            return chat.get("title", account_id)
        raise PlatformError("Для этой площадки доступен ручной режим")

    async def fetch(self, channel, token):
        """Bounded, cursor-aware batches; caller persists cursor with imported rows."""
        if adapter := self.adapter(channel.platform):
            return await adapter.fetch(channel, token)
        if channel.platform == "youtube":
            params = {"part": "snippet", "allThreadsRelatedToChannelId": channel.account_id,
                      "order": "time", "maxResults": 100, "textFormat": "plainText"}
            if channel.cursor.get("page"):
                params["pageToken"] = channel.cursor["page"]
            data = await self.request("GET", "https://www.googleapis.com/youtube/v3/commentThreads", token, params=params)
            rows = []
            for item in data.get("items", []):
                comment = item["snippet"]["topLevelComment"]
                snippet = comment["snippet"]
                if snippet.get("authorChannelId", {}).get("value") == channel.account_id:
                    continue
                rows.append({"external_id": comment["id"], "text": snippet.get("textOriginal", ""),
                    "post_text": "", "target": {"video_id": snippet.get("videoId"), "can_reply": item["snippet"].get("canReply", False)}})
            return rows, {"page": data.get("nextPageToken")}
        if channel.platform == "instagram":
            # Persist pagination within a media and across media without dropping comments.
            cursor = dict(channel.cursor)
            if not cursor.get("media"):
                params = {"fields": "id,caption", "limit": 25}
                if cursor.get("after"):
                    params["after"] = cursor["after"]
                batch = await self.request("GET", f"https://graph.instagram.com/v22.0/{channel.account_id}/media", token, params=params)
                cursor = {"media": batch.get("data", []), "after": batch.get("paging", {}).get("cursors", {}).get("after") if batch.get("paging", {}).get("next") else None}
            if not cursor["media"]:
                return [], {}
            media = cursor["media"][0]
            params = {"fields": "id,text,from", "limit": 100}
            if cursor.get("comments_after"):
                params["after"] = cursor["comments_after"]
            batch = await self.request("GET", f"https://graph.instagram.com/v22.0/{quote(media['id'], safe='')}/comments", token, params=params)
            rows = [{"external_id": c["id"], "text": c.get("text", ""), "post_text": media.get("caption", ""),
                     "target": {"media_id": media["id"]}} for c in batch.get("data", [])
                    if str(c.get("from", {}).get("id", "")) != channel.account_id]
            cursor["comments_after"] = batch.get("paging", {}).get("cursors", {}).get("after") if batch.get("paging", {}).get("next") else None
            if not cursor["comments_after"]:
                cursor["media"] = cursor["media"][1:]
            return rows, cursor if cursor.get("media") or cursor.get("after") else {}
        if channel.platform == "telegram":
            data = await self.request("POST", self.tg_url(token, "getUpdates"), token, telegram=True,
                json={"offset": channel.cursor.get("offset", 0), "limit": 100, "timeout": 0, "allowed_updates": ["message"]})
            rows = []
            for item in data:
                message = item.get("message", {})
                # Only comments replying in the discussion; no off-topic group chatter.
                if str(message.get("chat", {}).get("id")) != channel.account_id or message.get("from", {}).get("is_bot") or message.get("is_automatic_forward") or not message.get("text") or not message.get("reply_to_message"):
                    continue
                rows.append({"external_id": str(message["message_id"]), "text": message["text"],
                    "post_text": message["reply_to_message"].get("text", message["reply_to_message"].get("caption", "")),
                    "target": {"chat_id": channel.account_id, "message_id": message["message_id"], "thread_id": message.get("message_thread_id")}})
            return rows, {"offset": max([x["update_id"] + 1 for x in data] + [channel.cursor.get("offset", 0)])}
        raise PlatformError("Ручной режим: добавьте комментарий самостоятельно")

    async def send(self, channel, token, comment, text):
        if adapter := self.adapter(channel.platform):
            return await adapter.send(channel, token, comment, text)
        if channel.platform == "youtube":
            # Fetch native parent again and bind it to the owned channel.
            check = await self.request("GET", "https://www.googleapis.com/youtube/v3/comments", token,
                params={"part": "snippet", "id": comment.external_id})
            parent = next(iter(check.get("items", [])), None)
            if not parent or parent["snippet"].get("channelId") != channel.account_id:
                raise PlatformError("Комментарий удалён или относится к другому каналу")
            data = await self.request("POST", "https://www.googleapis.com/youtube/v3/comments", token,
                params={"part": "snippet"}, json={"snippet": {"parentId": comment.external_id, "textOriginal": text}})
            return str(data["id"])
        if channel.platform == "instagram":
            account = await self.request("GET", "https://graph.instagram.com/v22.0/me", token,
                params={"fields": "user_id,username"})
            if str(account.get("user_id") or account.get("id")) != channel.account_id or not comment.target.get("media_id"):
                raise PlatformError("Комментарий относится к другому аккаунту")
            # Native comments can only enter from account-scoped media imports.
            await self.request("GET", f"https://graph.instagram.com/v22.0/{quote(comment.external_id, safe='')}", token,
                params={"fields": "id,text"})
            data = await self.request("POST", f"https://graph.instagram.com/v22.0/{quote(comment.external_id, safe='')}/replies", token, data={"message": text})
            return str(data["id"])
        if channel.platform == "telegram":
            if comment.target.get("chat_id") != channel.account_id:
                raise PlatformError("Комментарий относится к другой группе")
            payload = {"chat_id": channel.account_id, "text": text,
                "reply_parameters": {"message_id": int(comment.external_id), "allow_sending_without_reply": False}}
            if comment.target.get("thread_id"):
                payload["message_thread_id"] = comment.target["thread_id"]
            data = await self.request("POST", self.tg_url(token, "sendMessage"), token, telegram=True, json=payload)
            return str(data["message_id"])
        raise PlatformError("Автоматическая отправка для этой площадки недоступна")
