@echo off
chcp 65001 >nul
echo ============================================
echo STOPPING ALL BOT INSTANCES
echo ============================================
echo.

echo Searching for python processes...
taskkill /F /IM python.exe 2>nul
taskkill /F /IM pythonw.exe 2>nul

echo.
echo Done! All Python processes stopped.
echo.
echo Now you can start the bot with:
echo   python run_bot_debug.py
echo.
pause
