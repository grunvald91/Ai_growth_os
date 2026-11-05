"""Telegram ownership proof; never merge from a bare browser-supplied Telegram ID."""
import html

from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database import async_session_maker
from database.account_link import telegram_claim, telegram_confirm, AccountLinkError
from database.models import EmailIdentity
from sqlalchemy import select

router = Router()


def private(message):
    return message.chat.type == "private"


@router.message(CommandStart(deep_link=True), F.text.startswith("/start account_"))
async def begin_link(message, command: CommandObject):
    if not private(message):
        await message.answer("Откройте ссылку в личном чате с ботом")
        return
    token = (command.args or "").removeprefix("account_")
    async with async_session_maker() as db:
        try:
            request = await telegram_claim(db, token, message.from_user)
        except AccountLinkError as exc:
            await message.answer(str(exc)); return
        identity = await db.scalar(select(EmailIdentity).where(EmailIdentity.user_id == request.user_id))
        if identity is None or identity.session_version != request.email_session_version:
            await message.answer("Email-сессия изменилась. Запросите новую ссылку в кабинете"); return
        # A forwarded link does not silently bind the person who opens it.
        await db.commit()
        local, domain = identity.email.split("@", 1)
        masked = html.escape(local[:1] + "***@" + domain)
        await message.answer(f"Связать этот Telegram с вашим аккаунтом ServGo ({masked})?\n"
            "Подтверждайте только если сами начали привязку в кабинете. После этого вернитесь на сайт для окончательного объединения.",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Подтвердить мой Telegram", callback_data=f"account_link:{request.id}")]]))


@router.callback_query(F.data.startswith("account_link:"))
async def confirm_link(callback):
    if callback.message is None or not private(callback.message):
        await callback.answer("Подтверждение доступно в личном чате", show_alert=True); return
    request_id = callback.data.split(":", 1)[1]
    async with async_session_maker() as db:
        try:
            await telegram_confirm(db, request_id, callback.from_user.id)
        except AccountLinkError as exc:
            await callback.answer(str(exc), show_alert=True); return
        await db.commit()
    await callback.message.edit_text("Telegram подтверждён. Вернитесь в кабинет ServGo, проверьте аккаунт и завершите объединение.")
    await callback.answer("Telegram подтверждён")
