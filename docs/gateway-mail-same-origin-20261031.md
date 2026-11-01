# ServGo Mail: same-origin PWA — подготовка к staged rollout
**Дата:** 31.10.2026. **Статус:** review-only, feature flag OFF, production не изменён.

## Зачем этот этап

Пользователь устанавливает ServGo с `https://servgo.ru/` и должен открывать клиентскую запись, админку, Growth и Mail **в одном окне установленного PWA**. Для Mail оказалось возможным обойтись без отдельного nginx cross-origin прокси: все защищённые Mail обработчики **уже подключены** к BookingBot FastAPI с префиксом `/owner-mail`, который на основном сайте доступен как `/api/owner-mail`.

Код: [owner_mail.py](../web/api/routes/owner_mail.py), [mail_pwa.py](../web/api/routes/mail_pwa.py), [main.py](../web/api/main.py), [OwnerMailPage.tsx](../web/frontend/src/pages/client/OwnerMailPage.tsx).

## Маршруты (новые, opt-in)

| URL в ServGo PWA | Что делает | Доступ |
|---|---|---|
| `/workspace/mail` | React-экран входа, получает билет из POST `/api/owner-mail/entry` | BookingBot login, затем platform mail_owner |
| `/api/owner-mail/claim?ticket=...` | Проверка подписи, TTL 60 секунд, одноразового JTI и назначения билета pwa; выдаёт HttpOnly cookie | Подтверждённый владелец платформы |
| `/api/owner-mail/workspace` | Кампании, HTML с безопасно переписанными API-маршрутами | Только platform mail_owner |
| `/api/owner-mail/workspace/mailbox` | Входящие, исходящие, адресаты, черновики, HTML + собственный API | Только platform mail_owner |
| `/api/owner-mail/workspace/logout` | Удаляет **только** cookie Mail, возвращает на `/admin` | Только canonical PWA origin |
| `/api/owner-mail/api/*` | Действующие обработчики контактов, почты, поиска компаний и кампаний | Существующая серверная mail_owner + Origin-проверка для записей |

Новый маршрут не меняет фоновый сервис отправки, SMTP, IMAP, правила рассылок или бизнес-данные. Старый вход `https://mail.servgo.ru/claim` продолжает работать, когда флаг выключен, а после включения **существующие legacy tickets** продолжают приниматься только на старом host.

## Флаг, cookie и защита

`SERVGO_MAIL_GATEWAY_ENABLED=false` по умолчанию. Если опция включена, POST `/api/owner-mail/entry` по-прежнему проверяет пользователя через серверный `require_owner`, но подписывает ticket с `destination=pwa`, который идёт на фиксированный `https://servgo.ru/api/owner-mail/claim`. При выключенном флаге билет подписан как `destination=legacy` и ведёт на старый host. URL запроса и пользовательский Host **не** управляют назначением билета.

На приёме canonical ticket проверяются подпись, срок, платформа-владелец, привязка `destination=pwa`, одноразовость JTI и действительный HTTPS Host. При успехе выставляется Cookie `servgo_owner_mail` с `Secure; HttpOnly; SameSite=Lax; Path=/api/owner-mail`. Это **не** BookingBot cookie и она не отправляется браузером в `/api/auth`, `/studio`, `/admin` или обычную запись.

Для любого почтового POST/PUT/PATCH/DELETE `verify_mail_write_origin` сравнивает `Origin` с действительным HTTPS Host (`servgo.ru` либо `mail.servgo.ru`). Запрос без Origin, с Cross-Site metadata, с чужим Host или сменой hostname запрещён. При отсутствии Mail session запрос к приватным HTML переводит на `/mail` для входа; без `mail_owner` возвращается 403.

