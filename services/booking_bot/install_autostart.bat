@echo off
chcp 65001 >nul
echo ========================================
echo    Настройка автозапуска бота
echo ========================================
echo.

set SCRIPT_PATH=%~dp0start_bot.bat
set TASK_NAME=TelegramBookingBot

echo Создание задачи в планировщике Windows...
echo.

REM Удаляем старую задачу если есть
schtasks /Delete /TN "%TASK_NAME%" /F 2>nul

REM Создаем новую задачу
schtasks /Create /TN "%TASK_NAME%" /TR "\"%SCRIPT_PATH%\"" /SC ONLOGON /RL HIGHEST /F

if %ERRORLEVEL% EQU 0 (
    echo.
    echo [УСПЕХ] Автозапуск настроен!
    echo.
    echo Бот будет автоматически запускаться при входе в систему.
    echo.
    echo Управление:
    echo - Запустить сейчас: start_bot.bat
    echo - Удалить автозапуск: schtasks /Delete /TN "%TASK_NAME%" /F
    echo - Посмотреть задачу: taskschd.msc
) else (
    echo.
    echo [ОШИБКА] Не удалось создать задачу!
    echo Запустите этот файл от имени администратора.
)

echo.
pause
