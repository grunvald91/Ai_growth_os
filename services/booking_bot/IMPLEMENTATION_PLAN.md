# План реализации: Переименование админ→владелец + новая роль "Администратор"

## Текущее состояние (ПОСЛЕ РЕАЛИЗАЦИИ)
- **Супер-админ** (ID 437862772) - управление владельцами
- **Владелец** (ADMIN_IDS в .env) - полный контроль над бизнесом
- **Администратор** (Employee с role='administrator') - запись клиентов, отмена, статистика
- **Мастер** (Employee с role='master') - своё расписание, запись своих клиентов
- **Клиент** - запись на услуги

---

## ВЫПОЛНЕННЫЕ ЭТАПЫ

### ЭТАП 1: Переименование "админ" → "владелец" ✅ ГОТОВО

Изменены файлы:
- `bot/keyboards/client_kb.py` - "Режим владельца"
- `bot/keyboards/admin_kb.py` - "Управление владельцами"
- `bot/handlers/common.py` - тексты режима владельца
- `bot/handlers/admin_management.py` - управление владельцами

---

### ЭТАП 2: Поле role в модель Employee ✅ ГОТОВО

Изменены файлы:
- `database/models.py` - добавлено поле `role` (default='master')
- `database/crud.py` - добавлены функции:
  - `get_administrator_by_telegram_id()`
  - `get_master_by_telegram_id()`

---

### ЭТАП 3: keyboards/administrator_kb.py ✅ ГОТОВО

Создан файл `bot/keyboards/administrator_kb.py` с функциями:
- `get_administrator_menu_keyboard()` - главное меню администратора
- `get_admin_bookings_filter_keyboard()` - фильтры записей
- `get_master_selection_keyboard()` - выбор мастера
- `get_admin_booking_actions_keyboard()` - действия с записью
- `get_bookings_list_keyboard()` - список записей
- `get_admin_confirmation_keyboard()` - подтверждение

---

### ЭТАП 4: states/administrator.py ✅ ГОТОВО

Создан файл `bot/states/administrator.py` с классами:
- `AdminBookingStates` - запись клиента
- `AdminCancelStates` - отмена записи
- `AdminViewStates` - просмотр информации

---

### ЭТАП 5: handlers/administrator.py ✅ ГОТОВО

Создан файл `bot/handlers/administrator.py` с хендлерами:
- `show_all_bookings()` - просмотр записей с фильтрами
- `start_admin_booking()` - начало записи клиента
- Выбор мастера → услуги → даты → времени → данных клиента
- `start_cancel_booking()` - отмена записи
- `show_clients()` - список клиентов
- `show_statistics()` - статистика бизнеса

---

### ЭТАП 6: Интеграция в main.py и common.py ✅ ГОТОВО

Изменены файлы:
- `bot/main.py` - добавлен роутер administrator
- `bot/handlers/common.py` - определение роли администратора при /start

---

## ОСТАЛОСЬ СДЕЛАТЬ

### ЭТАП 7: Возможность добавлять администраторов владельцем ⬜

В `bot/handlers/admin.py` добавить:
- При создании сотрудника - выбор роли (мастер/администратор)
- Кнопка для изменения роли существующего сотрудника

---

## МИГРАЦИЯ БД (ОБЯЗАТЕЛЬНО!)

### Локально:
```bash
cd C:\Users\User\booking_bot
# SQLite сам добавит колонку при запуске (SQLAlchemy create_all)
python -c "import asyncio; from database import init_db; asyncio.run(init_db())"
```

### На сервере:
```sql
-- PostgreSQL/MySQL
ALTER TABLE employees ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'master';

-- Проверить
SELECT id, name, role FROM employees;
```

Или файл миграции: `migrations/add_employee_role.sql`

---

## ТЕСТИРОВАНИЕ

1. Перезапустить локального бота
2. Проверить что кнопки переименованы (владелец вместо админ)
3. Добавить администратора (пока вручную в БД):
   ```sql
   UPDATE employees SET role = 'administrator' WHERE telegram_id = <ID>;
   ```
4. Проверить что администратор видит свое меню при /start
5. Протестировать функции записи, отмены, статистики

---

## СТРУКТУРА ФАЙЛОВ

```
bot/
├── handlers/
│   ├── administrator.py  ← НОВЫЙ
│   ├── admin.py
│   ├── admin_management.py
│   ├── client.py
│   ├── common.py  ← ИЗМЕНЕН
│   └── master.py
├── keyboards/
│   ├── administrator_kb.py  ← НОВЫЙ
│   ├── admin_kb.py  ← ИЗМЕНЕН
│   ├── client_kb.py  ← ИЗМЕНЕН
│   └── master_kb.py
├── states/
│   ├── administrator.py  ← НОВЫЙ
│   ├── admin.py
│   ├── booking.py
│   └── master.py
├── main.py  ← ИЗМЕНЕН
└── config.py

database/
├── models.py  ← ИЗМЕНЕН (добавлено поле role)
├── crud.py  ← ИЗМЕНЕН (добавлены функции)
└── database.py

migrations/
└── add_employee_role.sql  ← НОВЫЙ
```
