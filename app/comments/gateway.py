"""Fixed-host HTTP adapters. Credentials exist only at this boundary."""
import re
from urllib.parse import quote
from urllib.parse import urlsplit
import httpx


class PlatformError(ValueError):
    pass


class CommentGateway:
    def __init__(self, client=None, *, proxy_url=None, instagram_graph_version="v27.0",
                 vk_read_token=None):
        self.client = client
        self.proxy_url = proxy_url
        self.instagram_graph_version = instagram_graph_version
        self.vk_read_token = vk_read_token

    def instagram_base(self, channel):
        auth = (channel.cursor or {}).get("_auth") or {}
        if auth.get("mode") == "unified":
            return f"https://graph.facebook.com/{auth.get('version') or self.instagram_graph_version}"
        return "https://graph.instagram.com/v22.0"

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

    async def fetch_instagram_replies(self, channel, token, parent_id, *, after=None):
        """Page native replies of one already imported, account-scoped parent.

        The caller owns the media/parent mapping and persists the opaque
        pagination cursor. Never follow Meta's absolute paging.next URL.
        """
        if channel.platform != "instagram" or not str(parent_id).isdigit():
            raise PlatformError("Некорректный комментарий Instagram")
        params = {"fields": "id,text,from,timestamp", "limit": 100}
        if after:
            if not isinstance(after, str) or len(after) > 2048:
                raise PlatformError("Некорректный курсор Instagram")
            params["after"] = after
        return await self.request(
            "GET", f"{self.instagram_base(channel)}/{quote(str(parent_id), safe='')}/replies",
            token, params=params)

    async def fetch(self, channel, token):
        """Bounded, cursor-aware batches; caller persists cursor with imported rows."""
        if adapter := self.adapter(channel.platform):
            return await adapter.fetch(channel, token)
        if channel.platform == "youtube":
            # Complete partial YouTube reply threads in bounded batches. The
            # native commentThreads.replies field is *not* exhaustive; docs
            # require comments.list(parentId) for the remaining replies.
            cursor = {key: value for key, value in (channel.cursor or {}).items() if key != "_poll"}
            pending = list(cursor.get("reply_queue") or [])
            if pending:
                root = pending[0]
                params = {"part": "snippet", "parentId": root["id"], "maxResults": 100,
                          "textFormat": "plainText"}
                if cursor.get("reply_page"):
                    params["pageToken"] = cursor["reply_page"]
                data = await self.request("GET", "https://www.googleapis.com/youtube/v3/comments", token,
                                          params=params)
                rows = []
                for reply in data.get("items", []):
                    snippet = reply.get("snippet", {})
                    if snippet.get("authorChannelId", {}).get("value") == channel.account_id:
                        continue
                    rows.append({"external_id": reply["id"], "text": snippet.get("textOriginal", ""),
                        "post_text": root.get("post_text", ""),
                        "target": {"video_id": root.get("video_id"), "parent_id": root["id"],
                                   "can_reply": root.get("can_reply", False),
                                   "published_at": snippet.get("publishedAt")}})
                next_reply = data.get("nextPageToken")
                if next_reply:
                    cursor["reply_page"] = next_reply
                else:
                    pending.pop(0)
                    cursor.pop("reply_page", None)
                cursor["reply_queue"] = pending
                if not pending and cursor.pop("_yt_threads_complete", False):
                    return rows, {}
                if not pending:
                    cursor.pop("reply_queue", None)
                return rows, cursor

            params = {"part": "snippet,replies", "allThreadsRelatedToChannelId": channel.account_id,
                      "order": "time", "maxResults": 100, "textFormat": "plainText"}
            if cursor.get("page"):
                params["pageToken"] = cursor["page"]
            data = await self.request("GET", "https://www.googleapis.com/youtube/v3/commentThreads", token,
                                      params=params)
            rows, pending = [], []
            for item in data.get("items", []):
                thread = item.get("snippet", {})
                parent = thread.get("topLevelComment", {})
                snippet = parent.get("snippet", {})
                parent_id = parent.get("id")
                if not parent_id:
                    continue
                post_context = ""
                if snippet.get("authorChannelId", {}).get("value") != channel.account_id:
                    rows.append({"external_id": parent_id, "text": snippet.get("textOriginal", ""),
                        "post_text": post_context,
                        "target": {"video_id": snippet.get("videoId"),
                                   "parent_id": parent_id,
                                   "can_reply": thread.get("canReply", False),
                                   "published_at": snippet.get("publishedAt")}})
                inline_replies = item.get("replies", {}).get("comments", [])
                for reply in inline_replies:
                    reply_snippet = reply.get("snippet", {})
                    if reply_snippet.get("authorChannelId", {}).get("value") == channel.account_id:
                        continue
                    rows.append({"external_id": reply["id"], "text": reply_snippet.get("textOriginal", ""),
                        "post_text": snippet.get("textOriginal", ""),
                        "target": {"video_id": snippet.get("videoId"), "parent_id": parent_id,
                                   "can_reply": thread.get("canReply", False),
                                   "published_at": reply_snippet.get("publishedAt")}})
                if thread.get("totalReplyCount", 0) > len(inline_replies):
                    pending.append({"id": parent_id, "video_id": snippet.get("videoId"),
                                    "post_text": (snippet.get("textOriginal") or "")[:8000],
                                    "can_reply": thread.get("canReply", False)})

            next_page = data.get("nextPageToken")
            if not next_page and not pending:
                return rows, {}
            result = {"page": next_page}
            if pending:
                result["reply_queue"] = pending
                result["_yt_threads_complete"] = not bool(next_page)
            return rows, result

        if channel.platform == "instagram":
            # Always refresh the newest media, even during a large historic scan;
            # otherwise 25 old posts each cost a whole 10-minute poll interval.
            cursor = {key: value for key, value in (channel.cursor or {}).items()
                      if key not in {"_poll", "_auth"}}
            rows = []
            base = self.instagram_base(channel)
            recent = await self.request("GET", f"{base}/{channel.account_id}/media", token,
                                        params={"fields": "id,caption", "limit": 3})
            for media in recent.get("data", [])[:3]:
                batch = await self.request("GET",
                    f"{base}/{quote(media['id'], safe='')}/comments", token,
                    params={"fields": "id,text,from,timestamp", "limit": 100})
                rows.extend({"external_id": c["id"], "text": c.get("text", ""),
                             "post_text": media.get("caption") or "",
                             "target": {"media_id": media["id"],
                                        "published_at": c.get("timestamp")}}
                            for c in batch.get("data", [])
                            if str(c.get("from", {}).get("id", "")) != channel.account_id)
            # Historic pagination progresses through up to five pages per tick.
            # The freshest first-page scan above is independent of this cursor.
            for _ in range(5):
                if not cursor.get("media"):
                    params = {"fields": "id,caption", "limit": 25}
                    if cursor.get("after"):
                        params["after"] = cursor["after"]
                    batch = await self.request("GET",
                        f"{base}/{channel.account_id}/media", token, params=params)
                    cursor = {"media": batch.get("data", []),
                              "after": batch.get("paging", {}).get("cursors", {}).get("after")
                                       if batch.get("paging", {}).get("next") else None}
                    if not cursor["media"]:
                        return rows, {}
                media = cursor["media"][0]
                params = {"fields": "id,text,from,timestamp", "limit": 100}
                if cursor.get("comments_after"):
                    params["after"] = cursor["comments_after"]
                batch = await self.request("GET",
                    f"{base}/{quote(media['id'], safe='')}/comments", token, params=params)
                rows.extend({"external_id": c["id"], "text": c.get("text", ""),
                             "post_text": media.get("caption") or "",
                             "target": {"media_id": media["id"],
                                        "published_at": c.get("timestamp")}}
                            for c in batch.get("data", [])
                            if str(c.get("from", {}).get("id", "")) != channel.account_id)
                cursor["comments_after"] = (batch.get("paging", {}).get("cursors", {}).get("after")
                                           if batch.get("paging", {}).get("next") else None)
                if not cursor["comments_after"]:
                    cursor["media"] = cursor["media"][1:]
                    if not cursor["media"] and not cursor.get("after"):
                        return rows, {}
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
            parent_id = (comment.target or {}).get("parent_id") or comment.external_id
            check = await self.request("GET", "https://www.googleapis.com/youtube/v3/comments", token,
                params={"part": "snippet", "id": parent_id})
            parent = next(iter(check.get("items", [])), None)
            if not parent or parent["snippet"].get("channelId") != channel.account_id:
                raise PlatformError("Комментарий удалён или относится к другому каналу")
            if parent_id != comment.external_id:
                # A reply must still belong to the same verified parent thread.
                source = await self.request("GET", "https://www.googleapis.com/youtube/v3/comments", token,
                    params={"part": "snippet", "id": comment.external_id})
                nested = next(iter(source.get("items", [])), None)
                if not nested or nested["snippet"].get("parentId") != parent_id:
                    raise PlatformError("Ответ относится к другому обсуждению")
            data = await self.request("POST", "https://www.googleapis.com/youtube/v3/comments", token,
                params={"part": "snippet"}, json={"snippet": {"parentId": parent_id, "textOriginal": text}})
            return str(data["id"])
        if channel.platform == "instagram":
            base = self.instagram_base(channel)
            if not (comment.target or {}).get("media_id"):
                raise PlatformError("Комментарий не связан с публикацией Instagram")
            if ((channel.cursor or {}).get("_auth") or {}).get("mode") == "unified":
                # Query the specific granted asset rather than only page 1 of
                # assigned_instagram_accounts, which can hide other granted assets.
                await self.request("GET", f"{base}/{channel.account_id}/media", token,
                                   params={"fields": "id", "limit": 1})
                native = await self.request("GET", f"{base}/{quote(comment.external_id, safe='')}", token,
                    params={"fields": "id,text,media{id}"})
                if str((native.get("media") or {}).get("id")) != str(comment.target["media_id"]):
                    raise PlatformError("Комментарий относится к другой публикации")
            else:
                account = await self.request("GET", "https://graph.instagram.com/v22.0/me", token,
                    params={"fields": "user_id,username"})
                if str(account.get("user_id") or account.get("id")) != channel.account_id:
                    raise PlatformError("Комментарий относится к другому аккаунту")
                await self.request("GET", f"{base}/{quote(comment.external_id, safe='')}", token,
                    params={"fields": "id,text"})
            data = await self.request("POST",
                f"{base}/{quote(comment.external_id, safe='')}/replies", token,
                data={"message": text})
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
