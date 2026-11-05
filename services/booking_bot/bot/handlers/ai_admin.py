"""
AI Admin catch-all handler.
Перехватывает свободный текст и медиа:
  - от владельцев бизнесов -> owner flow (управление настройками, записями)
  - от клиентов -> client flow (бронирование через диалог)
"""
import logging
import uuid
from typing import List

from aiogram import Router, F, Bot
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select

from database.database import async_session_maker
from database.models import Business, User
from database.crud import get_user_businesses
from bot.prompts.business_admin import build_system_prompt, build_system_prompt_multi
from bot.prompts.client_admin import build_client_system_prompt
from bot.services.ai_admin import process_message, process_tool_result, store_pending_action, pop_pending_action
from bot.services.ai_admin_tools import TOOL_SCHEMAS, READ_ONLY_TOOLS, describe_tool_call
from bot.services.ai_admin_executor import execute_tool
from bot.services.client_admin_tools import (
    CLIENT_TOOL_SCHEMAS, CLIENT_READ_ONLY_TOOLS, describe_client_tool_call,
)
from bot.services.client_admin_executor import execute_client_tool, get_client_context
from bot.keyboards.ai_admin_kb import confirm_keyboard

def _safe_text(text):
    """Remove surrogate characters that break Telegram API."""
    if not text:
        return text or ""
    if isinstance(text, str):
        return text.encode("utf-8", "replace").decode("utf-8")
    return str(text)


router = Router()
logger = logging.getLogger(__name__)

# Prefix for client history (to avoid collision with owner history)
_CLIENT_PREFIX = 900_000_000


async def _owner_businesses(telegram_id: int) -> List[Business]:
    """Return all active businesses of this owner (via both junction table and legacy owner_id)."""
    async with async_session_maker() as session:
        res = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = res.scalar_one_or_none()
        if not user:
            return []
        return await get_user_businesses(session, user.id)


async def _fresh(business_id: int):
    async with async_session_maker() as session:
        res = await session.execute(select(Business).where(Business.id == business_id))
        return res.scalar_one_or_none()


# --- CATCH-ALL ---------------------------------------------------------------

@router.message(StateFilter(None), F.content_type.in_({"text", "photo", "document"}))
async def ai_message(message: Message, bot: Bot, state: FSMContext):
    if message.text and message.text.startswith("/"):
        return

    state_data = await state.get_data()
    if state_data.get("current_mode") == "client":
        await _handle_client(message, bot)
        return

    businesses = await _owner_businesses(message.from_user.id)
    if businesses:
        await _handle_owner(message, bot, businesses)
    else:
        await _handle_client(message, bot)


# --- OWNER FLOW --------------------------------------------------------------

async def _handle_owner(message: Message, bot: Bot, businesses: List[Business]):
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
        caption = message.caption or "фото без описания"
        user_content = "Владелец прислал фото (file_id=" + file_id + "). Подпись: " + caption
    elif message.document:
        file_id = message.document.file_id
        caption = message.caption or "файл без описания"
        user_content = (
            "Владелец прислал файл (file_id=" + file_id +
            ", name=" + (message.document.file_name or "?") + "). Подпись: " + caption
        )
    else:
        user_content = message.text

    # Build system prompt (single vs multi business)
    if len(businesses) == 1:
        fresh_biz = await _fresh(businesses[0].id)
        system_prompt = build_system_prompt(fresh_biz)
        default_biz_id = businesses[0].id
    else:
        fresh_list = []
        for b in businesses:
            fb = await _fresh(b.id)
            if fb:
                fresh_list.append(fb)
        system_prompt = build_system_prompt_multi(fresh_list)
        default_biz_id = 0  # AI must specify business_id in each tool call

    await bot.send_chat_action(message.chat.id, "typing")

    try:
        result = await process_message(
            user_id=message.from_user.id,
            system_prompt=system_prompt,
            user_content=user_content,
            tools=TOOL_SCHEMAS,
        )
    except Exception as exc:
        logger.error("AI owner error (user=%s): %s", message.from_user.id, exc, exc_info=True)
        await message.answer("\u26a0\ufe0f Ошибка AI. Попробуйте ещё раз.")
        return

    if isinstance(result, str):
        await message.answer(_safe_text(result))
        return

    tool_name = result["name"]
    tool_args = result["arguments"]

    # Determine which business this tool call targets
    effective_biz_id = int(tool_args.get("business_id") or default_biz_id or businesses[0].id)

    if tool_name in READ_ONLY_TOOLS:
        await message.answer(await execute_tool(tool_name, tool_args, effective_biz_id, bot, file_id))
        return

    action_id = str(uuid.uuid4())[:8]
    store_pending_action(action_id, tool_name, tool_args, effective_biz_id, file_id)
    await message.answer(
        "\U0001f916 Предлагаю:\n\n" + describe_tool_call(tool_name, tool_args),
        reply_markup=confirm_keyboard(action_id),
    )


# --- CLIENT FLOW -------------------------------------------------------------

