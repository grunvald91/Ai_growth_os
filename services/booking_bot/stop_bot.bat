@echo off
chcp 65001 >nul
echo Остановка бота...

REM Находим и останавливаем процесс Python с ботом
for /f "tokens=2" %%i in ('tasklist /FI "IMAGENAME eq python.exe" /FO LIST ^| find "PID:"') do (
    taskkill /PID %%i /F 2>nul
)

echo Бот остановлен
timeout /t 3
