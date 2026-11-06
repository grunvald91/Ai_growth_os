"""Separate VK wall/video capabilities, using a community manager's user token."""
import re

from app.comments.gateway import PlatformError


class VKAdapter:
    def __init__(self, gateway, platform):
        self.gateway = gateway
        self.video = platform == "vk_video"
        self.namespace = "video" if self.video else "wall"
        self.object_key = "video_id" if self.video else "post_id"

    async def call(self, method, token, **params):
        # Tokens go in the form body, never in a URL or cursor.
        data = await self.gateway.request("POST", f"https://api.vk.com/method/{method}", token,
                                          data={**params, "v": "5.199", "access_token": token})
        if "response" not in data:
            raise PlatformError("VK не подтвердил запрос")
        return data["response"]

    @staticmethod
    def owner(account_id):
        if not re.fullmatch(r"[1-9]\d*", account_id):
            raise PlatformError("Укажите положительный числовой ID сообщества VK без club/public")
        return -int(account_id)

    async def manager(self, account_id, token):
        self.owner(account_id)
        data = await self.call("groups.getById", token, group_id=account_id)
        groups = data.get("groups", []) if isinstance(data, dict) else data
        group = next((g for g in groups if str(g.get("id")) == account_id), None)
        if not group or group.get("is_admin") != 1 or group.get("admin_level", 0) < 2:
            raise PlatformError("Нужен пользовательский токен редактора или администратора этого сообщества VK")
        return group

    async def objects(self, account_id, token, offset=0, count=25):
        params = {"owner_id": self.owner(account_id)} if self.video else {"domain": f"club{account_id}", "filter": "owner"}
        return await self.call(f"{self.namespace}.get", token, **params, offset=offset, count=count)

    async def check(self, account_id, token):
        group = await self.manager(account_id, token)
        data = await self.objects(account_id, token, count=1)
        for item in data.get("items", []):
            if item.get("owner_id") == self.owner(account_id):
                await self.call(f"{self.namespace}.getComments", token, owner_id=self.owner(account_id),
                                **{self.object_key: item["id"]}, count=1)
        return group.get("name", account_id)

    async def fetch(self, channel, token):
        owner = self.owner(channel.account_id)
        cursor = dict(channel.cursor)
        if not cursor.get("objects"):
            offset = cursor.get("objects_offset", 0)
            data = await self.objects(channel.account_id, token, offset)
            items = data.get("items", [])
            cursor = {"objects": [i for i in items if i.get("owner_id") == owner],
                      "objects_offset": offset + len(items) if items and offset + len(items) < data.get("count", 0) else None}
        if not cursor["objects"]:
            return [], cursor if cursor.get("objects_offset") else {}
        item = cursor["objects"][0]
        roots = list(cursor.get("threads", []))
        offset = cursor.get("comments_offset", 0)
        params = {"owner_id": owner, self.object_key: item["id"], "offset": offset,
                  "count": 100, "sort": "asc", "thread_items_count": 0}
        if not self.video:
            params["preview_length"] = 0
        if cursor.get("reading_threads"):
            params["comment_id"] = roots[0]
        data = await self.call(f"{self.namespace}.getComments", token, **params)
        comments = data.get("items", [])
        if not cursor.get("reading_threads"):
            roots.extend(c["id"] for c in comments if c.get("thread", {}).get("count", 0) > 0)
        rows = [{"external_id": f"{owner}_{item['id']}_{c['id']}", "text": c["text"],
                 "post_text": item.get("description" if self.video else "text", item.get("title", "")),
                 "target": {"owner_id": owner, self.object_key: item["id"], "comment_id": c["id"]}}
                for c in comments if c.get("text") and c.get("from_id") != owner and not c.get("deleted")]
        next_offset = offset + len(comments)
        if comments and next_offset < data.get("current_level_count", data.get("count", 0)):
            cursor["comments_offset"] = next_offset
        else:
            cursor["comments_offset"] = 0
            if cursor.get("reading_threads"):
                roots = roots[1:]
            if roots:
                cursor["reading_threads"] = True
            else:
                cursor.pop("reading_threads", None)
                cursor["objects"] = cursor["objects"][1:]
        cursor["threads"] = roots
        return rows, cursor if cursor.get("objects") or cursor.get("objects_offset") else {}

    async def send(self, channel, token, comment, text):
        owner = self.owner(channel.account_id)
        target = comment.target
        object_id, comment_id = target.get(self.object_key), target.get("comment_id")
        if target.get("owner_id") != owner or not object_id or not comment_id or comment.external_id != f"{owner}_{object_id}_{comment_id}":
            raise PlatformError("Комментарий относится к другому сообществу VK")
        await self.manager(channel.account_id, token)
        if self.video:
            data = await self.call("video.get", token, videos=f"{owner}_{object_id}", count=1)
        else:
            data = await self.call("wall.getById", token, posts=f"{owner}_{object_id}")
        items = data.get("items", []) if isinstance(data, dict) else data
        if not any(i.get("owner_id") == owner and i.get("id") == object_id for i in items):
            raise PlatformError("Исходная публикация VK удалена или принадлежит другому сообществу")
        if self.video:
            check = await self.call("video.getComments", token, owner_id=owner, video_id=object_id,
                                    start_comment_id=comment_id, count=1, sort="asc")
        else:
            check = await self.call("wall.getComment", token, owner_id=owner, comment_id=comment_id)
        if not any(c.get("id") == comment_id and not c.get("deleted")
                   and c.get(self.object_key, object_id if self.video else None) == object_id
                   for c in check.get("items", [])):
            raise PlatformError("Исходный комментарий VK удалён или недоступен")
        result = await self.call(f"{self.namespace}.createComment", token, owner_id=owner,
                                **{self.object_key: object_id}, reply_to_comment=comment_id, message=text,
                                from_group=1 if self.video else int(channel.account_id), guid=str(comment.id))
        reference = result.get("comment_id") if isinstance(result, dict) else result
        if not isinstance(reference, int) or reference <= 0:
            raise PlatformError("VK не подтвердил публикацию ответа")
        return str(reference)
