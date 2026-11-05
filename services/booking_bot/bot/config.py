"""
Конфигурация бота
Загружает настройки из .env файла
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле!")

# Database
DB_TYPE = os.getenv("DB_TYPE", "sqlite")  # sqlite или postgresql

if DB_TYPE == "sqlite":
    # Используем SQLite (не требует установки, для тестирования)
    DATABASE_URL = "sqlite+aiosqlite:///booking_bot.db"
else:
    # Используем PostgreSQL (для production)
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_NAME = os.getenv("DB_NAME", "booking_bot")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Timezone
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")

# Администраторы
ADMIN_IDS = [int(id_) for id_ in os.getenv("ADMIN_IDS", "").split(",") if id_.strip()]

# WebApp
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "8080"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "")  # Public URL для WebApp (например https://yourdomain.com)

# MLM Bot Database (для проверки подписок)
MLM_DB_HOST = os.getenv("MLM_DB_HOST", "localhost")
MLM_DB_PORT = os.getenv("MLM_DB_PORT", "5432")
MLM_DB_NAME = os.getenv("MLM_DB_NAME", "mlm_bot")
MLM_DB_USER = os.getenv("MLM_DB_USER", "postgres")
MLM_DB_PASSWORD = os.getenv("MLM_DB_PASSWORD")
MLM_DATABASE_URL = f"postgresql+asyncpg://{MLM_DB_USER}:{MLM_DB_PASSWORD}@{MLM_DB_HOST}:{MLM_DB_PORT}/{MLM_DB_NAME}"

# MLM Bot Username (для ссылок)
MLM_BOT_USERNAME = os.getenv("MLM_BOT_USERNAME", "MLMPartnerBot")

# Booking Bot Username (для QR-кодов)
BOT_USERNAME = os.getenv("BOT_USERNAME", "Sochiservice_bot")

# Compatibility with bot.services.ai_admin, which imports this setting.
OPENROUTER_AI_ADMIN_KEY = os.getenv("OPENROUTER_AI_ADMIN_KEY", "")
