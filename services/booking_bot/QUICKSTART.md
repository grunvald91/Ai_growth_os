# Быстрый старт

## Шаг 1: Установка PostgreSQL

### Windows:
1. Скачайте PostgreSQL с [официального сайта](https://www.postgresql.org/download/windows/)
2. Установите PostgreSQL (запомните пароль для пользователя postgres)
3. Откройте pgAdmin или командную строку psql
4. Выполните команды:

```sql
CREATE DATABASE booking_bot;
```

### Linux (Ubuntu/Debian):
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo -u postgres psql
```

Затем в psql:
```sql
CREATE DATABASE booking_bot;
CREATE USER bot_user WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE booking_bot TO bot_user;
\q
```

## Шаг 2: Создание бота в Telegram

1. Откройте Telegram и найдите [@BotFather](https://t.me/BotFather)
2. Отправьте команду `/newbot`
3. Введите имя бота (например: "Моя Запись Онлайн")
4. Введите username бота (должен заканчиваться на `bot`, например: `my_booking_bot`)
5. **Скопируйте токен** который вам выдаст BotFather (выглядит как `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

## Шаг 3: Узнайте свой Telegram ID

1. Найдите в Telegram бота [@userinfobot](https://t.me/userinfobot)
2. Нажмите `/start`
3. Скопируйте ваш ID (например: `123456789`)

## Шаг 4: Настройка проекта

1. Откройте папку `booking_bot`
2. Создайте файл `.env` (скопируйте из `.env.example`)
3. Заполните `.env`:

```env
BOT_TOKEN=ваш_токен_от_BotFather
DB_HOST=localhost
DB_PORT=5432
DB_NAME=booking_bot
DB_USER=postgres
DB_PASSWORD=ваш_пароль_от_postgres
TIMEZONE=Europe/Moscow
ADMIN_IDS=ваш_telegram_id
```

## Шаг 5: Установка зависимостей

```bash
# Создайте виртуальное окружение
python -m venv venv

# Активируйте (Windows)
venv\Scripts\activate

# Активируйте (Linux/Mac)
source venv/bin/activate

# Установите зависимости
pip install -r requirements.txt
```

## Шаг 6: Запуск бота

```bash
python -m bot.main
```

Если все настроено правильно, вы увидите:
```
INFO - Запуск бота...
INFO - Инициализация базы данных...
INFO - База данных готова
INFO - Планировщик уведомлений запущен
INFO - Бот запущен и готов к работе!
```

## Шаг 7: Первое использование

1. Найдите своего бота в Telegram по username
2. Нажмите `/start`
3. Создайте свой первый бизнес: "➕ Добавить бизнес"
4. Добавьте услуги и сотрудников
5. Готово! Теперь клиенты могут записываться

## Частые проблемы

### Ошибка подключения к БД
- Проверьте, что PostgreSQL запущен
- Проверьте правильность пароля в `.env`
- Убедитесь, что база данных создана

### Бот не отвечает
- Проверьте правильность токена в `.env`
- Убедитесь, что бот запущен (смотрите консоль)

### ModuleNotFoundError
- Убедитесь, что виртуальное окружение активировано
- Установите зависимости: `pip install -r requirements.txt`

## Следующие шаги

1. Изучите файл `README.md` для подробной документации
2. Настройте рабочее время и цены
3. Пригласите клиентов использовать бота
4. Просматривайте записи через меню "📊 Записи"

Удачи! 🚀
