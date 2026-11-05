@echo off
chcp 65001 >nul
echo === Deploy Booking bot to server ===
echo.

if "%BOOKING_DEPLOY_SERVER%"=="" (
    echo BOOKING_DEPLOY_SERVER is not set. Example: root@your-server-ip
    exit /b 1
)

set SERVER=%BOOKING_DEPLOY_SERVER%
set REMOTE_PATH=%BOOKING_REMOTE_PATH%

if "%REMOTE_PATH%"=="" (
    set REMOTE_PATH=/opt/booking_bot
)

echo 1. Copying files to the server...
scp -r bot database webapp migrations requirements.txt run.sh .env.production %SERVER%:%REMOTE_PATH%/

echo 2. Preparing runtime config on the server...
ssh %SERVER% "cd %REMOTE_PATH% && cp .env.production .env"

echo 3. Installing dependencies...
ssh %SERVER% "cd %REMOTE_PATH% && source venv/bin/activate && pip install -r requirements.txt"

echo 4. Running database migrations...
ssh %SERVER% "cd %REMOTE_PATH% && source venv/bin/activate && python migrations/add_subscription_fields.py"

echo 5. Restarting the service...
ssh %SERVER% "systemctl restart booking-bot"

echo.
echo === Service status ===
ssh %SERVER% "systemctl status booking-bot --no-pager"

echo.
echo === Deploy finished ===
echo Check logs with: ssh %SERVER% journalctl -u booking-bot -f
pause