Приватный HTML отдаётся с `Cache-Control: private, no-store`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY` и CSP. Приватные страницы нельзя кешировать в service worker. **Зависимость PWA SW v3 — жёсткий блокер релиза**.

## Почему в базе PR #103

Данный [PR #105](https://github.com/grunvald91/booking_bot/pull/105) **стековый** и базируется на [PR #103 — темы и ServGo Air](https://github.com/grunvald91/booking_bot/pull/103). Его HTML-адаптер перенаправляет все ссылки статических Mail CSS/JS из /api в /api/owner-mail/api и использует существующие ресурсы из #103; прямое слияние #105 в main до #103 не допускается. [PR #102 — безопасный SW](https://github.com/grunvald91/booking_bot/pull/102) — независимый обязательный предшественник для любой единой PWA-маршрутизации.

Growth имеет другой путь: [BookingBot #104](https://github.com/grunvald91/booking_bot/pull/104) + [Growth OS #183](https://github.com/grunvald91/Ai_growth_os/pull/183), с отдельным маршрутным proxy и действующим SSO. Growth backend и Mail backend **не** надо сливать в одну базу или сервис.

## Что проверено автоматически

- Восемь изолированных тестов `tests/test_mail_same_origin.py`: default OFF; signed 60s ticket и audience destination; попытка использовать ticket на другом host; replay; путь Cookie; Host+Origin cross-site; роль платформенного владельца; HTML API rewriting; logout.
- `web/frontend` собран CI, прогнаны тесты темы и основные существующие BookingBot regression tests.
- [GitHub Actions run](https://github.com/grunvald91/booking_bot/actions/runs/41178994829) — зелёный.
- Тесты запускаются на временных данных без production, SMTP, IMAP или отправки реальных писем.

## Реальные блокеры перед включением на сервере

1. Завершить ревью и staged deploy SW v3, убедиться, что установленный PWA обновился (особенно старые Android устройства).
2. Завершить #103: общие цвета и Mail static asset endpoints. Проверить светлую и тёмную тему клиента, админки, Mail на телефоне.
3. Проверить фактический **production nginx** для `servgo.ru`: `/api/owner-mail/*` идёт в действующий FastAPI, `root_path="/api"` не удваивается, Host и X-Forwarded-Proto корректны. Код `web/nginx.conf` в репозитории имеет другой `server_name`; он не доказывает production настройки.
4. Не менять `mail.servgo.ru` или его cookies во время тестирования canonical пути. **До выпуска закрыть логирование одноразовых секретов**: на действующем nginx точечно отключить запись URI с `GET /api/owner-mail/claim?ticket=…` (или маскировать query); не фиксировать токены в диагностике. Нельзя переписывать весь `/api/*` ради этого — сначала проверить фактическую схему сервера.
5. В staging (отдельно от production) включить `SERVGO_MAIL_GATEWAY_ENABLED=true`. В браузере с ролью владельца проверить /workspace/mail → claim → campaigns → mailbox → подготовка черновика (без SMTP отправки) → logout → /admin, back/reload, PWA standalone, отсутствие чувствительных данных Cache Storage.
6. Негативные проверки: любой посетитель, бизнес-владелец, сотрудник, чужой Origin, поддельный билет, повторное использование, истёкший билет, SQL/data isolation; запросы записи клиентов остаются в BookingBot.
7. Только после staged-проверок и отдельного решения владельца включать новый маршрут в production.

## Rollback

`SERVGO_MAIL_GATEWAY_ENABLED=false`: новые ссылки опять направляются на `https://mail.servgo.ru/claim`; старые страницы продолжают работать. Не удалять новый mail-only path-scoped cookie с помощью глобального logout — canonical `/api/owner-mail/workspace/logout` удаляет только свою cookie. Для отката UI можно временно скрыть новый `/workspace/mail` header entry; Mail API и переписку не затрагивать.

**Ограничение:** открытие из сторонних приложений непосредственно в установленный PWA зависит от Android/iOS и браузера; внутренние same-origin переходы внутри уже запущенного PWA — наш технический критерий приёмки. Реальный тест на устройстве не проводился.
