"""
FSM состояния для панели администратора
"""
from aiogram.fsm.state import State, StatesGroup


class AdminBookingStates(StatesGroup):
    """Состояния для записи клиента администратором"""
    choosing_master = State()       # Выбор мастера
    choosing_service = State()      # Выбор услуги
    choosing_date = State()         # Выбор даты
    choosing_time = State()         # Выбор времени
    entering_client_name = State()  # Ввод имени клиента
    entering_client_phone = State() # Ввод телефона клиента
    choosing_repeat = State()       # Выбор повтора записи (2-8 недель)
    confirming = State()            # Подтверждение записи


class AdminCancelStates(StatesGroup):
    """Состояния для отмены записи"""
    choosing_booking = State()      # Выбор записи для отмены
    confirming = State()            # Подтверждение отмены


class AdminViewStates(StatesGroup):
    """Состояния для просмотра информации"""
    viewing_bookings = State()      # Просмотр записей
    viewing_clients = State()       # Просмотр клиентов
    viewing_statistics = State()    # Просмотр статистики
