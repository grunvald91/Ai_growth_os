"""
Обработчики для управления владельцами (доступно только для супер-админа ID: 437862772)
"""
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.keyboards import admin_kb
from bot.config import ADMIN_IDS

router = Router()

# ID супер-администратора
SUPER_ADMIN_ID = 437862772


class AdminManagementStates(StatesGroup):
    """Состояния для управления владельцами"""
    adding_admin = State()


@router.message(F.text == "👥 Управление владельцами")
async def show_admin_management(message: Message):
    """Показать меню управления владельцами"""
    # Проверяем, является ли пользователь супер-админом
    if message.from_user.id != SUPER_ADMIN_ID:
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    admin_list = "\n".join([f"• {admin_id}" for admin_id in ADMIN_IDS])

    await message.answer(
        f"👥 <b>Управление владельцами</b>\n\n"
        f"<b>Текущие владельцы:</b>\n{admin_list if admin_list else 'Нет владельцев'}",
        parse_mode="HTML",
        reply_markup=admin_kb.get_admin_management_keyboard()
    )


@router.callback_query(F.data == "manage_admins")
async def manage_admins_callback(callback: CallbackQuery):
    """Вернуться к меню управления владельцами"""
    if callback.from_user.id != SUPER_ADMIN_ID:
        await callback.answer("❌ У вас нет доступа к этой функции.", show_alert=True)
        return

    admin_list = "\n".join([f"• {admin_id}" for admin_id in ADMIN_IDS])

    await callback.message.edit_text(
        f"👥 <b>Управление владельцами</b>\n\n"
        f"<b>Текущие владельцы:</b>\n{admin_list if admin_list else 'Нет владельцев'}",
        parse_mode="HTML",
        reply_markup=admin_kb.get_admin_management_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "add_admin")
