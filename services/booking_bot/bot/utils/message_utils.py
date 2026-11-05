"""
Утилиты для работы с сообщениями
"""
from aiogram.types import CallbackQuery, Message
from aiogram.exceptions import TelegramBadRequest


async def safe_edit_or_send(
    callback: CallbackQuery,
    text: str,
    parse_mode: str = "HTML",
    reply_markup=None,
    disable_web_page_preview: bool = False
) -> Message:
    """
    Безопасно редактирует сообщение или отправляет новое.
    Работает с текстовыми сообщениями и сообщениями с медиа (фото и т.д.)

    Args:
        callback: Callback query
        text: Текст сообщения
        parse_mode: Режим парсинга (HTML, Markdown)
        reply_markup: Клавиатура
        disable_web_page_preview: Отключить превью ссылок

    Returns:
        Message: Отправленное или отредактированное сообщение
    """
    message = callback.message

    # Пробуем редактировать текст
    try:
        return await message.edit_text(
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview
        )
    except TelegramBadRequest as e:
        # Если сообщение - медиа (фото, документ и т.д.)
        if "there is no text in the message" in str(e).lower():
            # Удаляем старое сообщение и отправляем новое
            try:
                await message.delete()
            except:
                pass

            return await message.answer(
                text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview
            )
        # Если сообщение не изменилось
        elif "message is not modified" in str(e).lower():
            return message
        else:
            raise
