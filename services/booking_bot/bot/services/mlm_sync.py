"""
Сервис синхронизации с базой MLM бота
Проверяет статус подписки пользователя в базе MLM бота
"""

from datetime_utils import utc_now
import logging
import secrets
import string
from typing import Optional, Tuple
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from bot.config import MLM_DATABASE_URL, MLM_BOT_USERNAME

logger = logging.getLogger(__name__)

# Создаем отдельный engine для подключения к MLM базе
mlm_engine = create_async_engine(
    MLM_DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)

mlm_session_maker = async_sessionmaker(
    mlm_engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def get_mlm_subscription_status(telegram_id: int) -> Tuple[Optional[str], Optional[str], Optional[int], Optional[datetime]]:
    """
    Получить статус подписки пользователя из базы MLM бота.

    Returns:
        Tuple[status, tariff, max_businesses, expires_at]:
        - status: 'active', 'trial', 'suspended', 'expired', None (не найден)
        - tariff: 'standard', 'business', 'network', None
        - max_businesses: 1, 3, 0 (безлимит), None
        - expires_at: дата окончания подписки, None
    """
    try:
        async with mlm_session_maker() as session:
            # Ищем пользователя и его активную подписку
            # Используем COALESCE для получения даты: trial_ends_at для trial, next_payment_date для active
            query = text("""
                SELECT
                    s.status,
                    s.tariff,
                    COALESCE(s.trial_ends_at, s.next_payment_date) as expires_at,
                    u.role
                FROM users u
                LEFT JOIN subscriptions s ON s.user_id = u.id AND s.status IN ('active', 'trial')
                WHERE u.telegram_id = :telegram_id
                ORDER BY s.created_at DESC NULLS LAST
                LIMIT 1
            """)

            result = await session.execute(query, {"telegram_id": telegram_id})
            row = result.fetchone()

            if not row:
                # Пользователь не найден в MLM базе
                return None, None, None, None

            status, tariff, expires_at, role = row

            # Если нет активной подписки
            if not status:
                # Проверяем, есть ли просроченная или приостановленная подписка
                suspended_query = text("""
                    SELECT status, tariff, COALESCE(trial_ends_at, next_payment_date) as expires_at
                    FROM subscriptions
                    WHERE user_id = (SELECT id FROM users WHERE telegram_id = :telegram_id)
                    ORDER BY created_at DESC
                    LIMIT 1
                """)
                suspended_result = await session.execute(suspended_query, {"telegram_id": telegram_id})
                suspended_row = suspended_result.fetchone()

                if suspended_row:
                    status, tariff, expires_at = suspended_row
                else:
                    return "none", None, None, None

            # Определяем лимит бизнесов по тарифу
            max_businesses = 1  # default: standard
            if tariff == "business":
                max_businesses = 3
            elif tariff == "network":
                max_businesses = 0  # безлимит

            return status, tariff, max_businesses, expires_at

    except Exception as e:
        logger.error(f"Ошибка при проверке подписки в MLM базе: {e}")
        # При ошибке возвращаем None - будет использоваться локальная проверка
        return None, None, None, None


async def check_subscription_for_business_creation(telegram_id: int) -> Tuple[bool, str]:
    """
    Проверить, может ли пользователь создать бизнес на основе данных из MLM базы.

    Returns:
        Tuple[can_create, reason_if_not]
    """
    status, tariff, max_businesses, expires_at = await get_mlm_subscription_status(telegram_id)

    # Если не удалось получить данные из MLM базы
    if status is None:
        return False, (
            f"Не найдена подписка. Оформите подписку через @{MLM_BOT_USERNAME}"
        )

    # Проверяем статус подписки
    if status == "none":
        return False, (
            f"У вас нет активной подписки. Оформите подписку через @{MLM_BOT_USERNAME}"
        )

    if status == "suspended":
        return False, (
            f"Ваша подписка приостановлена. Оплатите подписку через @{MLM_BOT_USERNAME}"
        )

    if status == "expired":
        return False, (
            f"Ваша подписка истекла. Продлите подписку через @{MLM_BOT_USERNAME}"
        )

    if status not in ("active", "trial"):
        return False, (
            f"Статус подписки: {status}. Проверьте подписку через @{MLM_BOT_USERNAME}"
        )

    # Подписка активна
    return True, ""


async def get_user_tariff_limits(telegram_id: int) -> Tuple[Optional[str], int]:
    """
    Получить тариф и лимит бизнесов пользователя.

    Returns:
        Tuple[tariff, max_businesses]:
        - tariff: 'standard', 'business', 'network', None
        - max_businesses: 1, 3, 0 (безлимит)
    """
    status, tariff, max_businesses, _ = await get_mlm_subscription_status(telegram_id)

    if status not in ("active", "trial"):
        return None, 0

    return tariff, max_businesses or 1


async def can_be_business_owner(telegram_id: int) -> bool:
    """
    Проверить, может ли пользователь быть владельцем бизнеса.

    Возвращает True, если выполняется ЛЮБОЕ из условий:
    1. У пользователя есть активная/trial подписка в MLM боте с ролью business/both
    2. Пользователь указан как owner_telegram_id в каком-либо бизнесе в MLM базе

    Это позволяет показывать кнопку "Режим владельца" пользователям,
    которые имеют активную подписку в MLM боте.
    """
    try:
        async with mlm_session_maker() as session:
            # Проверяем роль пользователя и наличие активной подписки
            query = text("""
                SELECT
                    u.role,
                    s.status
                FROM users u
                LEFT JOIN subscriptions s ON s.user_id = u.id AND s.status IN ('active', 'trial')
                WHERE u.telegram_id = :telegram_id
                ORDER BY s.created_at DESC NULLS LAST
                LIMIT 1
            """)

            result = await session.execute(query, {"telegram_id": telegram_id})
            row = result.fetchone()

            if row:
                role, subscription_status = row
                # Проверяем роль (business или both) И наличие активной подписки
                is_owner_role = role in ('business', 'both')
                has_active_subscription = subscription_status in ('active', 'trial')
                if is_owner_role and has_active_subscription:
                    return True

            # Также проверяем, есть ли у пользователя бизнесы с его telegram_id
            # (добавлены кем-то через "Добавить бизнес")
            business_query = text("""
                SELECT COUNT(*) FROM businesses
                WHERE owner_telegram_id = :telegram_id
            """)
            business_result = await session.execute(business_query, {"telegram_id": telegram_id})
            business_count = business_result.scalar()

            if business_count and business_count > 0:
                return True

            return False

    except Exception as e:
        logger.error(f"Ошибка при проверке can_be_business_owner в MLM базе: {e}")
        return False


def generate_referral_code() -> str:
    """Генерация уникального реферального кода"""
    chars = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(8))


