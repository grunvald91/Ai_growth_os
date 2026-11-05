"""Общие состояния для бота"""
from aiogram.fsm.state import State, StatesGroup


class ReportProblemStates(StatesGroup):
    """Состояния для отправки сообщения о проблеме"""
    waiting_for_description = State()  # Ожидание описания проблемы
    waiting_for_screenshot = State()   # Ожидание скриншота (опционально)
