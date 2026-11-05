# BookingBot Web App — React + Tailwind Design

Дата: 2026-03-15

## Цель

Два контура на одном домене (`artur.grunvald1991.fvds.ru`, позже — свой домен):
- **Клиент** (по умолчанию) — просмотр бизнесов, запись, AI-чат, мои записи
- **Владелец/Персонал** (по авторизации) — расписание, управление, аналитика

Дизайн: Apple-style, минималистичный, мобиль-first.

## Архитектура

```
Nginx (443/80)
  ├── /              → React SPA (static files)
  ├── /api/*         → FastAPI (новый, порт 8000)
  └── /api/legacy/*  → aiohttp (существующий, порт 8080)
```

### Почему FastAPI отдельно
- Существующий aiohttp сервер завязан на Telegram WebApp auth
- FastAPI даёт standalone auth (JWT), OpenAPI docs, современный стек
- aiohttp остаётся для бота и Telegram WebApp — ничего не ломаем
- FastAPI работает с той же БД (asyncpg + SQLAlchemy)

## Стек

- **Frontend**: React 18 + Vite + Tailwind CSS + React Router
- **Backend**: FastAPI + SQLAlchemy async (та же БД что у бота)
- **Auth**: JWT tokens (phone + SMS/код, Telegram Login Widget)
- **Deploy**: Nginx serves React static + proxies API

## Клиентский контур (/)

### Страницы
1. **/** — Главная: список бизнесов (карточки Apple-style)
2. **/business/:id** — Бизнес: описание, услуги, мастера, фото
3. **/book/:businessId** — Запись: выбор услуги → дата → время → подтверждение
4. **/ai** — AI-чат (опционально, кнопка внизу)
5. **/my** — Мои записи (требует авторизации)
6. **/login** — Вход: Telegram Login Widget + телефон

### UI компоненты
- `BusinessCard` — карточка бизнеса (фото, название, рейтинг)
- `ServiceList` — список услуг с ценами
- `DatePicker` — календарь в стиле iOS
- `TimeSlots` — сетка доступного времени
- `BookingConfirm` — итоговая карточка перед записью
- `BookingCard` — карточка записи в «Мои записи»
- `AIChatBubble` — плавающая кнопка AI-помощника

## Владелец/Персонал (/admin)

### Страницы
1. **/admin** — Дашборд: записи на сегодня, быстрая статистика
2. **/admin/schedule** — Расписание: день/неделя, drag-n-drop
3. **/admin/bookings** — Все записи: фильтры, поиск
4. **/admin/services** — Управление услугами
5. **/admin/employees** — Управление сотрудниками
6. **/admin/settings** — Настройки бизнеса
7. **/admin/analytics** — Аналитика: выручка, клиенты, загрузка

### UI компоненты
- `DayTimeline` — вертикальная шкала дня с записями
- `BookingModal` — создание/редактирование записи
- `StatCard` — карточка статистики
- `DataTable` — таблица с фильтрами
- `Sidebar` — боковое меню навигации

## Apple-Style Design System

### Принципы
- Много белого пространства
- Скруглённые углы (rounded-2xl)
- Тени мягкие (shadow-sm, shadow-md)
- Шрифт: SF Pro Display / Inter
- Анимации: ease-out, 200-300ms
- Цвета: чёрный текст, серые подложки, один акцентный цвет

### Цветовая палитра
```
--bg: #FFFFFF
--bg-secondary: #F5F5F7
--text-primary: #1D1D1F
--text-secondary: #86868B
--accent: #0071E3 (Apple blue)
--success: #30D158
--warning: #FF9F0A
--danger: #FF3B30
--border: #E5E5EA
```

### Типографика
```
Заголовок H1: 34px, weight 700, tracking -0.02em
Заголовок H2: 28px, weight 600
Заголовок H3: 22px, weight 600
Body: 17px, weight 400, line-height 1.5
Caption: 15px, weight 400, color: text-secondary
Small: 13px
```

## API Endpoints (FastAPI)

### Auth
- `POST /api/auth/telegram` — вход через Telegram Login Widget
- `POST /api/auth/phone` — запрос кода на телефон
- `POST /api/auth/verify` — проверка кода
- `POST /api/auth/admin-login` — вход владельца (phone + PIN)
- `GET /api/auth/me` — текущий пользователь

### Client
- `GET /api/businesses` — список бизнесов
- `GET /api/businesses/:id` — детали бизнеса + услуги + мастера
- `GET /api/businesses/:id/slots?service_id=&date=` — свободные слоты
- `POST /api/bookings` — создать запись
- `GET /api/my/bookings` — мои записи
- `POST /api/bookings/:id/cancel` — отменить
- `POST /api/bookings/:id/reschedule` — перенести
- `POST /api/ai/chat` — AI-чат

### Admin
- `GET /api/admin/dashboard` — дашборд
- `GET /api/admin/bookings?date=&status=` — записи
- `POST /api/admin/bookings` — создать запись
- `PATCH /api/admin/bookings/:id` — изменить
- `CRUD /api/admin/services`
- `CRUD /api/admin/employees`
- `GET /api/admin/analytics`
- `PATCH /api/admin/settings`

## Файловая структура

```
booking_bot/
├── webapp/          # Существующий aiohttp (не трогаем)
├── web/
│   ├── api/         # FastAPI backend
│   │   ├── main.py
│   │   ├── auth.py
│   │   ├── routes/
│   │   │   ├── client.py
│   │   │   ├── admin.py
│   │   │   └── ai.py
│   │   ├── deps.py  # Dependencies (auth, db session)
│   │   └── schemas.py
│   └── frontend/    # React app
│       ├── package.json
│       ├── vite.config.ts
│       ├── tailwind.config.ts
│       ├── src/
│       │   ├── main.tsx
│       │   ├── App.tsx
│       │   ├── api/        # API client
│       │   ├── components/ # Shared UI
│       │   ├── pages/
│       │   │   ├── client/
│       │   │   └── admin/
│       │   ├── hooks/
│       │   ├── stores/     # State management
│       │   └── styles/
│       └── public/
└── docs/plans/
```

## Порядок реализации

### Фаза 1: Скелет (MVP)
1. React проект + Tailwind + роутинг
2. FastAPI сервер + JWT auth
3. Клиент: главная → бизнес → запись
4. Nginx конфиг

### Фаза 2: Полный клиент
5. Мои записи
6. AI-чат
7. Telegram Login Widget
8. Абонементы

### Фаза 3: Админ-панель
9. Дашборд + расписание
10. Управление услугами/сотрудниками
11. Аналитика
12. Настройки бизнеса
