# ServGo — матрица всех пользовательских функций для редизайна v4

**Назначение:** контроль полноты нового мобильного и десктопного фронтенда. Каждый пункт основан на маршруте или конкретном исходном модуле, однако **наличие кода не подтверждает работу на production**. Дата снимка: 30.10.2026.

**Легенда:** P0 — обязательный безопасный путь и права; P1 — основная функция нового интерфейса; P2 — вспомогательный раздел; «Смежный продукт» — сохраняем связку, не копируем функциональность BookingBot. Все элементы v4 сегодня — **демо**, не реальное управление.

## Главная

| Функция | Подтверждение в коде | Раздел нового UI | Задача интеграции / ограничение | Важность |
|---|---|---|---|---|
| Пустой стартовый экран с одним вводом | [`GET /studio/products/{product_id}/comments`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/comments.py#L76) | Главная | Существующая Studio; v4 пока отдельный макет | P0 |
| Текстовые команды диспетчеру | [`POST /studio/products/{product_id}/dispatch/messages`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/dispatcher.py#L183) | Главная → ответ | Существующий API; подключить новый компонент | P0 |
| Голосовая диктовка | [`POST /studio/voice/dictate`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/voice.py#L139) | Главная → микрофон | API есть; проверить разрешения, загрузку и ошибки | P0 |
| Динамические подсказки и контекст | [`GET /studio/products/{product_id}/dispatch/state`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/dispatcher.py#L176) | Главная → чипы | Читать состояние из backend, не фиктивные цифры | P0 |
| Подтверждение платных или внешних действий | [`POST /studio/products/{product_id}/dispatch/confirm`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/dispatcher.py#L199) | После команды → подтверждение | Сохранять реальные проверки полномочий и стоимости | P0 |
| Обновления долгих задач | [`GET /studio/products/{product_id}/dispatch/updates`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/dispatcher.py#L215) | После команды → прогресс | Добавить статусы, отмену и повтор без потери истории | P0 |

## Работа и контент

| Функция | Подтверждение в коде | Раздел нового UI | Задача интеграции / ограничение | Важность |
|---|---|---|---|---|
| Создание поста и контент-пакета | [`GET /studio/products/{product_id}/create`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2075) | Создать | Перенести текущую форму с полями и ограничениями | P0 |
| Планы и утверждение плана | [`GET /studio/products/{product_id}/plan`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1851) | Контент-план | Сохранить различие черновик/активный план | P0 |
| Календарь контента | [`GET /studio/products/{product_id}/calendar`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1863) | Календарь | Подключить реальные слоты, перемещение и ограничения | P1 |
| Очередь и статусы задач | [`GET /studio/products/{product_id}/work`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2135) | В работе | Сохранить доступ к ошибкам и деталям | P0 |
| Повтор задачи после ошибки | [`POST /studio/products/{product_id}/tasks/{task_id}/retry`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2217) | В работе → задача | Только с текущими проверками разрешённых повторов | P1 |
| Библиотека и поиск материалов | [`GET /studio/products/{product_id}/materials`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1873) | Материалы | Фильтры, поиск, версии и статусы | P0 |
| Согласование и доработка | [`POST /studio/products/{product_id}/review/{artifact_id}/approve`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2260) | Материалы → проверка | Без автоодобрения от рендера, сохранить audit | P0 |
| Ручное редактирование материала | [`POST /studio/products/{product_id}/materials/{artifact_id}/edit`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2063) | Материалы → редактор | Редактирование с безопасным сохранением | P1 |
| Подключение каналов и публикация | [`POST /products/{product_id}/comments/channels/{platform}/{action}`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/comments.py#L211) | Каналы | Показывать реальные подключённые площадки | P1 |
| Планирование публикаций | [`POST /studio/products/{product_id}/publishing/schedule`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2350) | Каналы → запланировать | Только после согласования по backend | P0 |
| Комментарий, ответы, модерация | [`GET /studio/assets/comments.css`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/comments.py#L66) | Комментарии | Сохранять настройки доступов и каналы | P2 |

## SEO-завод

| Функция | Подтверждение в коде | Раздел нового UI | Задача интеграции / ограничение | Важность |
|---|---|---|---|---|
| Запуск SEO-завода через диспетчер | [`POST /studio/products/{product_id}/dispatch/messages`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/dispatcher.py#L183) | SEO-завод → запуск | Команда seo_factory есть; отдельной полнофункциональной страницы API пока не обнаружено | P1 |
| Сбор семантики и кластеров | [`GET /products/{product_id}/artifacts/{artifact_id}`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/products.py#L142) | SEO-завод → семантика | Код есть в app/seo; нужен агрегирующий read-only endpoint для UI | P1 |
| Производство и контроль качества статей | [`POST /products/{product_id}/articles`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/products.py#L127) | SEO-завод → конвейер | Сохранить реальные этапы/аудит и устранить выявленные регрессионные риски | P1 |
| Публикация статей и публичная лента | [`GET /public/blog/{product_slug}/articles`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/public_blog.py#L63) | SEO-завод → статьи/публикации | Есть публичная лента; статус живого блога проверить отдельно | P1 |

## Видео

| Функция | Подтверждение в коде | Раздел нового UI | Задача интеграции / ограничение | Важность |
|---|---|---|---|---|
| Простое видео — готовый анимированный MP4 | [`POST /products/{product_id}/whiteboard/jobs`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/whiteboard.py#L128) | Создать → Простое видео | Переименовать UI везде; не менять internal whiteboard API | P1 |
| Утверждение, отмена, возобновление, исправление видео | [`GET /studio/products/{product_id}/whiteboard/{task_id}`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/whiteboard.py#L436) | Видео → задача | Нужны статусы и управление из нового интерфейса | P1 |
| Сценарии Shorts/Reels и визуальные брифы | [`GET /studio/products/{product_id}/create`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2075) | Создать → Видео/Stories | Отличать сценарий/бриф от готового MP4 | P1 |

## Команда

| Функция | Подтверждение в коде | Раздел нового UI | Задача интеграции / ограничение | Важность |
|---|---|---|---|---|
| Управление ИИ-ролями и назначенными моделями | [`GET /studio/team/api`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L265) | ИИ-команда | Есть админ API; разделить доступ владельца платформы и клиента | P1 |
| Предпросмотр изменений состава ИИ-команды | [`POST /studio/team/api/preview`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L283) | ИИ-команда → изменения | Предварительная проверка до сохранения | P1 |
| Каталог доступных моделей/ролей | [`GET /api/admin/team/catalog`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L313) | ИИ-команда → каталог | Отображать только подтверждённые модельные ID | P1 |
| Статистика моделей и расхода | [`GET /api/admin/team/usage`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L335) | ИИ-команда → расходы | Реальные затраты, неизвестные usage не приравнивать к 0 | P1 |
| Промпты и настройка поведения | [`GET /studio/tune`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/tune.py#L133) | ИИ-команда → настройки | Сохранить контроль версий и аудит изменений | P2 |

## Разведка и маркетинг

| Функция | Подтверждение в коде | Раздел нового UI | Задача интеграции / ограничение | Важность |
|---|---|---|---|---|
| Сбор конкурентной разведки | [`POST /competitive-intelligence/runs`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/competitive_intelligence.py#L33) | Разведка | Отчёты, источники и события выполнения | P1 |
| Экспорт находок разведки | [`POST /competitive-intelligence/runs/{run_id}/follow-up`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/competitive_intelligence.py#L68) | Разведка → действия | Не путать экспорт, доработку и запуск кампании | P1 |
| Цели, эксперименты и рост | [`POST /products/{product_id}/experiments`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/products.py#L66) | Кампании → эксперименты | Нет подтверждённой единой сущности рекламной кампании со всеми каналами; необходима модель UI | P1 |
| Суточные обзоры роста | [`POST /products/{product_id}/daily-growth-review`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/products.py#L83) | Аналитика → выводы | Существующий endpoint; сделать контекстные карточки после команды | P1 |
| Аналитика Growth OS | [`GET /studio/products/{product_id}/analytics`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1947) | Аналитика | Свести реальную аналитику с BookingBot через проверенные интеграции | P1 |

## Почта

| Функция | Подтверждение в коде | Раздел нового UI | Задача интеграции / ограничение | Важность |
|---|---|---|---|---|
| Поиск компаний в 2ГИС, сайтов и email | [`GET /api/owner-mail/api/company-search/companies`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_company_search.py#L406) | Почта → поиск компаний | Отдельный BookingBot сервис; блокировки/частичные результаты должны отображаться | P1 |
| Просмотр и отбор найденных компаний | [`GET /api/owner-mail/api/company-search/companies`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_company_search.py#L406) | Почта → найденные | Не переносить чужие контакты между компаниями | P1 |
| База адресатов и ручное добавление | [`GET /api/owner-mail/api/prospects`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L308) | Почта → контакты | Оставить согласия, исключения и источники адресов | P1 |
| AI-черновик личного письма | [`POST /api/owner-mail/api/draft/{prospect_id}`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L487) | Почта → письмо | Проверка текста до отправки | P1 |
| Отправка и согласование личного письма | [`POST /api/owner-mail/api/send/{prospect_id}`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L560) | Почта → отправка | Строго сохранять текущие проверки разрешений и SMTP | P1 |
| Создание email-кампаний | [`GET /api/owner-mail/api/campaigns/{campaign_id}/analytics`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_analytics.py#L25) | Кампании → email | Существующий модуль владельца платформы; мультитенантное расширение отдельно | P1 |
| Получатели и персонализация кампании | [`POST /api/owner-mail/api/campaigns/{campaign_id}/recipients/{recipient_id}/reply`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_analytics.py#L45) | Email-кампания → получатели | Существующий API, отдельная авторизация | P1 |
| Подготовка, утверждение, запуск, пауза, завершение | [`POST /api/owner-mail/api/campaigns/{campaign_id}/launch`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_review.py#L97) | Email-кампания → управление | Вывести все стадии и явные подтверждения | P1 |
| Аналитика рассылки и ответы | [`GET /api/owner-mail/api/campaigns/{campaign_id}/analytics`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_analytics.py#L25) | Email-кампания → результаты | Не обещать доставку только по факту принятия SMTP | P1 |
| Входящие и исходящие письма | [`GET /api/owner-mail/api/inbox`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L639) | Почта → входящие/исходящие | Реальные письма, без демо-статусов | P1 |
| Отписка, исключения и ограничения | [`GET /api/owner-mail/unsubscribe`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_unsubscribe.py#L24) | Почта → отписки | Нельзя скрывать или обходить отписку | P0 |

## BookingBot

| Функция | Подтверждение в коде | Раздел нового UI | Задача интеграции / ограничение | Важность |
|---|---|---|---|---|
| Просмотр расписания и свободных слотов | [`GET /api/businesses/{business_id}/slots`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/client.py#L410) | Главная → пустые окна/календарь | Данные из BookingBot; выводить фактические даты и слоты | P0 |
| Услуги, мастера, ресурсы | [`GET /api/admin/businesses/{business_id}/services`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L293) | Настройки → услуги и команда | Пока управление BookingBot; можно оставить deep link | P2 |
| Создание и изменение записей администратором | [`GET /api/admin/bookings`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L249) | Главная/Календарь | Существующие ограничения конфликтов и доступов обязательны | P1 |
| Клиентский путь записи | [`PAGE /book/:businessId`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L141) | BookingBot — отдельная клиентская витрина | Не заменять Growth OS, сохранить связь | Смежный продукт |
| Предоплаты и подтверждение | [`GET /api/admin/prepayments/attention`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/payments.py#L20) | BookingBot → платежи | Никаких автоматических подтверждений по макету | Смежный продукт |
| Абонементы | [`GET /api/admin/businesses/{business_id}/subscriptions`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L2196) | BookingBot → абонементы | Не дублировать бизнес-логику в Growth OS | Смежный продукт |
| Управление арендой | [`GET /api/businesses/{business_id}/rental/slots`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/rental.py#L84) | BookingBot → аренда | Оставить в профильном модуле, при необходимости ссылка | Смежный продукт |
| ИИ-чат бронирования | [`POST /api/ai/chat`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/ai.py#L106) | BookingBot → AI-запись | Раздельные контексты: запись клиента ≠ диспетчер владельца | Смежный продукт |
| Продажи и входящие обращения | [`PAGE /admin/sales`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L169) | BookingBot → CRM/продажи | Проверить полноту CRM клиентов до обещания объединения | P2 |
| Кабинет мастера | [`GET /api/master/schedule`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/master.py#L140) | BookingBot → мастер | Ролевой доступ сохранить без расширения прав | Смежный продукт |
| Push-подписки и уведомления | [`POST /api/notifications/subscribe`](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/notifications.py#L70) | Уведомления | Каналы и разрешения браузера; iOS PWA отдельно проверять | P2 |

## Платформа

| Функция | Подтверждение в коде | Раздел нового UI | Задача интеграции / ограничение | Важность |
|---|---|---|---|---|
| Добавить компанию и собрать профиль | [`GET /studio/new`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio_company.py#L200) | Настройки → компании | Существующий intake; нельзя терять ручное утверждение профиля | P1 |
| Права сотрудников и доступ компаний | [`GET /studio/products/{product_id}/accounts`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/studio_accounts.py#L143) | Настройки → доступы | Только разрешённые пользователи; проверить owner boundary | P0 |
| SSO между Growth и Booking | [`POST /studio/sso/create`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/studio_sso.py#L144) | Настройки → интеграции | Действующие ограничения токенов и привязки компаний сохранять | P0 |
| Здоровье приложения и версия | [`GET /health`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/health.py#L6) | Настройки → диагностика | Для оператора, не главный экран бизнеса | P2 |
| Обновление дизайна в отдельной ветке | [`GET /studio/design-preview`](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/growth_design_preview.py#L20) | Только review | Отключён по умолчанию; без вызовов production API | Review |

## Команды голосового диспетчера (19)

Все команды перечислены в [исходном реестре диспетчера](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py). Это команды API, не обязательно отдельные пункты меню.

| ID | Пользовательское название в коде | Где объявлено |
|---|---|---|
| `show_work` | Что в работе | [registry.py:164](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L164) |
| `show_review` | Что ждёт проверки | [registry.py:167](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L167) |
| `show_plan` | Показать контент-план | [registry.py:170](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L170) |
| `go_to` | Где это находится | [registry.py:173](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L173) |
| `show_schedule` | Расписание записей | [registry.py:177](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L177) |
| `show_insights` | Итоги бизнеса по записям | [registry.py:182](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L182) |
| `set_insights_depth` | Глубина анализа | [registry.py:189](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L189) |
| `send_to_team` | Передать данные команде | [registry.py:193](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L193) |
| `create_post` | Создать пост | [registry.py:197](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L197) |
| `make_plan` | Составить контент-план | [registry.py:201](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L201) |
| `seo_factory` | SEO-завод | [registry.py:204](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L204) |
| `approve_plan` | Утвердить контент-план | [registry.py:211](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L211) |
| `show_material` | Показать готовый материал | [registry.py:215](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L215) |
| `approve_material` | Утвердить материал | [registry.py:219](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L219) |
| `revise_material` | Доработать пост | [registry.py:224](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L224) |
| `schedule_post` | Запланировать публикацию | [registry.py:230](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L230) |
| `whiteboard_video` | **В интерфейсе v4: Простое видео** (старое название: «Рисованное видео») | [registry.py:235](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L235) |
| `cancel_video` | Отменить рисованное видео | [registry.py:239](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L239) |
| `restricted_action` | Действие под запретом | [registry.py:242](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/dispatcher/registry.py#L242) |

## Критические правила полноты

1. **Пустая главная:** при загрузке не показывать загромождённые графики; метрики и команды появляются только после осмысленного запроса или открытия специализированного раздела.
2. **Права:** platform-owner email-модуль из BookingBot и админ-настройки моделей нельзя автоматически предоставлять любой бизнес-компании. Для нового SaaS нужны явные правила авторизации и изоляции арендаторов.
3. **Контент:** различать сценарий видео, готовый MP4, SEO-статью, обычный пост, запланированную и подтверждённую публикацию.
4. **Почта:** поиск контактов не равен разрешению на рассылку. Сохранять исключения, отписки, проверки, ограничения скорости и реальные статусы SMTP.
5. **SEO:** до массовой автопубликации проверить восстановление зависших задач, повтор планов и критика для финальной версии.
6. **Исторические настройки:** перед скрытием старых страниц провести сверку всех форм `/studio/products/{id}/*`, `/studio/team`, `/owner-mail` и админки BookingBot.
7. **Запуск:** не делать деплой с этой ветки до регрессии, role-specific UI-тестов и проверки backend интеграций.

## Что ещё отсутствует в v4 как рабочая интеграция

- Реальная отправка команд из новой главной и отображение структурированных ответов/подтверждений.
- API-связка нового SEO-экрана с семантикой, прогрессом, статьями и аудитом.
- Отдельная мультитенантная модель почты и кампаний для клиентов (текущий owner-mail основан на правах владельца платформы).
- Контролируемая интеграция BookingBot данных о расписании и аналитике.
- Полные UI-переходы для задач, кампаний, критика и видео без возврата к старым страницам.
- Тесты доступности, мобильной вёрстки и сохранения всех функций по ролям.
