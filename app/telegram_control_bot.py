import asyncio
import json
import os
from uuid import UUID

import httpx

from app.control.decisions import ApprovalDecisionError, decide_approval, approval_review_token
from app.control.telegram import (
    action_label,
    channel_label,
    render_decision_required,
    render_experiment_detail,
    render_experiment_launch_confirmation,
    render_experiments,
    render_status,
    risk_label,
    status_label,
)
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import Approval, Artifact, Experiment
from app.services.control import ControlService
from app.services.experiment_launch import ExperimentLaunchError, ExperimentLaunchService
from app.services.prospects import ProspectService
from app.intake.telegram import TelegramCompanyIntake
from app.content.telegram import TelegramContent
from app.communications.telegram import TelegramCommunications


MAIN_MENU = {
    "keyboard": [
        [{"text": "📊 Главное"}, {"text": "✅ На согласование"}],
        [{"text": "🧪 Тесты"}, {"text": "🔄 Обновить"}],
        [{"text": "📝 Контент"}, {"text": "📇 Контакты"}],
        [{"text": "🏢 Компания"}, {"text": "📧 Почта"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

STATUS_INLINE = {
    "inline_keyboard": [
        [
            {"text": "✅ На согласование", "callback_data": "menu:inbox"},
            {"text": "🧪 Тесты", "callback_data": "menu:experiments"},
        ],
        [{"text": "🔄 Обновить", "callback_data": "menu:status"}],
    ]
}


class TelegramAPIError(RuntimeError):
    pass


class TelegramAPI:
    def __init__(self, token: str, proxy_url: str | None = None) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.proxy_url = proxy_url or os.getenv("TELEGRAM_PROXY_URL")

    async def call(self, method: str, payload: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=35.0, proxy=self.proxy_url) as client:
            response = await client.post(
                f"{self.base_url}/{method}",
                json=payload or {},
            )
            response.raise_for_status()
            body = response.json()
        if not body.get("ok"):
            raise TelegramAPIError(body.get("description") or "Telegram API error")
        return body["result"]

    async def upload(self, chat_id: int, method: str, field: str, name: str, data: bytes):
        if len(data) > 49_000_000:
            raise ValueError('File exceeds Telegram upload limit')
        async with httpx.AsyncClient(timeout=120, proxy=self.proxy_url) as client:
            response = await client.post(f'{self.base_url}/{method}',
                data={'chat_id': str(chat_id)}, files={field: (name, data)})
            response.raise_for_status()
            body = response.json()
        if not body.get('ok'):
            raise TelegramAPIError('Telegram upload failed')
        return body['result']

    async def download_file(self, file_id: str, max_bytes: int) -> bytes:
        from pathlib import PurePosixPath
        metadata = await self.call("getFile", {"file_id": file_id})
        path = metadata.get("file_path", "")
        if not path or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise TelegramAPIError("Invalid Telegram file path")
        if metadata.get("file_size", 0) > max_bytes:
            raise TelegramAPIError("File too large")
        url = self.base_url.replace("/bot", "/file/bot", 1) + "/" + path
        async with httpx.AsyncClient(timeout=35, proxy=self.proxy_url, follow_redirects=False) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise TelegramAPIError("File too large")
        return bytes(data)


class TelegramControlBot:
    def __init__(self, api: TelegramAPI, owner_chat_id: int, poll_timeout: int = 25) -> None:
        self.api = api
        self.owner_chat_id = owner_chat_id
        self.poll_timeout = poll_timeout
        self.company_intake = TelegramCompanyIntake(self, SessionLocal, get_settings)
        self.content = TelegramContent(self, SessionLocal, get_settings)
        self.communications = TelegramCommunications(self, SessionLocal, get_settings)

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

        if await self.communications.message(message):
            return
        if await self.content.message(message):
            return
        if await self.company_intake.message(message):
            return

        text = (message.get("text") or "").strip()
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""

        if command == "/status" or text in {"📊 Главное", "📊 Статус", "🔄 Обновить"}:
            await self._send_status(chat_id)
            return

        if command == "/inbox" or text in {"✅ На согласование", "📨 Решения"}:
            await self._send_inbox(chat_id)
            return

        if command == "/experiments" or text in {"🧪 Тесты", "🧪 Эксперименты"}:
            await self._send_experiments(chat_id)
            return

        if command in {"/start", "/help"}:
            await self._send(
                chat_id,
                "🤖 AI Growth OS — твоя ИИ-команда по маркетингу.\n\n"
                "Здесь видно, что команда делает сейчас, какой есть прогресс "
                "и где требуется твоё решение.\n\n"
                "Начни с кнопки «📊 Главное».",
                reply_markup=MAIN_MENU,
            )
            return

        await self._send(
            chat_id,
            "Выбери раздел кнопками ниже. Если не знаешь, куда идти — нажми «📊 Главное».",
            reply_markup=MAIN_MENU,
        )

    async def _send_status(self, chat_id: int) -> None:
        with SessionLocal() as db:
            status = ControlService(db).all_status()
            experiment_stats = {}
            for product in status.products:
                for record in product.experiments:
                    if record.status in {"running", "active"}:
                        experiment_stats[str(record.id)] = ProspectService(db).stats_for_experiment(record.id)
            body = render_status(status, experiment_stats)
        await self._send(chat_id, body, reply_markup=STATUS_INLINE)

    async def _send_experiments(self, chat_id: int) -> None:
        with SessionLocal() as db:
            status = ControlService(db).all_status()
            body = render_experiments(status)
            buttons = []
            for product in status.products:
                for record in product.experiments:
                    prefix = "▶️" if record.status == "draft" else "📈"
                    key = record.experiment_key or str(record.id)
                    buttons.append(
                        [{"text": f"{prefix} {key}", "callback_data": f"experiment:open:{record.id}"}]
                    )
        buttons.extend(
            [
                [{"text": "📊 Главное", "callback_data": "menu:status"}],
                [{"text": "✅ На согласование", "callback_data": "menu:inbox"}],
                [{"text": "🔄 Обновить", "callback_data": "menu:experiments"}],
            ]
        )
        await self._send(
            chat_id,
            body,
            reply_markup={"inline_keyboard": buttons},
        )

    async def _send_experiment_detail(self, chat_id: int, experiment_id: UUID) -> None:
        with SessionLocal() as db:
            experiment = db.get(Experiment, experiment_id)
            if experiment is None:
                await self._send(chat_id, "Эксперимент не найден.")
                return
            prospect_stats = ProspectService(db).stats_for_experiment(experiment.id)
            body = render_experiment_detail(experiment, prospect_stats)
            keyboard = []
            if experiment.status == "draft":
                keyboard.append(
                    [{"text": "▶️ Запустить", "callback_data": f"experiment:start:{experiment.id}"}]
                )
            keyboard.extend(
                [
                    [{"text": "🧪 Ко всем тестам", "callback_data": "menu:experiments"}],
                    [{"text": "📊 Главное", "callback_data": "menu:status"}],
                ]
            )
        await self._send(chat_id, body, reply_markup={"inline_keyboard": keyboard})

    async def _send_launch_confirmation(self, chat_id: int, experiment_id: UUID) -> None:
        with SessionLocal() as db:
            experiment = db.get(Experiment, experiment_id)
            if experiment is None:
                await self._send(chat_id, "Эксперимент не найден.")
                return
            if experiment.status != "draft":
                await self._send(
                    chat_id,
                    f"Этот тест уже имеет состояние «{status_label(experiment.status)}».",
                    reply_markup={
                        "inline_keyboard": [
                            [{"text": "🧪 Тесты", "callback_data": "menu:experiments"}]
                        ]
                    },
                )
                return
            body = render_experiment_launch_confirmation(experiment)
        await self._send(
            chat_id,
            body,
            reply_markup={
                "inline_keyboard": [
                    [
                        {
                            "text": "✅ Подтвердить запуск",
                            "callback_data": f"experiment:confirm:{experiment_id}",
                        }
                    ],
                    [{"text": "↩️ Отмена", "callback_data": f"experiment:open:{experiment_id}"}],
                ]
            },
        )

    async def _send_inbox(self, chat_id: int) -> None:
        with SessionLocal() as db:
            control = ControlService(db)
            status = control.all_status()
            items = []
            for product in status.products:
                items.extend(control.inbox(product.product_id, limit=20))

        if not items:
            await self._send(
                chat_id,
                "✅ Сейчас от тебя ничего не требуется.\n\n"
                "Команда продолжает работу сама. Если появится действие, которое нельзя выполнять без тебя, оно появится здесь.",
                reply_markup={
                    "inline_keyboard": [
                        [{"text": "📊 Главное", "callback_data": "menu:status"}],
                        [{"text": "🔄 Обновить", "callback_data": "menu:inbox"}],
                    ]
                },
            )
            return

        for item in items:
            keyboard = []
            row = []
            for action in item.actions:
                if action.key == "open":
                    continue
                with SessionLocal() as db:
                    approval = db.get(Approval, item.id)
                    token = approval_review_token(db, approval) if approval else None
                callback_data = f"{action.key}:{item.id}"
                if token:
                    callback_data += f":{token}"
                row.append(
                    {
                        "text": action.label,
                        "callback_data": callback_data,
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
            keyboard.append(
                [
                    {"text": "📊 Главное", "callback_data": "menu:status"},
                    {"text": "🔄 Обновить", "callback_data": "menu:inbox"},
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

        if data.startswith(('contact:', 'mail:', 'e:')):
            self.content.topic_product = self.content.metrics_article = self.content.post_target = self.content.post_edit_target = None
            if callback_id:
                await self._answer_callback(callback_id, 'Открываю')
            await self.communications.callback(chat_id, data)
            return
        if data.startswith('content:'):
            self.communications.mail_step, self.communications.mail_draft = None, {}
            if callback_id:
                await self._answer_callback(callback_id, 'Открываю')
            await self.content.callback(chat_id, data)
            return

        if data.startswith("company:"):
            self.communications.mail_step, self.communications.mail_draft = None, {}
            self.content.topic_product = self.content.metrics_article = self.content.post_target = self.content.post_edit_target = None
            if callback_id:
                await self._answer_callback(callback_id, "Открываю")
            await self.company_intake.callback(chat_id, data)
            return

        if data.startswith("menu:"):
            target = data.split(":", 1)[1]
            if callback_id:
                await self._answer_callback(callback_id, "Обновляю")
            if target == "status":
                await self._send_status(chat_id)
            elif target == "inbox":
                await self._send_inbox(chat_id)
            elif target == "experiments":
                await self._send_experiments(chat_id)
            return

        if data.startswith("experiment:"):
            try:
                _, action, raw_id = data.split(":", 2)
                experiment_id = UUID(raw_id)
            except (ValueError, TypeError):
                if callback_id:
                    await self._answer_callback(callback_id, "Некорректный эксперимент")
                return

            if callback_id:
                await self._answer_callback(callback_id, "Открываю")
            if action == "open":
                await self._send_experiment_detail(chat_id, experiment_id)
                return
            if action == "start":
                await self._send_launch_confirmation(chat_id, experiment_id)
                return
            if action == "confirm":
                try:
                    with SessionLocal() as db:
                        result = ExperimentLaunchService(
                            db,
                            get_settings().products_root,
                        ).launch(
                            experiment_id,
                            actor=f"telegram:{self.owner_chat_id}",
                        )
                    await self._send(
                        chat_id,
                        (
                            "✅ Маркетинговый тест запущен.\n\n"
                            f"Компаний в базе: {result.prospect_count}.\n"
                            "ИИ-команда начала внутреннюю подготовку и проверку данных.\n"
                            "Письма и сообщения без отдельного согласования отправляться не будут."
                        ),
                        reply_markup={
                            "inline_keyboard": [
                                [
                                    {
                                        "text": "🧪 Открыть эксперимент",
                                        "callback_data": f"experiment:open:{experiment_id}",
                                    }
                                ],
                                [{"text": "📊 Главное", "callback_data": "menu:status"}],
                            ]
                        },
                    )
                except ExperimentLaunchError as exc:
                    await self._send(
                        chat_id,
                        "Не удалось запустить тест из-за неполных настроек. "
                        "Система не выполнила никаких внешних действий."
                    )
                except LookupError:
                    await self._send(chat_id, "Эксперимент не найден.")
                return

        try:
            parts = data.split(":")
            if len(parts) not in {2, 3}:
                raise ValueError("Invalid decision callback")
            action, raw_id = parts[:2]
            review_token = parts[2] if len(parts) == 3 else None
            approval_id = UUID(raw_id)
        except (ValueError, TypeError):
            if callback_id:
                await self._answer_callback(callback_id, "Некорректная команда")
            return

        if action == "open":
            package_messages = []
            with SessionLocal() as db:
                record = db.get(Approval, approval_id)
                if record is None:
                    detail_text = "Решение не найдено."
                else:
                    detail_text = (
                        "📋 Подробности решения\n\n"
                        f"Что предлагается: {action_label(record.action_type)}\n"
                        f"Состояние: {status_label(record.status)}\n"
                        f"Уровень контроля: {risk_label(record.risk_level)}"
                    )
                    reason = (record.reason or "").strip()
                    if reason and any("А" <= char <= "я" or char in "Ёё" for char in reason):
                        detail_text += f"\n\nПочему:\n{reason}"
                    if record.action_type in {"send_outreach_batch", "send_outreach_message"} and record.artifact_id:
                        artifact = db.get(Artifact, record.artifact_id)
                        if artifact is not None:
                            try:
                                package = json.loads(artifact.content)
                                package_messages = ([package] if record.action_type == "send_outreach_message" else package.get("messages") or [])
                            except json.JSONDecodeError:
                                package_messages = []
            await self._send(
                chat_id,
                detail_text,
                reply_markup={
                    "inline_keyboard": [
                        [{"text": "✅ К согласованиям", "callback_data": "menu:inbox"}],
                        [{"text": "📊 Главное", "callback_data": "menu:status"}],
                    ]
                },
            )
            for item in package_messages:
                subject = item.get("subject")
                header = (
                    f"✉️ {item.get('lead_id')} · {item.get('business_name')}\n"
                    f"Канал: {channel_label(item.get('channel'))} → {item.get('target')}"
                )
                if subject:
                    header += f"\nТема: {subject}"
                card_buttons = []
                if item.get('prospect_id'):
                    card_buttons.append([{'text':'📇 Открыть карточку контакта',
                        'callback_data':f"contact:open:{item['prospect_id']}"}])
                if item.get('from_email'):
                    header += f"\nОт: {item.get('from_name') or ''} <{item['from_email']}>"
                await self._send(
                    chat_id,
                    header + "\n\n" + str(item.get("body") or ""),
                    reply_markup={'inline_keyboard':card_buttons} if card_buttons else None,
                )
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
                    expected_review_token=review_token,
                )
            result_text = "✅ Одобрено" if record.status == "approved" else "❌ Отклонено"
        except ApprovalDecisionError as exc:
            result_text = "🔴 " + str(exc)
        except LookupError:
            result_text = "Решение не найдено."

        if callback_id:
            await self._answer_callback(callback_id, result_text)
        await self._send(
            chat_id,
            result_text,
            reply_markup={
                "inline_keyboard": [
                    [{"text": "✅ Следующее согласование", "callback_data": "menu:inbox"}],
                    [{"text": "📊 Главное", "callback_data": "menu:status"}],
                ]
            },
        )

    async def _send(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        # Telegram counts UTF-16 code units, not Python code points.
        chunks, current, units = [], '', 0
        for char in text:
            count = len(char.encode('utf-16-le')) // 2
            if units + count > 3900:
                chunks.append(current)
                current, units = '', 0
            current += char
            units += count
        if current:
            chunks.append(current)
        if len(chunks) > 1:
            for chunk in chunks[:-1]:
                await self.api.call('sendMessage', {'chat_id':chat_id, 'text':chunk})
        payload = {"chat_id": chat_id, "text": chunks[-1] if chunks else ' ', 'link_preview_options': {'is_disabled':True}}
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
        TelegramAPI(settings.telegram_bot_token, settings.telegram_proxy_url),
        settings.telegram_owner_chat_id,
        settings.telegram_poll_timeout_seconds,
    )
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