async def _handle_client(message: Message, bot: Bot):
    client_id, context = await get_client_context(message.from_user.id)
    if client_id is None:
        return  # unregistered user — ignore

    businesses = context.get('businesses', [])

    if message.photo:
        user_content = "Клиент прислал фото. Подпись: " + (message.caption or "без описания")
    elif message.document:
        user_content = "Клиент прислал файл: " + (message.document.file_name or "файл")
    else:
        user_content = message.text

    system_prompt = build_client_system_prompt(context)
    history_key = _CLIENT_PREFIX + message.from_user.id

    await bot.send_chat_action(message.chat.id, "typing")

    try:
        result = await process_message(
            user_id=history_key,
            system_prompt=system_prompt,
            user_content=user_content,
            tools=CLIENT_TOOL_SCHEMAS,
        )
    except Exception as exc:
        logger.error("AI client error (user=%s): %s", message.from_user.id, exc, exc_info=True)
        await message.answer("\u26a0\ufe0f Ошибка AI. Попробуйте ещё раз.")
        return

    # Агентный цикл: автоматически выполняем read-only tools и продолжаем диалог
    for _iter in range(6):
        if isinstance(result, str):
            await message.answer(_safe_text(result))
            return

        tool_name = result["name"]
        tool_args = result["arguments"]

        if tool_name not in CLIENT_READ_ONLY_TOOLS:
            break

        # Выполнить read-only tool и передать результат обратно AI
        tool_output = await execute_client_tool(
            tool_name, tool_args, client_id,
            client_telegram_id=message.from_user.id, bot=bot,
        )
        logger.info("Auto-executed tool %s, feeding result back to AI", tool_name)
        try:
            result = await process_tool_result(
                user_id=history_key,
                system_prompt=system_prompt,
                tool_call_id=result["tool_call_id"],
                tool_name=tool_name,
                tool_output=tool_output,
                tools=CLIENT_TOOL_SCHEMAS,
            )
        except Exception as exc:
            logger.error("AI tool loop error (user=%s): %s", message.from_user.id, exc, exc_info=True)
            await message.answer("\u26a0\ufe0f Ошибка AI. Попробуйте ещё раз.")
            return

    if isinstance(result, str):
        await message.answer(_safe_text(result))
        return

    tool_name = result["name"]
    tool_args = result["arguments"]

    # Validate create_booking has required fields
    if tool_name == "create_booking":
        missing = []
        if not tool_args.get("service_id"):
            missing.append("услугу")
        if not tool_args.get("date"):
            missing.append("дату")
        if not tool_args.get("time"):
            missing.append("время")
        if missing:
            # Feed error back to AI so it asks the client
            error_msg = f"Ошибка: не указано: {', '.join(missing)}. Уточни у клиента."
            try:
                result = await process_tool_result(
                    user_id=history_key,
                    system_prompt=system_prompt,
                    tool_call_id=result["tool_call_id"],
                    tool_name=tool_name,
                    tool_output=error_msg,
                    tools=CLIENT_TOOL_SCHEMAS,
                )
                if isinstance(result, str):
                    await message.answer(_safe_text(result))
                    return
                # If AI calls create_booking again with data, continue below
                tool_name = result["name"]
                tool_args = result["arguments"]
            except Exception as exc:
                logger.error("Validation loop error: %s", exc)
                await message.answer("Уточните услугу, дату и время для записи.")
                return

    # Confirm required — show cancellation policy photo before create_booking
    if tool_name == "create_booking":
        biz_id = tool_args.get("business_id")
        if biz_id:
            async with async_session_maker() as _s:
                from sqlalchemy import select as _sel
                from database.models import Business as _Biz
                _res = await _s.execute(_sel(_Biz).where(_Biz.id == biz_id))
                _biz = _res.scalar_one_or_none()
            if _biz and _biz.cancellation_policy_photo:
                try:
                    await bot.send_photo(
                        message.chat.id,
                        photo=_biz.cancellation_policy_photo,
                        caption="\U0001f4cb Условия отмены бронирования — ознакомьтесь перед подтверждением"
                    )
                except Exception:
                    pass

    # Store pending action — client_id:telegram_id in file_id field
    action_id = str(uuid.uuid4())[:8]
    store_pending_action(
        action_id, tool_name, tool_args,
        business_id=0,
        file_id=str(client_id) + ":" + str(message.from_user.id),
    )
    await message.answer(
        "\U0001f916 Подтвердите:\n\n" + describe_client_tool_call(tool_name, tool_args),
        reply_markup=confirm_keyboard(action_id),
    )


# --- CALLBACKS ---------------------------------------------------------------

@router.callback_query(F.data.startswith("ai_confirm:"))
async def ai_confirm(callback: CallbackQuery, bot: Bot):
    action_id = callback.data.split(":", 1)[1]
    action = pop_pending_action(action_id)
    if not action:
        await callback.answer("Действие устарело", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        return
    await callback.message.edit_reply_markup(reply_markup=None)

    if action["business_id"] > 0:
        # Owner action
        result = await execute_tool(
            action["name"], action["args"],
            action["business_id"], bot, action.get("file_id"),
        )
    else:
        # Client action — parse "client_id:telegram_id"
        parts = (action.get("file_id") or "0:0").split(":", 1)
        client_id = int(parts[0])
        client_tg_id = int(parts[1]) if len(parts) > 1 else 0
        result = await execute_client_tool(
            action["name"], action["args"],
            client_id, client_telegram_id=client_tg_id, bot=bot,
        )

    await callback.message.answer(_safe_text(result))
    await callback.answer()


@router.callback_query(F.data == "ai_cancel")
async def ai_cancel(callback: CallbackQuery):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Отменено")
