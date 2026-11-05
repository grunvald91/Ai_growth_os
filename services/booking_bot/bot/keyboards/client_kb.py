"""
Клавиатуры для клиентской части бота
"""
from typing import List, Union
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import Business, Service, Employee
from bot.utils.business_types import get_business_type_name, get_terminology, is_venue_rental_type


def get_mode_switch_keyboard(show_owner_button: bool = False, show_employee_button: bool = False, show_client_button: bool = False) -> ReplyKeyboardMarkup:
    """Минимальная клавиатура только с кнопками переключения режима

    Args:
        show_owner_button: показывать кнопку "Режим владельца"
        show_employee_button: показывать кнопку "Режим сотрудника"
        show_client_button: показывать кнопку "Режим клиента"
    """
    mode_buttons = []

    if show_client_button:
        mode_buttons.append([KeyboardButton(text="👤 Режим клиента")])
    if show_employee_button:
        mode_buttons.append([KeyboardButton(text="💼 Режим сотрудника")])
    if show_owner_button:
        mode_buttons.append([KeyboardButton(text="🏢 Режим владельца")])

    # Если нет кнопок переключения, создаем минимальную клавиатуру
    if not mode_buttons:
        mode_buttons = [[KeyboardButton(text="🏠 Главное меню")]]

    keyboard = ReplyKeyboardMarkup(
        keyboard=mode_buttons,
        resize_keyboard=True
    )
    return keyboard


def get_main_menu_keyboard(show_owner_button: bool = False, show_employee_button: bool = False) -> ReplyKeyboardMarkup:
    """Главное меню для клиента

    Args:
        show_owner_button: показывать кнопку "Режим владельца"
        show_employee_button: показывать кнопку "Режим сотрудника"
    """
    buttons = [
        [KeyboardButton(text="📅 Записаться"), KeyboardButton(text="🔄 Повторить запись")],
        [KeyboardButton(text="📋 Мои записи"), KeyboardButton(text="🎫 Мои абонементы")],
        [KeyboardButton(text="ℹ️ Помощь")],
    ]

    # Добавляем кнопки переключения режима
    mode_buttons = []
    if show_employee_button:
        mode_buttons.append(KeyboardButton(text="💼 Режим сотрудника"))
    if show_owner_button:
        mode_buttons.append(KeyboardButton(text="🏢 Режим владельца"))

    if mode_buttons:
        buttons.append(mode_buttons)

    keyboard = ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True
    )
    return keyboard


def get_employee_mode_selection_keyboard(is_master: bool = False, is_administrator: bool = False, show_owner_button: bool = False) -> ReplyKeyboardMarkup:
    """Клавиатура выбора подрежима сотрудника (Мастер/Администратор)

    Args:
        is_master: пользователь является мастером
        is_administrator: пользователь является администратором
        show_owner_button: показывать кнопку "Режим владельца"
    """
    buttons = []

    # Показываем только доступные роли
    if is_master:
        buttons.append([KeyboardButton(text="👨‍🔧 Режим мастера")])
    if is_administrator:
        buttons.append([KeyboardButton(text="👔 Режим администратора")])

    buttons.append([KeyboardButton(text="ℹ️ Помощь")])

    # Кнопки переключения на другие режимы
    mode_buttons = [KeyboardButton(text="👤 Режим клиента")]
    if show_owner_button:
        mode_buttons.append(KeyboardButton(text="🏢 Режим владельца"))
    buttons.append(mode_buttons)

    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def get_businesses_keyboard(businesses: List[Business], page: int = 0, per_page: int = 10) -> InlineKeyboardMarkup:
    """Клавиатура для выбора бизнеса с пагинацией

    Args:
        businesses: список бизнесов
        page: текущая страница (начиная с 0)
        per_page: количество бизнесов на странице
    """
    builder = InlineKeyboardBuilder()

    total = len(businesses)
    total_pages = (total + per_page - 1) // per_page
    start = page * per_page
    end = min(start + per_page, total)

    # Показываем бизнесы текущей страницы
    for business in businesses[start:end]:
        business_type = get_business_type_name(business.business_type)
        builder.button(
            text=f"{business.name} ({business_type})",
            callback_data=f"business:{business.id}"
        )

    # Кнопки навигации (если больше одной страницы)
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(("◀️", f"biz_page:{page-1}"))
        nav_buttons.append((f"{page+1}/{total_pages}", "noop"))
        if page < total_pages - 1:
            nav_buttons.append(("▶️", f"biz_page:{page+1}"))

        for text, callback in nav_buttons:
            builder.button(text=text, callback_data=callback)

    builder.button(text="🏠 Главное меню", callback_data="client_main_menu")

    # Настройка раскладки
    adjust = [1] * (end - start)  # По 1 кнопке для бизнесов
    if total_pages > 1:
        adjust.append(len([b for b in [page > 0, True, page < total_pages - 1] if b]))  # Навигация
    adjust.append(1)  # Главное меню

    builder.adjust(*adjust)
    return builder.as_markup()


