"""Threads API: owned conversations and two-stage text replies through the gateway."""
from urllib.parse import quote

from app.comments.gateway import PlatformError

BASE = "https://graph.threads.net/v1.0"


class ThreadsAdapter:
    def __init__(self, gateway):
        self.gateway = gateway

    async def call(self, method, path, token, **kwargs):
        return await self.gateway.request(method, f"{BASE}/{quote(str(path), safe='/')}", token, **kwargs)

    async def check(self, account_id, token):
        if not account_id.isdigit():
            raise PlatformError("Укажите числовой ID Threads-аккаунта")
        me = await self.call("GET", "me", token, params={"fields": "id,username"})
        if str(me.get("id")) != account_id:
            raise PlatformError("Токен относится к другому Threads-аккаунту")
        posts = await self.call("GET", "me/threads", token, params={"fields": "id,is_reply", "limit": 1})
        for post in posts.get("data", []):
            if not post.get("is_reply"):
                await self.call("GET", f"{post['id']}/conversation", token, params={"fields": "id,text", "limit": 1})
        return me.get("username", account_id)

    @staticmethod
    def after(data):
        paging = data.get("paging", {})
        after = paging.get("cursors", {}).get("after")
        if paging.get("next") and not after:
            raise PlatformError("Площадка не вернула курсор следующей страницы")
        return after if paging.get("next") else None

    async def fetch(self, channel, token):
        cursor = dict(channel.cursor)
        if not cursor.get("posts"):
            params = {"fields": "id,text,is_reply", "limit": 25}
            if cursor.get("after"):
                params["after"] = cursor["after"]
            data = await self.call("GET", f"{channel.account_id}/threads", token, params=params)
            cursor = {"posts": [p for p in data.get("data", []) if not p.get("is_reply")], "after": self.after(data)}
        if not cursor["posts"]:
            return [], cursor if cursor.get("after") else {}
        post = cursor["posts"][0]
        params = {"fields": "id,text,is_reply_owned_by_me,root_post", "limit": 100, "reverse": "false"}
        if cursor.get("replies_after"):
            params["after"] = cursor["replies_after"]
        data = await self.call("GET", f"{post['id']}/conversation", token, params=params)
        rows = [{"external_id": str(c["id"]), "text": c["text"], "post_text": post.get("text", ""),
                 "target": {"root_post_id": str(post["id"])}} for c in data.get("data", [])
                if c.get("text") and not c.get("is_reply_owned_by_me") and str(c.get("root_post", {}).get("id")) == str(post["id"])]
        cursor["replies_after"] = self.after(data)
        if not cursor["replies_after"]:
            cursor["posts"] = cursor["posts"][1:]
        return rows, cursor if cursor.get("posts") or cursor.get("after") else {}

    async def send(self, channel, token, comment, text):
        if not text.strip() or len(text) > 500:
            raise PlatformError("Ответ Threads должен содержать от 1 до 500 символов")
        root_id = comment.target.get("root_post_id")
        if not root_id:
            raise PlatformError("Неизвестна исходная публикация Threads")
        me = await self.call("GET", "me", token, params={"fields": "id"})
        parent = await self.call("GET", comment.external_id, token, params={"fields": "id,root_post"})
        root = await self.call("GET", root_id, token, params={"fields": "id,owner"})
        if (str(me.get("id")) != channel.account_id or str(parent.get("id")) != comment.external_id
                or str(parent.get("root_post", {}).get("id")) != root_id
                or str(root.get("owner", {}).get("id")) != channel.account_id):
            raise PlatformError("Комментарий удалён или относится к другому Threads-аккаунту")
        container = await self.call("POST", "me/threads", token,
                                    data={"media_type": "TEXT", "text": text, "reply_to_id": comment.external_id})
        if not container.get("id"):
            raise PlatformError("Threads не подтвердил создание ответа")
        published = await self.call("POST", "me/threads_publish", token, data={"creation_id": container["id"]})
        if not published.get("id"):
            raise PlatformError("Threads не подтвердил публикацию ответа")
        return str(published["id"])
