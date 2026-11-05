"""
FSM состояния для админ-панели
"""
from aiogram.fsm.state import State, StatesGroup


class BusinessCreationStates(StatesGroup):
    """Состояния для создания бизнеса"""
    entering_name = State()
    entering_type = State()
    entering_description = State()
    entering_address = State()
    entering_phone = State()
    entering_working_hours = State()
    entering_break_time = State()
    entering_booking_interval = State()


class ServiceCreationStates(StatesGroup):
    """Состояния для создания услуги"""
    choosing_business = State()
    entering_name = State()
    entering_description = State()
    entering_duration = State()
    entering_price = State()
    choosing_hourly = State()  # Цена за час? (для доп. услуг venue_rental)
    choosing_pricing_type = State()   # Фиксированная / по кол-ву человек
    entering_group_tier = State()     # Ввод тарифов мин-макс цена (per_person)


class EmployeeCreationStates(StatesGroup):
    """Состояния для создания сотрудника"""
    choosing_business = State()
    entering_name = State()
    entering_specialization = State()
    entering_telegram_id = State()
    entering_booking_interval = State()


class BusinessEditStates(StatesGroup):
    """Состояния для редактирования бизнеса"""
    choosing_field = State()
    editing_name = State()
    editing_description = State()
    editing_address = State()
    editing_phone = State()
    editing_working_hours = State()
    editing_break = State()
    editing_photos = State()
    editing_video = State()
    editing_directions = State()


class ServiceEditStates(StatesGroup):
    """Состояния для редактирования услуги"""
    editing_name = State()
    editing_description = State()
    editing_duration = State()
    editing_price = State()
    entering_group_tier = State()   # Ввод тарифов мин-макс цена (per_person) при редактировании


class EmployeeEditStates(StatesGroup):
    """Состояния для редактирования сотрудника"""
    editing_name = State()
    editing_specialization = State()
    editing_telegram_id = State()
    editing_interval = State()
    entering_master_specialization = State()  # Для ввода специализации при назначении мастером
    editing_capacity = State()  # Вместимость зала
    editing_area = State()  # Площадь зала
    editing_description = State()  # Описание зала
    editing_min_booking = State()  # Мин. время бронирования
    adding_photo = State()  # Добавление фото зала
    adding_video = State()  # Добавление видео зала


class AddAdministratorStates(StatesGroup):
    """Состояния для назначения администратора бизнеса"""
    entering_name = State()         # Ввод имени
    entering_telegram_id = State()  # Ввод Telegram ID


class EmployeeServicesStates(StatesGroup):
    """Состояния для управления услугами сотрудника"""
    selecting_services = State()    # Выбор услуг для мастера


class PrepaymentSettingsStates(StatesGroup):
    """Состояния для настройки предоплаты"""
    entering_percent = State()              # Ввод процента предоплаты
    entering_phone = State()               # Ввод номера для оплаты
    entering_details = State()             # Ввод реквизитов (карта, СБП и т.д.)
    entering_cancellation_photo = State()  # Загрузка фото условий отмены
    entering_cancellation_text = State()   # Ввод текста условий отмены


class BusinessOwnerStates(StatesGroup):
    """Состояния для управления совладельцами бизнеса"""
    entering_telegram_id = State()  # Ввод Telegram ID или username совладельца


class VenueTimePricingStates(StatesGroup):
    """Состояния для добавления тарифа по времени суток"""
    entering_time_from = State()
    entering_time_to = State()
    entering_price = State()
    choosing_day_type = State()  # Будни/выходные/все дни