def get_services_keyboard(services: List[Service], selected_ids: List[int] = None) -> InlineKeyboardMarkup:
    """Клавиатура для выбора услуг (мультивыбор)"""
    builder = InlineKeyboardBuilder()
    selected_ids = selected_ids or []

    for service in services:
        is_contact_only = hasattr(service, 'tariff_description') and service.tariff_description
        if is_contact_only:
            info_text = " (по запросу)"
        else:
            price_text = f" - {service.price}₽" if service.price else ""
            # Время: показываем только если > 0
            duration_text = f"{service.duration_minutes} мин" if service.duration_minutes and service.duration_minutes > 0 else ""
            # Форматируем скобки
            if duration_text and price_text:
                info_text = f" ({duration_text}{price_text})"
            elif duration_text:
                info_text = f" ({duration_text})"
            elif price_text:
                info_text = f" ({service.price}₽)"
            else:
                info_text = ""
        # Отмечаем выбранные услуги галочкой
        check = "✅ " if service.id in selected_ids else ""
        builder.button(
            text=f"{check}{service.name}{info_text}",
            callback_data=f"service:{service.id}"
        )

    # Кнопка "Готово" появляется только если выбрана хотя бы одна услуга
    if selected_ids:
        builder.button(text="◀️ Назад", callback_data="back_to_businesses")
        builder.button(text="✅ Готово", callback_data="services_done", style="success")
        # Раскладка: все услуги по 1 в ряд, затем "Назад" и "Готово" в одном ряду
        builder.adjust(*([1] * len(services)), 2)
    else:
        builder.button(text="◀️ Назад", callback_data="back_to_businesses")
        builder.adjust(1)

    return builder.as_markup()


