@echo off
echo Starting Telegram Booking Bot...
echo.

REM Activate virtual environment
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
) else (
    echo Virtual environment not found!
    echo Please create it first: python -m venv venv
    pause
    exit
)

REM Run the bot
python -m bot.main

pause
