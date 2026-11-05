# 🚀 Развертывание и автозапуск бота

## 📋 Содержание
1. [Локальный запуск (Windows)](#локальный-запуск-windows)
2. [Развертывание на VPS](#развертывание-на-vps)
3. [Docker (опционально)](#docker)

---

## 🖥 Локальный запуск (Windows)

### Вариант 1: Автозапуск через Task Scheduler (Рекомендуется)

**Шаг 1: Установка автозапуска**

Запустите файл **от имени администратора**:
```
install_autostart.bat
```

Это создаст задачу в планировщике Windows, которая будет запускать бота при входе в систему.

**Шаг 2: Проверка**

1. Откройте "Планировщик заданий" (Win+R → `taskschd.msc`)
2. Найдите задачу "TelegramBookingBot"
3. Можете запустить её вручную правой кнопкой → "Выполнить"

**Управление:**
- **Запустить бота:** `start_bot.bat`
- **Остановить бота:** `stop_bot.bat` или Ctrl+C в окне бота
- **Удалить автозапуск:** `schtasks /Delete /TN "TelegramBookingBot" /F`

### Вариант 2: Windows Service через NSSM

**Более надежный способ - бот работает как служба Windows**

1. **Скачайте NSSM:**
   - https://nssm.cc/download
   - Распакуйте в папку проекта

2. **Установите службу (от администратора):**
```cmd
nssm install BookingBot "C:\Users\User\booking_bot\venv\Scripts\python.exe" "-m bot.main"
nssm set BookingBot AppDirectory "C:\Users\User\booking_bot"
nssm set BookingBot DisplayName "Telegram Booking Bot"
nssm set BookingBot Description "Бот для онлайн-записи клиентов"
nssm set BookingBot Start SERVICE_AUTO_START
nssm start BookingBot
```

3. **Управление службой:**
```cmd
# Запустить
net start BookingBot

# Остановить
net stop BookingBot

# Удалить службу
nssm remove BookingBot confirm
```

---

## ☁️ Развертывание на VPS

### Рекомендуемые провайдеры:
- **Timeweb** (Россия) - от 190₽/мес
- **Регру** - от 149₽/мес
- **Selectel** - от 200₽/мес
- **VDSina** - от 150₽/мес

### Установка на Ubuntu/Debian:

**1. Подключитесь к серверу по SSH:**
```bash
ssh user@your-server-ip
```

**2. Установите зависимости:**
```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv postgresql git
```

**3. Настройте PostgreSQL:**
```bash
sudo -u postgres psql
CREATE DATABASE booking_bot;
CREATE USER bot_user WITH PASSWORD 'secure_password';
GRANT ALL PRIVILEGES ON DATABASE booking_bot TO bot_user;
\q
```

**4. Клонируйте проект:**
```bash
cd /opt
git clone <your-repo-url> booking_bot
cd booking_bot
```

**5. Настройте окружение:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**6. Создайте .env файл:**
```bash
nano .env
# Вставьте настройки
```

**7. Создайте systemd service:**
```bash
sudo nano /etc/systemd/system/booking-bot.service
```

Вставьте:
```ini
[Unit]
Description=Telegram Booking Bot
After=network.target postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/booking_bot
Environment="PATH=/opt/booking_bot/venv/bin"
ExecStart=/opt/booking_bot/venv/bin/python -m bot.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**8. Запустите службу:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable booking-bot
sudo systemctl start booking-bot

# Проверка статуса
sudo systemctl status booking-bot

# Просмотр логов
sudo journalctl -u booking-bot -f
```

**Управление на сервере:**
```bash
sudo systemctl start booking-bot    # Запустить
sudo systemctl stop booking-bot     # Остановить
sudo systemctl restart booking-bot  # Перезапустить
sudo systemctl status booking-bot   # Статус
```

---

## 🐳 Docker (Опционально)

**Создайте Dockerfile:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y postgresql-client

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-m", "bot.main"]
```

**docker-compose.yml:**
```yaml
version: '3.8'

services:
  bot:
    build: .
    env_file: .env
    depends_on:
      - db
    restart: unless-stopped

  db:
    image: postgres:15
    environment:
      POSTGRES_DB: booking_bot
      POSTGRES_USER: bot_user
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

volumes:
  postgres_data:
```

**Запуск:**
```bash
docker-compose up -d
```

---

## 📊 Мониторинг

### Проверка работы бота:

**Windows:**
```cmd
tasklist | find "python"
```

**Linux:**
```bash
ps aux | grep python
sudo systemctl status booking-bot
```

### Логи:

**Windows:** Смотрите в окне `start_bot.bat`

**Linux:**
```bash
sudo journalctl -u booking-bot -f --lines=100
```

---

## 🔧 Обновление бота

**Windows:**
1. Остановите бота (`stop_bot.bat`)
2. Внесите изменения в код
3. Запустите снова (`start_bot.bat`)

**Linux:**
```bash
cd /opt/booking_bot
git pull
sudo systemctl restart booking-bot
```

---

## ⚠️ Важные моменты

1. **Бэкапы базы данных:**
   ```bash
   # PostgreSQL
   pg_dump -U bot_user booking_bot > backup.sql
   ```

2. **Безопасность:**
   - Используйте сильные пароли
   - Настройте firewall
   - Регулярно обновляйте систему

3. **Мониторинг:**
   - Проверяйте логи регулярно
   - Настройте уведомления об ошибках

---

## 📞 Поддержка

При проблемах проверьте:
- Логи бота
- Статус PostgreSQL
- Правильность .env файла
- Доступность сети
