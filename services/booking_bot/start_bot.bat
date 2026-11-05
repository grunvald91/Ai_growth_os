@echo off
chcp 65001 >nul
title Telegram Booking Bot
echo ========================================
echo    Telegram Booking Bot
echo ========================================
echo.

cd /d "%~dp0"

REM Проверка виртуального окружения
if not exist "venv\Scripts\python.exe" (
    echo [ОШИБКА] Виртуальное окружение не найдено!
    echo Создайте его командой: python -m venv venv
    pause
    exit /b 1
)

REM Проверка .env файла
if not exist ".env" (
    echo [ОШИБКА] Файл .env не найден!
    echo Скопируйте .env.example в .env и настройте
    pause
    exit /b 1
)

echo [INFO] Запуск бота...
echo [INFO] Для остановки нажмите Ctrl+C
echo.

REM Запуск бота с автоперезапуском при ошибке
:restart
venv\Scripts\python.exe -m bot.main
if errorlevel 1 (
    echo.
    echo [ОШИБКА] Бот остановился с ошибкой!
    echo Перезапуск через 10 секунд...
    timeout /t 10 /nobreak
    goto restart
)

echo.
echo [INFO] Бот остановлен
pause
