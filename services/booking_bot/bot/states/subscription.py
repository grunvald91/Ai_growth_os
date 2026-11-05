"""
FSM состояния для абонементов
"""
from aiogram.fsm.state import State, StatesGroup


class SubscriptionCreationStates(StatesGroup):
    """Состояния для создания абонемента (владелец)"""
    entering_name = State()           # Название абонемента
    entering_description = State()    # Описание
    selecting_services = State()      # Мультивыбор услуг
    entering_quantities = State()     # Количество каждой услуги
    entering_price = State()          # Цена абонемента
    entering_validity = State()       # Срок действия в днях
    entering_rules = State()          # Правила применения
    confirming = State()              # Подтверждение создания


class SubscriptionEditStates(StatesGroup):
    """Состояния для редактирования абонемента"""
    choosing_field = State()          # Выбор поля для редактирования
    editing_name = State()
    editing_description = State()
    editing_price = State()
    editing_validity = State()
    editing_rules = State()
    editing_services = State()        # Редактирование услуг


class SubscriptionPurchaseStates(StatesGroup):
    """Состояния для покупки абонемента (клиент)"""
    viewing_details = State()         # Просмотр деталей
    confirming_purchase = State()     # Подтверждение покупки
    waiting_payment = State()         # Ожидание оплаты (показаны реквизиты)
    uploading_receipt = State()       # Загрузка чека


class SubscriptionUseStates(StatesGroup):
    """Состояния для использования абонемента при бронировании"""
    choosing_subscription = State()           # Выбор абонемента
    choosing_service_from_subscription = State()  # Выбор услуги из абонемента


class SubscriptionPaymentConfirmStates(StatesGroup):
    """Состояния для подтверждения оплаты (владелец)"""
    viewing_payment = State()         # Просмотр заявки на оплату
    confirming_payment = State()      # Подтверждение получения оплаты
