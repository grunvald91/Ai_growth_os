from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def confirm_keyboard(action_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Применить", callback_data=f"ai_confirm:{action_id}")
    b.button(text="❌ Отмена",    callback_data="ai_cancel")
    b.adjust(2)
    return b.as_markup()
