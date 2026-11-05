"""
Тестирование основной функциональности бота
"""
import asyncio
from database.database import async_session_maker
from database.crud import (
    get_all_active_businesses,
    get_or_create_user
)


async def test_database():
    """Проверка подключения к БД"""
    print("=" * 50)
    print("ТЕСТ 1: Подключение к базе данных")
    print("=" * 50)

    try:
        async with async_session_maker() as session:
            # Проверяем подключение
            businesses = await get_all_active_businesses(session)
            print(f"[OK] Подключение к БД успешно!")
            print(f"[INFO] Найдено активных бизнесов: {len(businesses)}")

            if businesses:
                for b in businesses[:3]:  # Показываем первые 3
                    print(f"  - {b.name} (ID: {b.id})")

            # Проверяем создание/получение пользователя
            user = await get_or_create_user(
                session,
                telegram_id=123456789,
                username="test_user",
                full_name="Test User"
            )
            print(f"\n[OK] Тест создания пользователя успешен!")
            print(f"[USER] Пользователь: {user.full_name} (ID: {user.id})")

    except Exception as e:
        print(f"[ERROR] Ошибка при работе с БД: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True


async def test_models():
    """Проверка моделей"""
    print("\n" + "=" * 50)
    print("ТЕСТ 2: Проверка моделей данных")
    print("=" * 50)

    try:
        from database.models import User, Business, Service, Employee, Booking
        print("[OK] Все модели успешно импортированы:")
        print("  - User")
        print("  - Business")
        print("  - Service")
        print("  - Employee")
        print("  - Booking")
        return True
    except Exception as e:
        print(f"[ERROR] Ошибка импорта моделей: {e}")
        return False


async def test_handlers():
    """Проверка handlers"""
    print("\n" + "=" * 50)
    print("ТЕСТ 3: Проверка handlers")
    print("=" * 50)

    try:
        from bot.handlers import client, admin, master, common, admin_management
        print("[OK] Все handlers успешно импортированы:")
        print("  - client")
        print("  - admin")
        print("  - master")
        print("  - common")
        print("  - admin_management")
        return True
    except Exception as e:
        print(f"[ERROR] Ошибка импорта handlers: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_services():
    """Проверка сервисов"""
    print("\n" + "=" * 50)
    print("ТЕСТ 4: Проверка сервисов")
    print("=" * 50)

    try:
        from bot.services import scheduler, notifications
        print("[OK] Все сервисы успешно импортированы:")
        print("  - scheduler")
        print("  - notifications")
        return True
    except Exception as e:
        print(f"[ERROR] Ошибка импорта сервисов: {e}")
        return False


async def main():
    """Запуск всех тестов"""
    print("\n>>> ЗАПУСК ТЕСТИРОВАНИЯ БОТА\n")

    results = []

    # Тест 1: БД
    results.append(await test_database())

    # Тест 2: Модели
    results.append(await test_models())

    # Тест 3: Handlers
    results.append(await test_handlers())

    # Тест 4: Сервисы
    results.append(await test_services())

    # Итоги
    print("\n" + "=" * 50)
    print("ИТОГИ ТЕСТИРОВАНИЯ")
    print("=" * 50)
    passed = sum(results)
    total = len(results)
    print(f"Пройдено тестов: {passed}/{total}")

    if passed == total:
        print("[SUCCESS] ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
    else:
        print("[WARNING] Некоторые тесты не прошли")

    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
