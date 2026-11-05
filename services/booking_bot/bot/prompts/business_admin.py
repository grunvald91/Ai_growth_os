"""System prompt builder for AI admin."""
import json
from typing import List
from database.models import Business


def _biz_block(business: Business) -> str:
    """One-business settings block."""
    try:
        photos_count = len(json.loads(business.photos)) if business.photos else 0
    except Exception:
        photos_count = 0

    break_info = "нет"
    if business.break_start and business.break_end:
        break_info = f"{business.break_start.strftime('%H:%M')}–{business.break_end.strftime('%H:%M')}"

    prepayment_info = "выключена"
    if business.prepayment_enabled:
        prepayment_info = f"включена {business.prepayment_percent}%"

    wh_start = business.working_hours_start.strftime('%H:%M') if business.working_hours_start else "?"
    wh_end = business.working_hours_end.strftime('%H:%M') if business.working_hours_end else "?"

    block = (
        f"━━━ ТЕКУЩИЕ НАСТРОЙКИ БИЗНЕСА ━━━\n"
        f"ID: {business.id}\n"
        f"Название: {business.name}\n"
        f"Описание: {business.description or 'не задано'}\n"
        f"Адрес: {business.address or 'не задан'}\n"
        f"Телефон: {business.phone or 'не задан'}\n"
        f"Часы работы: {wh_start}–{wh_end}\n"
        f"Перерыв: {break_info}\n"
        f"Фото в галерее: {photos_count}\n"
        f"Видео: {'есть' if business.video else 'нет'}\n"
        f"Схема проезда: {'есть' if business.directions else 'нет'}\n"
        f"Предоплата: {prepayment_info}\n"
        f"Реквизиты: {business.payment_details or 'не заданы'}"
    )
    if business.ai_instruction:
        block += "\n\n━━━ ИНСТРУКЦИЯ ПО ЭТОМУ БИЗНЕСУ ━━━\n" + business.ai_instruction
    return block


_OWNER_RULES = (
    "━━━ ПРАВИЛА ━━━\n"
    "- Хочет изменить настройку — сразу вызывай функцию, НЕ проси словесного подтверждения (придут кнопки).\n"
    "- Хочет посмотреть записи — вызывай get_bookings() с нужными фильтрами.\n"
    "- Хочет записать клиента — спроси имя и телефон клиента, вызови get_business_services, затем create_booking_for_client.\n"
    "- Прислал фото с подписью — определи по подписи что делать и вызови нужную функцию.\n"
    "- Непонятный запрос — задай ОДИН короткий уточняющий вопрос.\n"
    "- Перед рассылкой — сообщи сколько получателей.\n"
    "- Отвечай кратко, без предисловий.\n"
    "- Язык: русский, стиль деловой но дружелюбный.\n"
    "- На вопросы не по теме: «Я ваш AI-администратор, работаю только с настройками бизнеса»."
)


def build_system_prompt(business: Business) -> str:
    """Build dynamic system prompt for single business."""
    return (
        f"Ты — AI-администратор бизнеса «{business.name}» в Telegram-боте для онлайн-записи.\n"
        f"Помогаешь владельцу управлять настройками, записями и коммуникацией через естественный диалог.\n\n"
        f"{_biz_block(business)}\n\n"
        f"{_OWNER_RULES}"
    )


def build_system_prompt_multi(businesses: List[Business]) -> str:
    """Build dynamic system prompt when owner has multiple businesses."""
    biz_list = "\n".join(
        f"  • ID={b.id}: «{b.name}»"
        + (f" — {b.address}" if b.address else "")
        for b in businesses
    )

    details = "\n\n".join(
        f"─── Бизнес ID={b.id} «{b.name}» ───\n{_biz_block(b)}"
        for b in businesses
    )

    return (
        "Ты — AI-администратор в Telegram-боте для онлайн-записи.\n"
        "Помогаешь владельцу управлять бизнесами через естественный диалог.\n\n"
        f"━━━ БИЗНЕСЫ ВЛАДЕЛЬЦА ━━━\n{biz_list}\n\n"
        f"{details}\n\n"
        "━━━ ПРАВИЛА ━━━\n"
                "- ОБЯЗАТЕЛЬНО: в каждом вызове функции указывай business_id нужного бизнеса.\n"
        "- Если неясно какой бизнес — задай ОДИН уточняющий вопрос.\n"
        "- Хочет изменить настройку — сразу вызывай функцию с правильным business_id.\n"
        "- Хочет посмотреть записи — вызывай get_bookings() с нужными фильтрами и business_id.\n"
        "- Хочет записать клиента — спроси имя и телефон клиента, вызови get_business_services, затем create_booking_for_client.\n"
        "- Прислал фото — определи по подписи что делать и вызови нужную функцию.\n"
        "- Перед рассылкой — сообщи сколько получателей.\n"
        "- Отвечай кратко, без предисловий.\n"
        "- Язык: русский, стиль деловой но дружелюбный."
    )