async def add_admin_start(callback: CallbackQuery, state: FSMContext):
    """Начать процесс добавления владельца"""
    if callback.from_user.id != SUPER_ADMIN_ID:
        await callback.answer("❌ У вас нет доступа к этой функции.", show_alert=True)
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад", callback_data="manage_admins")

    await callback.message.edit_text(
        "➕ <b>Добавление владельца</b>\n\n"
        "Отправьте Telegram ID пользователя, которого хотите сделать владельцем.\n\n"
        "Пользователь может узнать свой ID командой /help в боте.",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await state.set_state(AdminManagementStates.adding_admin)
    await callback.answer()


@router.message(AdminManagementStates.adding_admin)
async def add_admin_process(message: Message, state: FSMContext):
    """Обработка добавления владельца"""
    if message.from_user.id != SUPER_ADMIN_ID:
        await message.answer("❌ У вас нет доступа к этой функции.")
        await state.clear()
        return

    try:
        new_admin_id = int(message.text.strip())

        if new_admin_id in ADMIN_IDS:
            await message.answer(
                f"⚠️ Пользователь {new_admin_id} уже является владельцем.",
                reply_markup=admin_kb.get_admin_management_keyboard()
            )
        else:
            # Добавляем ID в список
            ADMIN_IDS.append(new_admin_id)

            # Обновляем .env файл
            update_env_admin_ids()

            # Устанавливаем пользователя как manual owner (без ограничений по тарифу)
            from database import async_session_maker
            from database.crud import set_user_as_manual_owner, get_or_create_user

            async with async_session_maker() as session:
                # Создаём пользователя если его нет
                await get_or_create_user(
                    session,
                    telegram_id=new_admin_id,
                    username=None,
                    full_name=f"Admin {new_admin_id}"
                )
                # Устанавливаем как manual owner
                await set_user_as_manual_owner(session, new_admin_id)

            admin_list = "\n".join([f"• {admin_id}" for admin_id in ADMIN_IDS])

            await message.answer(
                f"✅ Владелец {new_admin_id} успешно добавлен!\n\n"
                f"<b>Без ограничений по тарифу</b> — может создавать любое количество бизнесов.\n\n"
                f"<b>Текущие владельцы:</b>\n{admin_list}",
                parse_mode="HTML",
                reply_markup=admin_kb.get_admin_management_keyboard()
            )

        await state.clear()

    except ValueError:
        await message.answer(
            "❌ Неверный формат. Пожалуйста, отправьте числовой Telegram ID.",
            reply_markup=admin_kb.get_admin_management_keyboard()
        )
        await state.clear()


@router.callback_query(F.data == "remove_admin")
async def remove_admin_start(callback: CallbackQuery):
    """Показать список владельцев для удаления"""
    if callback.from_user.id != SUPER_ADMIN_ID:
        await callback.answer("❌ У вас нет доступа к этой функции.", show_alert=True)
        return

    if not ADMIN_IDS:
        await callback.message.edit_text(
            "❌ Нет владельцев для удаления.",
            reply_markup=admin_kb.get_admin_management_keyboard()
        )
        await callback.answer()
        return

    # Получаем информацию о пользователях
    from database import async_session_maker
    from database.crud import get_user_by_telegram_id

    admin_info = {}
    async with async_session_maker() as session:
        for admin_id in ADMIN_IDS:
            user = await get_user_by_telegram_id(session, admin_id)
            if user:
                # Используем username если есть, иначе full_name
                name = f"@{user.username}" if user.username else user.full_name
                admin_info[admin_id] = f"{name} (ID: {admin_id})"
            else:
                admin_info[admin_id] = f"ID: {admin_id}"

    await callback.message.edit_text(
        "🗑 <b>Удаление владельца</b>\n\n"
        "Выберите владельца для удаления:",
        parse_mode="HTML",
        reply_markup=admin_kb.get_admin_list_keyboard(admin_info)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delete_admin:"))
async def remove_admin_confirm(callback: CallbackQuery):
    """Подтверждение удаления владельца"""
    if callback.from_user.id != SUPER_ADMIN_ID:
        await callback.answer("❌ У вас нет доступа к этой функции.", show_alert=True)
        return

    admin_id_to_remove = int(callback.data.split(":")[1])

    # Защита от удаления самого супер-админа
    if admin_id_to_remove == SUPER_ADMIN_ID:
        await callback.answer("❌ Невозможно удалить супер-администратора.", show_alert=True)
        return

    if admin_id_to_remove in ADMIN_IDS:
        ADMIN_IDS.remove(admin_id_to_remove)

        # Обновляем .env файл
        update_env_admin_ids()

        admin_list = "\n".join([f"• {admin_id}" for admin_id in ADMIN_IDS])

        await callback.message.edit_text(
            f"✅ Владелец {admin_id_to_remove} успешно удален!\n\n"
            f"<b>Текущие владельцы:</b>\n{admin_list if admin_list else 'Нет владельцев'}",
            parse_mode="HTML",
            reply_markup=admin_kb.get_admin_management_keyboard()
        )
    else:
        await callback.message.edit_text(
            f"❌ Владелец {admin_id_to_remove} не найден.",
            reply_markup=admin_kb.get_admin_management_keyboard()
        )

    await callback.answer()


@router.callback_query(F.data == "back_to_admin_menu")
async def back_to_admin_menu(callback: CallbackQuery, state: FSMContext):
    """Вернуться в главное меню владельца"""
    await state.clear()

    # Получаем роли пользователя
    from database import async_session_maker, get_employee_by_telegram_id

    async with async_session_maker() as session:
        employee = await get_employee_by_telegram_id(session, callback.from_user.id)

    is_super_admin = callback.from_user.id == SUPER_ADMIN_ID

    # Удаляем сообщение с управлением владельцами
    try:
        await callback.message.delete()
    except:
        pass

    # Отправляем новое сообщение с панелью владельца
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text="🏢 Панель владельца\n\nЗдесь вы можете управлять своими бизнесами, услугами, сотрудниками и просматривать записи клиентов.",
        reply_markup=admin_kb.get_admin_menu_keyboard(
            show_employee_button=(employee is not None),
            is_super_admin=is_super_admin
        )
    )
    await callback.answer()


def update_env_admin_ids():
    """Обновить список владельцев в .env файле"""
    import os
    from pathlib import Path
    import logging

    logger = logging.getLogger(__name__)

    # Получаем абсолютный путь к корню проекта (где лежит .env)
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent.parent  # handlers -> bot -> booking_bot
    env_path = project_root / ".env"

    logger.info(f"Обновление .env файла: {env_path}")

    if not env_path.exists():
        logger.error(f".env файл не найден: {env_path}")
        return

    try:
        # Читаем текущий .env файл
        with open(env_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Обновляем строку ADMIN_IDS
        admin_ids_str = ','.join(map(str, ADMIN_IDS))
        updated = False

        for i, line in enumerate(lines):
            if line.strip().startswith('ADMIN_IDS='):
                lines[i] = f'ADMIN_IDS={admin_ids_str}\n'
                updated = True
                break

        # Если строки нет, добавляем
        if not updated:
            lines.append(f'ADMIN_IDS={admin_ids_str}\n')

        # Записываем обратно
        with open(env_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)

        logger.info(f"ADMIN_IDS успешно обновлен: {admin_ids_str}")

    except Exception as e:
        logger.error(f"Ошибка при обновлении .env файла: {e}")
