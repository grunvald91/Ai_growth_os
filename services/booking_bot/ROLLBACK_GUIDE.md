# 🔄 Руководство по откату версий

## 📌 Основы

Все принципиальные изменения в коде сохраняются в git с тегами версий для возможности откатов.

### Текущая версия в production:
- **Тег:** `v1.0.0-coowner-auth-fix`
- **Дата:** 20.02.2026
- **Описание:** Исправление веб-аутентификации для совладельцев (co-owners)
- **Изменения:**
  - Фиксирована функция `auth_token_handler` в `webapp/server.py` - теперь проверяет таблицу `BusinessOwner`
  - Фиксирована функция `api_auth_login` в `webapp/server.py` - теперь проверяет таблицу `BusinessOwner`
  - Улучшены уведомления при добавлении совладельца - теперь отправляются web-credentials в `bot/handlers/admin.py`
  - Обновлен скрипт архивирования - добавлена папка `webapp/`

---

## 🔙 Откат на локальной машине

### Откат в git на предыдущую версию:

```bash
# Просмотреть все теги (версии)
git tag -l

# Откатиться к конкретному тегу
git reset --hard v1.0.0-coowner-auth-fix

# Или откатиться на N коммитов назад
git reset --hard HEAD~3  # Откат на 3 коммита

# Проверить текущее состояние
git log --oneline -5
```

### После отката обновить архив:

```bash
cd c:\Users\User\booking_bot
python create_deploy_archive.py
```

---

## 🚀 Развертывание откатанной версии на сервер

### Если уже откатились локально:

```bash
# 1. Пересоздать архив
python create_deploy_archive.py

# 2. Развернуть на сервер
python deploy_to_server.py
```

### Или развернуть напрямую на сервере (не откатываясь локально):

```bash
# Подключиться к серверу по SSH
ssh root@your-server-ip

# Остановить бота
systemctl stop booking-bot

# Откатить git репозиторий на сервере
cd /opt/booking_bot
git reset --hard v1.0.0-coowner-auth-fix

# Запустить бота заново
systemctl start booking-bot

# Проверить статус
systemctl status booking-bot
journalctl -u booking-bot -f
```

---

## 📋 Все доступные версии

### v1.0.0-coowner-auth-fix
- **Статус:** ✅ В production
- **Дата:** 20.02.2026
- **Файлы измененные:** 
  - `webapp/server.py` (функции `auth_token_handler` и `api_auth_login`)
  - `bot/handlers/admin.py` (улучшены уведомления при добавлении совладельца)
  - `create_deploy_archive.py` (добавлена папка webapp)

---

## ⚙️ Быстрые команды

### Просмотр коммитов:
```bash
git log --oneline
```

### Просмотр различий между версиями:
```bash
git diff v1.0.0-coowner-auth-fix..HEAD
```

### Создать новый тег перед исправлением:
```bash
git tag -a v1.0.1-[description] -m "Description of changes"
git push origin v1.0.1-[description]
```

### Просмотр измененных файлов в конкретном коммите:
```bash
git show --name-status 12345abc
```

---

## 🆘 Если что-то пошло не так

### 1. Боту сложно восстановиться:

```bash
# На сервере - полная переустановка
cd /opt/booking_bot
git reset --hard origin/main  # Если есть удаленный репозиторий
# Или откатитесь на последнюю известную хорошую версию
git reset --hard v1.0.0-coowner-auth-fix

systemctl restart booking-bot
```

### 2. Проверить логи:

```bash
# Логи бота на сервере
journalctl -u booking-bot -n 100 --no-pager
tail -f /var/log/booking-bot.log

# Логи deploy скрипта
```

### 3. Если архив развернулся неправильно:

```bash
# На сервере
cd /opt/booking_bot
rm -rf bot database webapp  # Удаляем старые файлы
git checkout bot database webapp  # Восстанавливаем из git
systemctl restart booking-bot
```

---

## 💾 Резервные копии

На сервере автоматически создается резервная копия при развертывании:
```bash
/opt/booking_bot/bot.backup  # Резервная копия старого кода
```

Для восстановления:
```bash
cd /opt/booking_bot
mv bot bot.new
mv bot.backup bot
systemctl restart booking-bot
```

---

## 📝 Примечания

- **Не забывайте** делать тег перед каждым большим изменением
- **Тестируйте** изменения локально перед развертыванием
- **Сохраняйте** хорошие рабочие версии в git
- **Документируйте** что изменилось в каждой версии

---

**Последнее обновление:** 20.02.2026  
**Автор:** GitHub Copilot
