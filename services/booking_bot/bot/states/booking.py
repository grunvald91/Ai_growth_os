"""
FSM состояния для процесса записи клиента
"""
from aiogram.fsm.state import State, StatesGroup


class BookingStates(StatesGroup):
    """Состояния для процесса записи на услугу"""
    choosing_business = State()      # Выбор бизнеса
    choosing_service = State()       # Выбор услуги
    choosing_employee = State()      # Выбор мастера/специалиста
    choosing_employee_per_service = State()  # Пошаговый выбор мастера для каждой услуги
    choosing_date = State()          # Выбор даты
    choosing_time = State()          # Выбор времени
    choosing_venue_room_time = State()  # Комбинированный выбор помещения и времени (аренда)
    choosing_additional_services = State()  # Выбор дополнительных услуг (аренда)
    choosing_booking_format = State()  # Выбор формата: группа / соло
    entering_notes = State()           # Ввод комментария к записи
    entering_phone = State()         # Ввод номера телефона
    choosing_repeat = State()        # Выбор повтора записи (2-8 недель)
    confirming = State()             # Подтверждение записи
    waiting_prepayment = State()     # Ожидание предоплаты от клиента
    uploading_receipt = State()      # Загрузка чека об оплате

    # Состояния для переноса записи
    reschedule_choosing_date = State()   # Выбор новой даты для переноса
    reschedule_choosing_time = State()   # Выбор нового времени для переноса