async def create_trial_subscription(
    telegram_id: int,
    username: Optional[str],
    full_name: str,
    tariff: str = "standard",
    trial_days: int = 14
) -> Tuple[bool, str]:
    """
    Создать trial подписку для пользователя в базе MLM бота.

    Если пользователь не существует - создаёт его с ролью business.
    Если уже есть активная подписка - возвращает ошибку.

    Args:
        telegram_id: Telegram ID пользователя
        username: Username в Telegram
        full_name: Полное имя
        tariff: Тариф (standard, business)
        trial_days: Количество дней trial периода

    Returns:
        Tuple[success, message]
    """
    try:
        async with mlm_session_maker() as session:
            # Проверяем, есть ли уже активная подписка
            check_query = text("""
                SELECT s.id, s.status
                FROM subscriptions s
                JOIN users u ON s.user_id = u.id
                WHERE u.telegram_id = :telegram_id
                  AND s.status IN ('active', 'trial')
                LIMIT 1
            """)
            result = await session.execute(check_query, {"telegram_id": telegram_id})
            existing = result.fetchone()

            if existing:
                return False, "У вас уже есть активная подписка"

            # Проверяем, существует ли пользователь
            user_query = text("SELECT id FROM users WHERE telegram_id = :telegram_id")
            user_result = await session.execute(user_query, {"telegram_id": telegram_id})
            user_row = user_result.fetchone()

            if user_row:
                user_id = user_row[0]
                # Обновляем роль на business если нужно
                update_role = text("""
                    UPDATE users SET role = 'business'
                    WHERE id = :user_id AND role = 'partner'
                """)
                await session.execute(update_role, {"user_id": user_id})
            else:
                # Создаём нового пользователя
                referral_code = generate_referral_code()

                # Проверяем уникальность referral_code
                for _ in range(10):
                    check_code = text("SELECT id FROM users WHERE referral_code = :code")
                    code_result = await session.execute(check_code, {"code": referral_code})
                    if not code_result.fetchone():
                        break
                    referral_code = generate_referral_code()

                insert_user = text("""
                    INSERT INTO users (telegram_id, username, full_name, role, referral_code, created_at)
                    VALUES (:telegram_id, :username, :full_name, 'business', :referral_code, :created_at)
                    RETURNING id
                """)
                user_result = await session.execute(insert_user, {
                    "telegram_id": telegram_id,
                    "username": username,
                    "full_name": full_name,
                    "referral_code": referral_code,
                    "created_at": utc_now()
                })
                user_id = user_result.fetchone()[0]

            # Определяем цену по тарифу
            price = 2000 if tariff == "standard" else 4000

            # Создаём trial подписку
            trial_ends = utc_now() + timedelta(days=trial_days)

            insert_sub = text("""
                INSERT INTO subscriptions
                (user_id, tariff, price, status, started_at, trial_ends_at, next_payment_date, created_at, updated_at)
                VALUES
                (:user_id, :tariff, :price, 'trial', :started_at, :trial_ends_at, :next_payment_date, :created_at, :updated_at)
            """)
            await session.execute(insert_sub, {
                "user_id": user_id,
                "tariff": tariff,
                "price": price,
                "started_at": utc_now(),
                "trial_ends_at": trial_ends,
                "next_payment_date": trial_ends,
                "created_at": utc_now(),
                "updated_at": utc_now()
            })

            await session.commit()

            logger.info(f"Создана trial подписка для пользователя {telegram_id}")
            return True, f"Пробный период на {trial_days} дней активирован!"

    except Exception as e:
        logger.error(f"Ошибка при создании trial подписки: {e}")
        return False, f"Ошибка при создании подписки. Попробуйте через @{MLM_BOT_USERNAME}"
