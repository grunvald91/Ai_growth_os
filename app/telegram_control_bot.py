import asyncio
from uuid import UUID

import httpx

from app.control.decisions import ApprovalDecisionError, decide_approval
from app.control.telegram import render_decision_required, render_status
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.control import ControlService


class TelegramAPIError(RuntimeError):
    pass


class TelegramAPI:
    def __init__(self, token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"

    async def call(self, method: str, payload: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=35.0) as client:
            response = await client.post(
                f"{self.base_url}/{method}",
                json=payload or {},
            )
            response.raise_for_status()
            body = response.json()
        if not body.get("ok"):
            raise TelegramAPIError(body.get("description") or "Telegram API error")
        return body["result"]


class TelegramControlBot:
    def __init__(self, api: TelegramAPI, owner_chat_id: int, poll_timeout: int = 25) -> None:
        self.api = api
        self.owner_chat_id = owner_chat_id
        self.poll_timeout = poll_timeout

    async def run(self) -> None:
        offset: int | None = None
        while True:
            payload = {
                "timeout": self.poll_timeout,
                "allowed_updates": ["message", "callback_query"],
            }
            if offset is not None:
                payload["offset"] = offset
            try:
                updates = await self.api.call("getUpdates", payload)
                for update in updates:
                    offset = update["update_id"] + 1
                    await self.handle_update(update)
            except (httpx.HTTPError, TelegramAPIError):
                await asyncio.sleep(3)

    async def handle_update(self, update: dict) -> None:
        if "message" in update:
            await self._handle_message(update["message"])
        elif "callback_query" in update:
            await self._handle_callback(update["callback_query"])

    async def _handle_message(self, message: dict) -> None:
        sender_id = (message.get("from") or {}).get("id")
        chat_id = (message.get("chat") or {}).get("id")
        if sender_id != self.owner_chat_id or chat_id != self.owner_chat_id:
            return

        text = (message.get("text") or "").strip()
        command = text.split()[0].split("@")[0].lower() if text else ""
        if command == "/status":
            with SessionLocal() as db:
                body = render_status(ControlService(db).all_status())
            await self._send(chat_id, body)
            return

        if command == "/inbox":
            await self._send_inbox(chat_id)
            return

        if command in {"/start", "/help"}:
            await self._send(
                chat_id,
                "AI Growth OS\n\n/status — состояние системы\n/inbox — решения, которые ждут тебя",
            )

    async def _send_inbox(self, chat_id: int) -> None:
        with SessionLocal() as db:
            control = ControlService(db)
            status = control.all_status()
            items = []
            for product in status.products:
                items.extend(control.inbox(product.product_id, limit=20))

        if not items:
            await self._send(chat_id, "Decision Inbox пуст. Решений, требующих твоего участия, нет.")
            return

        for item in items:
            keyboard = []
            row = []
            for action in item.actions:
                if action.key == "open":
                    continue
                row.append(
                    {
                        "text": action.label,
                        "callback_data": f"{action.key}:{item.id}",
                    }
                )
            if row:
                keyboard.append(row)
            keyboard.append(
                [
                    {
                        "text": "Подробнее",
                        "callback_data": f"open:{item.id}",
                    }
                ]
            )
            await self._send(
                chat_id,
                render_decision_required(item),
                reply_markup={"inline_keyboard": keyboard},
            )

    async def _handle_callback(self, callback: dict) -> None:
        sender_id = (callback.get("from") or {}).get("id")
        if sender_id != self.owner_chat_id:
            return

        callback_id = callback.get("id")
        data = callback.get("data") or ""
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        if chat_id != self.owner_chat_id:
            return

        try:
            action, raw_id = data.split(":", 1)
            approval_id = UUID(raw_id)
        except (ValueError, TypeError):
            if callback_id:
                await self._answer_callback(callback_id, "Некорректная команда")
            return

        if action == "open":
            with SessionLocal() as db:
                record = db.get(__import__("app.models.domain", fromlist=["Approval"]).Approval, approval_id)
                if record is None:
                    text = "Решение не найдено."
                else:
                    text = (
                        f"Действие: {record.action_type}\n"
                        f"Риск: {record.risk_level}\n"
                        f"Статус: {record.status}\n"
                        f"Причина: {record.reason or 'не указана'}"
                    )
            await self._send(chat_id, text)
            if callback_id:
                await self._answer_callback(callback_id, "Открыто")
            return

        if action not in {"approve", "reject"}:
            if callback_id:
                await self._answer_callback(callback_id, "Неизвестное действие")
            return

        decision = "approved" if action == "approve" else "rejected"
        try:
            with SessionLocal() as db:
                record = decide_approval(
                    db,
                    approval_id,
                    decision=decision,
                    actor=f"telegram:{self.owner_chat_id}",
                )
            result_text = "✅ Одобрено" if record.status == "approved" else "❌ Отклонено"
        except ApprovalDecisionError:
            result_text = "🔴 RED-действие нельзя одобрить одной кнопкой. Открой детали."
        except LookupError:
            result_text = "Решение не найдено."

        if callback_id:
            await self._answer_callback(callback_id, result_text)
        await self._send(chat_id, result_text)

    async def _send(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        payload = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        await self.api.call("sendMessage", payload)

    async def _answer_callback(self, callback_id: str, text: str) -> None:
        await self.api.call(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": text[:200],
            },
        )


async def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token or settings.telegram_owner_chat_id is None:
        raise SystemExit("Configure TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_CHAT_ID")
    bot = TelegramControlBot(
        TelegramAPI(settings.telegram_bot_token),
        settings.telegram_owner_chat_id,
        settings.telegram_poll_timeout_seconds,
    )
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
