"""
FSM состояния для панели мастеров
"""
from aiogram.fsm.state import State, StatesGroup


class ManualBookingStates(StatesGroup):
    """Состояния для ручной записи клиента мастером"""
    choosing_service = State()
    entering_client_name = State()
    entering_client_phone = State()
    choosing_date = State()
    choosing_time = State()
    choosing_repeat = State()
    confirming = State()