def get_employees_keyboard(employees: List[Employee], business_type: str = None) -> InlineKeyboardMarkup:
    """Клавиатура для выбора сотрудника/помещения"""
    builder = InlineKeyboardBuilder()

    is_venue = is_venue_rental_type(business_type) if business_type else False
    if is_venue:
        any_text = "🏠 Любое помещение"
    else:
        any_text = "👤 Любой мастер"

    builder.button(text=any_text, callback_data="employee:any")

    for employee in employees:
        spec_text = f" - {employee.specialization}" if employee.specialization else ""
        builder.button(
            text=f"{employee.name}{spec_text}",
            callback_data=f"employee:{employee.id}"
        )

    builder.button(text="◀️ Назад", callback_data="back_to_services")
    builder.button(text="🏠 Главное меню", callback_data="client_main_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_back_to_services_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для возврата к выбору услуг (когда нет подходящих мастеров)"""
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Изменить выбор услуг", callback_data="back_to_services")
    builder.button(text="🏠 Главное меню", callback_data="client_main_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_dates_keyboard(available_dates: List[Union[str, dict]]) -> InlineKeyboardMarkup:
    """Клавиатура для выбора даты (устаревшая, для совместимости)"""
    builder = InlineKeyboardBuilder()

    for date_info in available_dates:
        if isinstance(date_info, dict):
            builder.button(
                text=date_info['display'],
                callback_data=f"date:{date_info['value']}"
            )
        else:
            builder.button(
                text=date_info,
                callback_data=f"date:{date_info}"
            )

    builder.button(text="◀️ Назад", callback_data="back_to_employees")
    builder.adjust(4)
    return builder.as_markup()


def get_booking_calendar_keyboard(
    year: int,
    month: int,
    exclude_dates: List = None,
    callback_prefix: str = "date",
    back_callback: str = "back_to_employees"
) -> InlineKeyboardMarkup:
    """Календарь для выбора даты записи

    Args:
        year: год
        month: месяц
        exclude_dates: список дат (date), которые недоступны (выходные мастера)
        callback_prefix: префикс для callback_data (date, admin_date и т.д.)
        back_callback: callback для кнопки "Назад"
    """
    import calendar
    from datetime import date, timedelta

    builder = InlineKeyboardBuilder()
    exclude_dates = exclude_dates or []

    # Названия месяцев
    month_names = [
        "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
    ]

    # Заголовок с месяцем и годом
    builder.button(text=f"📅 {month_names[month]} {year}", callback_data="noop")

    # Навигация по месяцам
    prev_month = month - 1
    prev_year = year
    if prev_month < 1:
        prev_month = 12
        prev_year -= 1

    next_month = month + 1
    next_year = year
    if next_month > 12:
        next_month = 1
        next_year += 1

    builder.button(text="◀️", callback_data=f"cal_nav:{prev_year}:{prev_month}")
    builder.button(text="▶️", callback_data=f"cal_nav:{next_year}:{next_month}")

    # Дни недели
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    for day in weekdays:
        builder.button(text=day, callback_data="noop")

    # Дни месяца
    cal = calendar.monthcalendar(year, month)
    today = date.today()

    for week in cal:
        for day in week:
            if day == 0:
                builder.button(text=" ", callback_data="noop")
            else:
                current_date = date(year, month, day)
                is_past = current_date < today
                is_day_off = current_date in exclude_dates

                if is_past:
                    # Прошедшие дни - серые, некликабельные
                    builder.button(text=f"·", callback_data="noop")
                elif is_day_off:
                    # Выходные мастера - помечены, некликабельные
                    builder.button(text=f"✕", callback_data="noop")
                elif current_date == today:
                    # Сегодня - выделено
                    builder.button(
                        text=f"•{day}•",
                        callback_data=f"{callback_prefix}:{current_date.strftime('%d.%m.%Y')}"
                    )
                else:
                    # Доступная дата
                    builder.button(
                        text=str(day),
                        callback_data=f"{callback_prefix}:{current_date.strftime('%d.%m.%Y')}"
                    )

    builder.button(text="◀️ Назад", callback_data=back_callback)

    # Настройка раскладки: 1 заголовок + 2 навигации + 7 дней недели + 7*n дней + 1 кнопка назад
    adjust = [1, 2, 7]
    for week in cal:
        adjust.append(7)
    adjust.append(1)

    builder.adjust(*adjust)
    return builder.as_markup()


def get_times_keyboard(available_times: List[str], occupied_times: List[str] = None) -> InlineKeyboardMarkup:
    """Клавиатура для выбора времени

    Args:
        available_times: список доступных слотов времени
        occupied_times: список занятых слотов времени (отображаются с пометкой)
    """
    builder = InlineKeyboardBuilder()
    occupied_times = occupied_times or []

    # Объединяем и сортируем все слоты
    all_times = []
    for time_str in available_times:
        all_times.append((time_str, True))
    for time_str in occupied_times:
        if time_str not in available_times:  # избегаем дубликатов
            all_times.append((time_str, False))

    # Сортируем по времени
    all_times.sort(key=lambda x: x[0])

    for time_str, is_available in all_times:
        if is_available:
            builder.button(
                text=time_str,
                callback_data=f"time:{time_str}"
            )
        else:
            # Занятый слот - показываем с пометкой, нельзя выбрать
            builder.button(
                text=f"🔒 {time_str}",
                callback_data=f"time_occupied:{time_str}"
            )

    builder.button(text="◀️ Назад", callback_data="back_to_dates")
    builder.adjust(3)  # По 3 времени в ряд
    return builder.as_markup()


def get_reschedule_times_keyboard(available_times: List[str], occupied_times: List[str] = None) -> InlineKeyboardMarkup:
    """Клавиатура для выбора времени при переносе записи

    Args:
        available_times: список доступных слотов времени
        occupied_times: список занятых слотов времени (отображаются с пометкой)
    """
    builder = InlineKeyboardBuilder()
    occupied_times = occupied_times or []

    # Объединяем и сортируем все слоты
    all_times = []
    for time_str in available_times:
        all_times.append((time_str, True))
    for time_str in occupied_times:
        if time_str not in available_times:
            all_times.append((time_str, False))

    all_times.sort(key=lambda x: x[0])

    for time_str, is_available in all_times:
        if is_available:
            builder.button(
                text=time_str,
                callback_data=f"reschedule_time:{time_str}"
            )
        else:
            builder.button(
                text=f"🔒 {time_str}",
                callback_data=f"reschedule_time_occupied:{time_str}"
            )

    builder.button(text="◀️ Назад к выбору даты", callback_data="reschedule_back_to_calendar")
    builder.button(text="❌ Отменить перенос", callback_data="cancel_reschedule", style="danger")
    builder.adjust(3)
    return builder.as_markup()


def get_phone_request_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для запроса номера телефона"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить номер телефона", request_contact=True)],
            [KeyboardButton(text="❌ Отмена")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    return keyboard


def get_repeat_toggle_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора: повторять запись каждую неделю или нет"""
    builder = InlineKeyboardBuilder()
    builder.button(text="Без повтора", callback_data="repeat_skip")
    builder.button(text="🔁 Повторять →", callback_data="repeat_enable")
    builder.adjust(1)
    return builder.as_markup()


def get_repeat_stepper_keyboard(weeks: int) -> InlineKeyboardMarkup:
    """Клавиатура степпера числа недель повтора (2-8)"""
    builder = InlineKeyboardBuilder()
    builder.button(text="−", callback_data="repeat_dec")
    builder.button(text=f"{weeks} недель", callback_data="repeat_noop")
    builder.button(text="+", callback_data="repeat_inc")
    builder.button(text="✅ Готово", callback_data="repeat_done")
    builder.button(text="❌ Отмена", callback_data="repeat_cancel")
    builder.adjust(3, 1, 1)
    return builder.as_markup()


def get_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для подтверждения записи"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data="confirm_booking", style="success")
    builder.button(text="📅 Изменить дату и время", callback_data="confirm_change_datetime")
    builder.button(text="❌ Отменить", callback_data="cancel_booking", style="danger")
    builder.adjust(1)
    return builder.as_markup()


def get_booking_actions_keyboard(booking_id: int) -> InlineKeyboardMarkup:
    """Клавиатура действий с записью"""
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отменить запись", callback_data=f"cancel_booking:{booking_id}", style="danger")
    builder.adjust(1)
    return builder.as_markup()


def get_business_info_keyboard(business_id: int, has_photos: bool = False, has_directions: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура для просмотра информации о бизнесе"""
    builder = InlineKeyboardBuilder()

    if has_photos:
        builder.button(text="📷 Фото заведения", callback_data=f"view_photos:{business_id}")
    if has_directions:
        builder.button(text="🗺 Как добраться", callback_data=f"view_directions:{business_id}")

    builder.adjust(2)
    return builder.as_markup()


def get_employees_for_service_keyboard(employees: List[Employee], service_id: int, service_name: str, business_type: str = None) -> InlineKeyboardMarkup:
    """Клавиатура для выбора мастера/помещения для конкретной услуги (пошаговый выбор)"""
    builder = InlineKeyboardBuilder()

    for employee in employees:
        spec_text = f" - {employee.specialization}" if employee.specialization else ""
        builder.button(
            text=f"{employee.name}{spec_text}",
            callback_data=f"emp_for_svc:{service_id}:{employee.id}"
        )

    builder.button(text="❌ Отмена", callback_data="cancel_per_service_selection", style="danger")
    builder.adjust(1)
    return builder.as_markup()


def get_prepayment_confirmation_keyboard(booking_id: int, require_receipt: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура для подтверждения отправки предоплаты клиентом

    Args:
        booking_id: ID бронирования
        require_receipt: если True, скрывает кнопку "Подтвердить без чека"
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="📸 Прикрепить чек и подтвердить", callback_data=f"prepay_attach_receipt:{booking_id}")
    if not require_receipt:
        builder.button(text="✅ Подтвердить без чека", callback_data=f"prepay_sent:{booking_id}", style="success")
    builder.button(text="❌ Отменить бронирование", callback_data=f"cancel_prepay_booking:{booking_id}", style="danger")
    builder.adjust(1)
    return builder.as_markup()


def get_venue_room_time_keyboard(
    employees: list,
    selected_employee_id: int,
    available_times: List[str],
    occupied_times: List[str] = None
) -> InlineKeyboardMarkup:
    """Комбинированная клавиатура: кнопки помещений + слоты времени для выбранного помещения

    Порядок отображения комнат:
    - Ряд 1: красная, серая, чёрная комнаты (до 3 в ряд)
    - Ряд 2: белая, бежевая комнаты
    - Ряды 3-4: танцевальные и прочие комнаты

    Args:
        employees: список помещений (объекты Employee)
        selected_employee_id: ID текущего выбранного помещения
        available_times: список доступных слотов времени для выбранного помещения
        occupied_times: список занятых слотов времени для выбранного помещения
    """
    builder = InlineKeyboardBuilder()
    occupied_times = occupied_times or []

    def _room_group(name: str) -> int:
        """Определяет группу комнаты для сортировки по названию"""
        n = name.lower()
        if 'красн' in n:
            return 0
        if 'сер' in n:
            return 1
        if 'чёрн' in n or 'черн' in n:
            return 2
        if 'бел' in n:
            return 3
        if 'бежев' in n:
            return 4
        return 5  # танцевальные и прочие

    # Сортируем помещения по группам
    sorted_emps = sorted(employees, key=lambda e: _room_group(e.name))

    # Разбиваем на группы для корректной раскладки
    row1_emps = [e for e in sorted_emps if _room_group(e.name) <= 2]   # красная, серая, чёрная
    row2_emps = [e for e in sorted_emps if _room_group(e.name) in (3, 4)]  # белая, бежевая
    other_emps = [e for e in sorted_emps if _room_group(e.name) >= 5]  # танцевальные и прочие

    ordered_emps = row1_emps + row2_emps + other_emps

    # Кнопки помещений
    for emp in ordered_emps:
        is_selected = emp.id == selected_employee_id
        check = "✅ " if is_selected else ""
        is_red = 'красн' in emp.name.lower()
        # Красная комната — кнопка красного цвета (Bot API 9.4, style="danger")
        btn_style = "danger" if is_red else None
        btn_kwargs = {"text": f"{check}{emp.name}", "callback_data": f"venue_room:{emp.id}"}
        if btn_style:
            btn_kwargs["style"] = btn_style
        builder.button(**btn_kwargs)

    # Слоты времени
    all_times = []
    for time_str in available_times:
        all_times.append((time_str, True))
    for time_str in occupied_times:
        if time_str not in available_times:
            all_times.append((time_str, False))

    all_times.sort(key=lambda x: x[0])

    for time_str, is_available in all_times:
        if is_available:
            builder.button(
                text=time_str,
                callback_data=f"time:{time_str}"
            )
        else:
            builder.button(
                text=f"🔒 {time_str}",
                callback_data=f"time_occupied:{time_str}"
            )

    builder.button(text="◀️ Назад", callback_data="back_to_dates")
    builder.button(text="🏠 Главное меню", callback_data="client_main_menu")

    # Раскладка рядов
    adjust = []

    # Ряд 1: красная, серая, чёрная (все в одном ряду, если есть)
    if row1_emps:
        adjust.append(len(row1_emps))

    # Ряд 2: белая, бежевая (все в одном ряду, если есть)
    if row2_emps:
        adjust.append(len(row2_emps))

    # Ряды 3+: танцевальные и прочие — каждый зал на отдельной строке
    for _ in other_emps:
        adjust.append(1)

    # Если все комнаты не попали в специальные группы, по 1 в ряд
    if not row1_emps and not row2_emps and other_emps:
        adjust = [1] * len(other_emps)

    # Время: по 3 в ряд
    time_count = len(all_times)
    remaining_count = time_count
    while remaining_count > 0:
        row_size = min(remaining_count, 3)
        adjust.append(row_size)
        remaining_count -= row_size

    # Навигационные кнопки
    adjust.append(2)

    builder.adjust(*adjust)
    return builder.as_markup()


def get_additional_services_keyboard(
    services: list,
    selected_ids: list = None,
    hourly_hours: dict = None,
    total_hours: int = 1
) -> InlineKeyboardMarkup:
    """Клавиатура для мультивыбора дополнительных услуг

    Args:
        services: список доп. услуг (объекты Service)
        selected_ids: список выбранных ID услуг
        hourly_hours: {service_id: кол-во часов} для почасовых услуг
        total_hours: макс. кол-во часов (длительность брони)
    """
    builder = InlineKeyboardBuilder()
    selected_ids = selected_ids or []
    hourly_hours = hourly_hours or {}

    adjust = []
    for service in services:
        is_hourly = getattr(service, 'is_hourly_price', False)
        is_selected = service.id in selected_ids

        if is_hourly and is_selected:
            hours = hourly_hours.get(service.id, total_hours)
            total_price = int(service.price * hours)
            builder.button(
                text="➖",
                callback_data=f"add_svc_minus:{service.id}"
            )
            builder.button(
                text=f"✅ {service.name} {hours}ч × {service.price}₽ = {total_price}₽",
                callback_data=f"add_svc:{service.id}"
            )
            builder.button(
                text="➕",
                callback_data=f"add_svc_plus:{service.id}"
            )
            adjust.append(3)
        else:
            price_suffix = "/ч" if is_hourly else ""
            price_text = f" - {service.price}₽{price_suffix}" if service.price else ""
            check = "✅ " if is_selected else ""
            builder.button(
                text=f"{check}{service.name}{price_text}",
                callback_data=f"add_svc:{service.id}"
            )
            adjust.append(1)

    # Кнопки навигации: "Пропустить" слева, "Готово" справа (если что-то выбрано)
    if selected_ids:
        builder.button(text="⏭ Пропустить", callback_data="additional_services_skip")
        builder.button(text="✅ Готово", callback_data="additional_services_done", style="success")
        adjust.append(2)  # Пропустить + Готово в одном ряду
    else:
        builder.button(text="⏭ Пропустить", callback_data="additional_services_skip")
        adjust.append(1)  # Только Пропустить

    builder.button(text="◀️ Назад", callback_data="additional_services_back")
    adjust.append(1)

    builder.adjust(*adjust)
    return builder.as_markup()


def get_booking_format_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора формата бронирования (группа / соло)"""
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Группа / дуэт", callback_data="booking_format:group")
    builder.button(text="👤 Самостоятельно / Педагог + ученик", callback_data="booking_format:solo")
    builder.button(text="◀️ Назад", callback_data="booking_format:back")
    builder.adjust(1)
    return builder.as_markup()


def get_notes_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для добавления комментария к записи"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Добавить комментарий", callback_data="booking_add_notes")
    builder.button(text="⏭ Пропустить", callback_data="booking_skip_notes")
    builder.button(text="◀️ Назад", callback_data="back_from_notes")
    builder.adjust(1)
    return builder.as_markup()


def get_employees_venue_keyboard(employees: List[Employee], business_type: str = None) -> InlineKeyboardMarkup:
    """Клавиатура для выбора помещения (для аренды помещений)

    Args:
        employees: список помещений (хранятся в таблице employees)
        business_type: тип бизнеса для определения терминологии
    """
    builder = InlineKeyboardBuilder()

    is_venue = is_venue_rental_type(business_type) if business_type else False

    # Для venue_rental не показываем "Любое помещение"
    if not is_venue:
        builder.button(text="👤 Любой мастер", callback_data="employee:any")

    for employee in employees:
        spec_text = f" - {employee.specialization}" if employee.specialization else ""
        builder.button(
            text=f"{employee.name}{spec_text}",
            callback_data=f"employee:{employee.id}"
        )

    builder.button(text="◀️ Назад", callback_data="back_to_services")
    builder.button(text="🏠 Главное меню", callback_data="client_main_menu")
    builder.adjust(1)
    return builder.as_markup()
