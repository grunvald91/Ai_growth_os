# ServGo — технический реестр HTTP- и UI-маршрутов

**Снимок исходников:** 30.10.2026. **Метод:** статический просмотр объявлений маршрутов в двух GitHub-репозиториях; это не подтверждение доступности на сервере.

**Всего 382 объявлений**, включая 29 React-маршрутов, 1 static-ресурс и 352 HTTP-маршрутов. Один и тот же URL может встречаться в разных сервисах или иметь несколько HTTP-методов; это *объявления*, не уникальные бизнес-функции.

- Основной файл для фильтрации: [SERVGO_ROUTE_REGISTRY.csv](SERVGO_ROUTE_REGISTRY.csv) (UTF‑8 с BOM; Excel/Sheets).
- Машинный формат с метаданными и источниками: [SERVGO_ROUTE_REGISTRY.json](SERVGO_ROUTE_REGISTRY.json).
- Карта бизнес-функций и соответствия новому фронтенду: [SERVGO_FUNCTION_MATRIX.md](SERVGO_FUNCTION_MATRIX.md).
- Команды диспетчера: [registry_dispatcher_capabilities.json](registry_dispatcher_capabilities.json).

## Методология и ограничения

- Growth OS: `app/api/*.py`, включая новую *review-only* страницу дизайна; снимок ветки создан от `8a87d1b0722115652fe3db82a557b734db2834bb`.
- BookingBot: `web/api/routes/*.py`, `web/api/rental.py`, `web/api/main.py`, `webapp/server.py`, `web/frontend/src/App.tsx` на коммите `eabd7b6b1321c8a3b147fcdb782241ba688cf47c`.
- Для BookingBot внешний префикс `/api` указан на основании `FastAPI(root_path="/api")`; окончательную схему проксирования nginx нужно подтвердить в среде исполнения. `webapp/server.py` и React перечислены отдельно, без обязательного префикса.
- Список включает защищённые, публичные, внутренние и устаревшие/redirect-маршруты. **Не означает**, что все они должны отображаться в меню Growth OS.
- Это не проверка прав пользователя, побочных эффектов, актуальной конфигурации, доступности внешних провайдеров и успешности деплоя. Динамически зарегистрированные маршруты вне проверенных файлов или Telegram-бот-команды могут не попасть в этот HTTP-реестр.
- `GET /studio/design-preview` — только для ветки проверки; по умолчанию отключён.

## Сводка по источникам

| Источник | Объявлений |
|---|---:|
| Growth OS FastAPI (включая 1 preview) | 141 |
| BookingBot FastAPI | 164 |
| BookingBot aiohttp | 48 |
| BookingBot React | 29 |
| **Всего** | **382** |

## Подробный список

Каждая строка ссылается на объявление маршрута в GitHub; при переименовании/удалении файла источник необходимо пересмотреть.

### BookingBot / Интеграция BookingBot ↔ Growth (1)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/api/internal/studio/schedule` | `studio_schedule` | [studio_schedule.py:74](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/studio_schedule.py#L74) |

### BookingBot / Почта: письма и кампании (37)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/api/owner-mail/api/campaigns/{campaign_id}/analytics` | `report` | [mail_campaign_analytics.py:25](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_analytics.py#L25) |
| `POST` | `/api/owner-mail/api/campaigns/{campaign_id}/recipients/{recipient_id}/reply` | `label_reply` | [mail_campaign_analytics.py:45](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_analytics.py#L45) |
| `POST` | `/api/owner-mail/api/campaigns` | `create_campaign` | [mail_campaign_api.py:47](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_api.py#L47) |
| `GET` | `/api/owner-mail/api/campaigns` | `list_campaigns` | [mail_campaign_api.py:62](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_api.py#L62) |
| `GET` | `/api/owner-mail/api/campaigns/automation` | `get_automation` | [mail_campaign_api.py:73](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_api.py#L73) |
| `PUT` | `/api/owner-mail/api/campaigns/automation` | `set_automation` | [mail_campaign_api.py:80](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_api.py#L80) |
| `GET` | `/api/owner-mail/api/campaigns/{campaign_id}` | `campaign_detail` | [mail_campaign_api.py:89](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_api.py#L89) |
| `POST` | `/api/owner-mail/api/campaigns/{campaign_id}/recipients` | `add_recipients` | [mail_campaign_api.py:105](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_api.py#L105) |
| `POST` | `/api/owner-mail/api/campaigns/{campaign_id}/prepare` | `prepare` | [mail_campaign_review.py:34](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_review.py#L34) |
| `PATCH` | `/api/owner-mail/api/campaigns/{campaign_id}/recipients/{recipient_id}` | `edit_message` | [mail_campaign_review.py:50](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_review.py#L50) |
| `POST` | `/api/owner-mail/api/campaigns/{campaign_id}/approve` | `approve` | [mail_campaign_review.py:71](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_review.py#L71) |
| `POST` | `/api/owner-mail/api/campaigns/{campaign_id}/launch` | `launch` | [mail_campaign_review.py:97](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_review.py#L97) |
| `POST` | `/api/owner-mail/api/campaigns/{campaign_id}/pause` | `pause` | [mail_campaign_review.py:122](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_review.py#L122) |
| `POST` | `/api/owner-mail/api/campaigns/{campaign_id}/finish` | `finish` | [mail_campaign_review.py:135](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_review.py#L135) |
| `POST` | `/api/owner-mail/api/campaigns/{campaign_id}/recipients/{recipient_id}/exclude` | `exclude` | [mail_campaign_review.py:148](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_review.py#L148) |
| `GET` | `/api/owner-mail/api/campaigns-dashboard` | `campaign_dashboard` | [mail_campaign_ui.py:15](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_ui.py#L15) |
| `GET` | `/api/owner-mail/api/mailbox-dashboard` | `mailbox_dashboard` | [mail_campaign_ui.py:39](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_ui.py#L39) |
| `POST` | `/api/owner-mail/entry` | `entry` | [owner_mail.py:142](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L142) |
| `GET` | `/api/owner-mail/claim` | `claim` | [owner_mail.py:148](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L148) |
| `POST` | `/api/owner-mail/api/login` | `login` | [owner_mail.py:197](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L197) |
| `GET` | `/api/owner-mail/api/logout` | `logout` | [owner_mail.py:234](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L234) |
| `GET` | `/api/owner-mail/app` | `app_page` | [owner_mail.py:271](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L271) |
| `GET` | `/api/owner-mail/api/status` | `status` | [owner_mail.py:299](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L299) |
| `GET` | `/api/owner-mail/api/prospects` | `prospects` | [owner_mail.py:308](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L308) |
| `POST` | `/api/owner-mail/api/prospects` | `add_prospect` | [owner_mail.py:318](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L318) |
| `POST` | `/api/owner-mail/api/draft/{prospect_id}` | `generate_draft` | [owner_mail.py:487](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L487) |
| `POST` | `/api/owner-mail/api/approve/{prospect_id}` | `approve_draft` | [owner_mail.py:548](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L548) |
| `POST` | `/api/owner-mail/api/send/{prospect_id}` | `send_draft` | [owner_mail.py:560](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L560) |
| `POST` | `/api/owner-mail/api/exclude/{prospect_id}` | `exclude` | [owner_mail.py:598](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L598) |
| `GET` | `/api/owner-mail/api/inbox` | `inbox` | [owner_mail.py:639](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L639) |
| `POST` | `/api/owner-mail/api/direct-draft` | `direct_draft` | [owner_mail.py:678](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L678) |
| `POST` | `/api/owner-mail/api/direct-send` | `direct_send` | [owner_mail.py:730](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L730) |
| `GET` | `/api/owner-mail/api/outbox` | `outbox` | [owner_mail.py:780](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L780) |
| `GET` | `/api/owner-mail/permission` | `permission` | [owner_mail.py:796](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L796) |
| `GET` | `/api/owner-mail/api/message/{uid}` | `read_message` | [owner_mail.py:851](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/owner_mail.py#L851) |
| `GET` | `/api/owner-mail/unsubscribe` | `unsubscribe_page` | [mail_campaign_unsubscribe.py:24](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_unsubscribe.py#L24) |
| `POST` | `/api/owner-mail/unsubscribe` | `unsubscribe` | [mail_campaign_unsubscribe.py:43](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_campaign_unsubscribe.py#L43) |

### BookingBot / Почта: поиск компаний и email (7)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/api/owner-mail/api/company-search/companies` | `companies` | [mail_company_search.py:406](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_company_search.py#L406) |
| `POST` | `/api/owner-mail/api/company-search/suggest` | `suggest` | [mail_company_search.py:418](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_company_search.py#L418) |
| `POST` | `/api/owner-mail/api/company-search` | `start` | [mail_company_search.py:439](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_company_search.py#L439) |
| `GET` | `/api/owner-mail/api/company-search` | `recent` | [mail_company_search.py:456](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_company_search.py#L456) |
| `GET` | `/api/owner-mail/api/company-search/{job_id}` | `detail` | [mail_company_search.py:463](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_company_search.py#L463) |
| `POST` | `/api/owner-mail/api/company-search/{job_id}/cancel` | `cancel` | [mail_company_search.py:470](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_company_search.py#L470) |
| `POST` | `/api/owner-mail/api/company-search/{job_id}/to-campaign` | `to_campaign` | [mail_company_search.py:479](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/mail_company_search.py#L479) |

### BookingBot / Прочие API и сервисы (1)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/api/health` | `health` | [main.py:42](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/main.py#L42) |

### BookingBot / Учётные записи и доступ (26)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `POST` | `/api/auth/phone` | `request_code` | [auth.py:224](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/auth.py#L224) |
| `POST` | `/api/auth/verify` | `verify_code` | [auth.py:241](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/auth.py#L241) |
| `POST` | `/api/auth/telegram-login` | `telegram_login_widget` | [auth.py:351](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/auth.py#L351) |
| `GET` | `/api/auth/session` | `session_from_shared_cookie` | [auth.py:376](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/auth.py#L376) |
| `POST` | `/api/auth/telegram` | `telegram_auth` | [auth.py:399](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/auth.py#L399) |
| `POST` | `/api/auth/logout` | `logout` | [auth.py:431](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/auth.py#L431) |
| `GET` | `/api/auth/me` | `me` | [auth.py:445](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/auth.py#L445) |
| `GET` | `/api/auth/email/capabilities` | `capabilities` | [email_auth.py:110](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/email_auth.py#L110) |
| `GET` | `/api/auth/email/account` | `account` | [email_auth.py:115](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/email_auth.py#L115) |
| `POST` | `/api/auth/email/start` | `begin` | [email_auth.py:124](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/email_auth.py#L124) |
| `POST` | `/api/auth/email/verify` | `verify` | [email_auth.py:157](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/email_auth.py#L157) |
| `POST` | `/api/auth/email/login` | `login` | [email_auth.py:216](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/email_auth.py#L216) |
| `GET` | `/api/studio/businesses` | `studio_businesses` | [studio_sso.py:86](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/studio_sso.py#L86) |
| `POST` | `/api/studio/ticket` | `studio_ticket` | [studio_sso.py:118](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/studio_sso.py#L118) |
| `GET` | `/api/internal/studio/access` | `studio_access` | [studio_sso.py:135](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/studio_sso.py#L135) |
| `GET` | `/api/internal/studio/business` | `studio_business` | [studio_sso.py:180](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/studio_sso.py#L180) |
| `POST` | `/api/auth/telegram-link/start` | `start` | [telegram_link.py:62](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/telegram_link.py#L62) |
| `GET` | `/api/auth/telegram-link/{request_id}` | `status` | [telegram_link.py:79](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/telegram_link.py#L79) |
| `POST` | `/api/auth/telegram-link/complete` | `complete` | [telegram_link.py:93](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/telegram_link.py#L93) |
| `POST` | `/api/auth/telegram-link/{request_id}/cancel` | `cancel` | [telegram_link.py:109](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/telegram_link.py#L109) |
| `GET` | `/api/auth/yandex/capabilities` | `capabilities` | [yandex_auth.py:159](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/yandex_auth.py#L159) |
| `GET` | `/api/auth/yandex/start` | `start` | [yandex_auth.py:164](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/yandex_auth.py#L164) |
| `POST` | `/api/auth/yandex/link/start` | `link_start` | [yandex_auth.py:174](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/yandex_auth.py#L174) |
| `GET` | `/api/auth/yandex/account` | `account` | [yandex_auth.py:188](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/yandex_auth.py#L188) |
| `GET` | `/api/auth/yandex/callback` | `callback` | [yandex_auth.py:195](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/yandex_auth.py#L195) |
| `POST` | `/api/auth/yandex/exchange` | `exchange` | [yandex_auth.py:357](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/yandex_auth.py#L357) |

### BookingBot / BookingBot: абонементы (5)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/api/subscriptions` | `list_subscriptions` | [subscriptions.py:26](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/subscriptions.py#L26) |
| `GET` | `/api/subscriptions/{subscription_id}` | `get_subscription` | [subscriptions.py:65](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/subscriptions.py#L65) |
| `POST` | `/api/subscriptions/{subscription_id}/buy` | `buy_subscription` | [subscriptions.py:100](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/subscriptions.py#L100) |
| `GET` | `/api/my/subscriptions` | `my_subscriptions` | [subscriptions.py:151](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/subscriptions.py#L151) |
| `POST` | `/api/my/subscriptions/{client_sub_id}/payment-sent` | `subscription_payment_sent` | [subscriptions.py:210](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/subscriptions.py#L210) |

### BookingBot / BookingBot: админ и предоплаты (47)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/api/admin/dashboard` | `dashboard` | [admin.py:203](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L203) |
| `GET` | `/api/admin/bookings` | `list_bookings` | [admin.py:249](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L249) |
| `GET` | `/api/admin/businesses/{business_id}/services` | `list_services` | [admin.py:293](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L293) |
| `GET` | `/api/admin/businesses/{business_id}/employees` | `list_employees` | [admin.py:324](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L324) |
| `GET` | `/api/admin/analytics/{business_id}` | `analytics` | [admin.py:356](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L356) |
| `GET` | `/api/admin/analytics/{business_id}/overview` | `analytics_overview` | [admin.py:392](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L392) |
| `POST` | `/api/admin/bookings` | `create_manual_booking` | [admin.py:639](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L639) |
| `PATCH` | `/api/admin/bookings/{booking_id}/status` | `update_booking_status` | [admin.py:773](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L773) |
| `PATCH` | `/api/admin/bookings/{booking_id}/service` | `change_booking_service_route` | [admin.py:934](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L934) |
| `POST` | `/api/admin/bookings/{booking_id}/reschedule` | `admin_reschedule_booking` | [admin.py:978](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L978) |
| `GET` | `/api/admin/businesses` | `list_admin_businesses` | [admin.py:1149](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1149) |
| `POST` | `/api/admin/businesses` | `create_admin_business` | [admin.py:1173](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1173) |
| `POST` | `/api/admin/businesses/{business_id}/services` | `create_service` | [admin.py:1240](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1240) |
| `PUT` | `/api/admin/businesses/{business_id}/services/{service_id}` | `update_service` | [admin.py:1269](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1269) |
| `DELETE` | `/api/admin/businesses/{business_id}/services/{service_id}` | `delete_service` | [admin.py:1299](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1299) |
| `POST` | `/api/admin/businesses/{business_id}/employees` | `create_employee` | [admin.py:1324](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1324) |
| `PUT` | `/api/admin/businesses/{business_id}/employees/{employee_id}` | `update_employee` | [admin.py:1356](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1356) |
| `DELETE` | `/api/admin/businesses/{business_id}/employees/{employee_id}` | `delete_employee` | [admin.py:1386](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1386) |
| `POST` | `/api/admin/businesses/{business_id}/photo` | `upload_business_photo` | [admin.py:1411](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1411) |
| `DELETE` | `/api/admin/businesses/{business_id}/photo/{photo_index}` | `delete_business_photo` | [admin.py:1475](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1475) |
| `POST` | `/api/admin/businesses/{business_id}/employees/{employee_id}/photo` | `upload_employee_photo` | [admin.py:1511](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1511) |
| `PUT` | `/api/admin/businesses/{business_id}/employees/{employee_id}/primary-photo` | `set_employee_primary_photo` | [admin.py:1580](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1580) |
| `DELETE` | `/api/admin/businesses/{business_id}/employees/{employee_id}/photo/{photo_index}` | `delete_employee_photo` | [admin.py:1616](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1616) |
| `GET` | `/api/admin/businesses/{business_id}/detail` | `get_business_detail` | [admin.py:1659](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1659) |
| `PUT` | `/api/admin/businesses/{business_id}` | `update_business` | [admin.py:1682](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1682) |
| `POST` | `/api/admin/businesses/{business_id}/services/reorder` | `reorder_services` | [admin.py:1765](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1765) |
| `GET` | `/api/admin/businesses/{business_id}/employees/{employee_id}/services` | `get_employee_services` | [admin.py:1792](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1792) |
| `PUT` | `/api/admin/businesses/{business_id}/employees/{employee_id}/services` | `set_employee_services` | [admin.py:1819](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1819) |
| `GET` | `/api/admin/businesses/{business_id}/employees/{employee_id}/time-pricing` | `get_time_pricing` | [admin.py:1854](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1854) |
| `POST` | `/api/admin/businesses/{business_id}/employees/{employee_id}/time-pricing` | `add_time_pricing` | [admin.py:1884](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1884) |
| `DELETE` | `/api/admin/businesses/{business_id}/employees/{employee_id}/time-pricing/{pricing_id}` | `delete_time_pricing` | [admin.py:1925](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1925) |
| `POST` | `/api/admin/businesses/{business_id}/employees/{employee_id}/toggle-duty` | `toggle_duty` | [admin.py:1956](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1956) |
| `GET` | `/api/admin/businesses/{business_id}/employees/{employee_id}/duty-schedule` | `get_duty_schedule` | [admin.py:1987](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L1987) |
| `PUT` | `/api/admin/businesses/{business_id}/employees/{employee_id}/duty-schedule` | `set_duty_schedule` | [admin.py:2016](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L2016) |
| `GET` | `/api/admin/businesses/{business_id}/owners` | `list_owners` | [admin.py:2053](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L2053) |
| `POST` | `/api/admin/businesses/{business_id}/owners` | `add_owner` | [admin.py:2103](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L2103) |
| `DELETE` | `/api/admin/businesses/{business_id}/owners/{owner_id}` | `remove_owner` | [admin.py:2168](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L2168) |
| `GET` | `/api/admin/businesses/{business_id}/subscriptions` | `list_subscriptions` | [admin.py:2196](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L2196) |
| `POST` | `/api/admin/businesses/{business_id}/subscriptions` | `create_subscription` | [admin.py:2235](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L2235) |
| `PUT` | `/api/admin/businesses/{business_id}/subscriptions/{subscription_id}` | `update_subscription` | [admin.py:2286](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L2286) |
| `DELETE` | `/api/admin/businesses/{business_id}/subscriptions/{subscription_id}` | `delete_subscription` | [admin.py:2348](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L2348) |
| `GET` | `/api/admin/businesses/{business_id}/qr` | `generate_qr` | [admin.py:2376](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/admin.py#L2376) |
| `GET` | `/api/admin/prepayments/attention` | `attention` | [payments.py:20](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/payments.py#L20) |
| `GET` | `/api/admin/prepayments` | `inbox` | [payments.py:43](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/payments.py#L43) |
| `GET` | `/api/admin/bookings/{booking_id}/payment-details` | `details` | [payments.py:63](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/payments.py#L63) |
| `GET` | `/api/admin/receipts/{receipt_id}` | `download` | [payments.py:78](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/payments.py#L78) |
| `POST` | `/api/admin/bookings/{booking_id}/prepayment-review` | `review` | [payments.py:96](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/payments.py#L96) |

### BookingBot / BookingBot: веб-сервер и публичные страницы (48)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/` | `index_handler` | [server.py:3600](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3600) |
| `GET` | `/calendar` | `calendar_handler` | [server.py:3601](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3601) |
| `GET` | `/api/calendar` | `api_calendar_data` | [server.py:3602](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3602) |
| `GET` | `/api/booking/{booking_id}` | `api_booking_details` | [server.py:3603](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3603) |
| `POST` | `/api/booking/{booking_id}/status` | `api_update_booking_status` | [server.py:3604](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3604) |
| `GET` | `/api/day/{date}` | `api_day_bookings` | [server.py:3605](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3605) |
| `GET` | `/analytics` | `analytics_handler` | [server.py:3608](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3608) |
| `GET` | `/api/analytics/businesses` | `api_analytics_businesses` | [server.py:3609](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3609) |
| `GET` | `/api/analytics/data` | `api_analytics_data` | [server.py:3610](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3610) |
| `GET` | `/api/analytics/schedule` | `api_analytics_schedule` | [server.py:3611](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3611) |
| `GET` | `/login` | `login_handler` | [server.py:3614](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3614) |
| `GET` | `/auth/token/{token}` | `auth_token_handler` | [server.py:3615](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3615) |
| `POST` | `/api/landing/connect-request` | `api_landing_connect_request` | [server.py:3616](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3616) |
| `POST` | `/api/auth/login` | `api_auth_login` | [server.py:3617](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3617) |
| `POST` | `/api/auth/telegram` | `api_auth_telegram` | [server.py:3618](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3618) |
| `GET` | `/api/auth/me` | `api_auth_me` | [server.py:3619](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3619) |
| `POST` | `/api/auth/logout` | `api_auth_logout` | [server.py:3620](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3620) |
| `GET` | `/api/auth/check` | `api_auth_check` | [server.py:3621](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3621) |
| `POST` | `/api/admin/booking` | `api_admin_create_booking` | [server.py:3624](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3624) |
| `GET` | `/book` | `booking_handler` | [server.py:3627](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3627) |
| `GET` | `/api/client/businesses` | `api_client_businesses` | [server.py:3628](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3628) |
| `GET` | `/api/client/last-bookings` | `api_client_last_bookings` | [server.py:3629](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3629) |
| `GET` | `/api/client/photo` | `api_get_photo` | [server.py:3630](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3630) |
| `GET` | `/api/media/{file_id}` | `api_media_proxy` | [server.py:3631](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3631) |
| `GET` | `/api/client/business/{business_id}` | `api_client_business_details` | [server.py:3632](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3632) |
| `GET` | `/api/client/business/{business_id}/services` | `api_client_business_details` | [server.py:3633](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3633) |
| `GET` | `/api/client/business/{business_id}/slots` | `api_client_slots` | [server.py:3634](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3634) |
| `GET` | `/api/client/business/{business_id}/calculate-price` | `api_client_calculate_price` | [server.py:3635](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3635) |
| `POST` | `/api/client/booking` | `api_client_create_booking` | [server.py:3636](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3636) |
| `GET` | `/api/client/my-bookings` | `api_client_my_bookings` | [server.py:3639](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3639) |
| `POST` | `/api/client/booking/{booking_id}/cancel` | `api_client_cancel_booking` | [server.py:3640](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3640) |
| `POST` | `/api/client/booking/{booking_id}/reschedule` | `api_client_reschedule_booking` | [server.py:3641](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3641) |
| `POST` | `/api/client/booking/{booking_id}/prepayment-sent` | `api_client_prepayment_sent` | [server.py:3642](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3642) |
| `GET` | `/api/client/all-subscriptions` | `api_client_all_subscriptions` | [server.py:3643](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3643) |
| `GET` | `/api/client/my-subscriptions` | `api_client_my_subscriptions` | [server.py:3644](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3644) |
| `GET` | `/api/client/subscription/{subscription_id}` | `api_client_subscription_details` | [server.py:3645](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3645) |
| `POST` | `/api/client/subscription/{subscription_id}/buy` | `api_client_buy_subscription` | [server.py:3646](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3646) |
| `POST` | `/api/client/subscription/{client_sub_id}/payment-sent` | `api_client_subscription_payment_sent` | [server.py:3647](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3647) |
| `POST` | `/api/client/subscription/{client_sub_id}/upload-receipt` | `api_client_subscription_upload_receipt` | [server.py:3648](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3648) |
| `STATIC` | `/static` | `static_path` | [server.py:3651](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3651) |
| `GET` | `/blog/sitemap.xml` | `blog_sitemap_handler` | [server.py:3654](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3654) |
| `GET` | `/blog` | `blog_handler` | [server.py:3655](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3655) |
| `GET` | `/blog/{article_slug:[a-z0-9-]+}` | `blog_handler` | [server.py:3656](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3656) |
| `GET` | `/sitemap.xml` | `sitemap_handler` | [server.py:3660](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3660) |
| `GET` | `/robots.txt` | `robots_handler` | [server.py:3661](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3661) |
| `GET` | `/llms.txt` | `llms_txt_handler` | [server.py:3662](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3662) |
| `GET` | `/roadmap` | `serve_roadmap` | [server.py:3665](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3665) |
| `GET` | `/{slug:[a-z][a-z0-9-]*}` | `serve_niche_landing` | [server.py:3670](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/webapp/server.py#L3670) |

### BookingBot / BookingBot: кабинет мастера (4)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/api/master/me` | `master_me` | [master.py:134](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/master.py#L134) |
| `GET` | `/api/master/schedule` | `master_schedule` | [master.py:140](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/master.py#L140) |
| `POST` | `/api/master/bookings/{booking_id}/finish` | `master_finish_booking` | [master.py:238](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/master.py#L238) |
| `PATCH` | `/api/master/bookings/{booking_id}/service` | `master_change_service` | [master.py:255](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/master.py#L255) |

### BookingBot / BookingBot: клиентская запись и аренда (18)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/api/businesses` | `list_businesses` | [client.py:316](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/client.py#L316) |
| `GET` | `/api/businesses/{business_id}` | `get_business` | [client.py:337](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/client.py#L337) |
| `GET` | `/api/businesses/{business_id}/slots` | `get_slots` | [client.py:410](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/client.py#L410) |
| `GET` | `/api/businesses/{biz_id}/calculate-price` | `calculate_price` | [client.py:544](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/client.py#L544) |
| `POST` | `/api/bookings` | `create_booking` | [client.py:673](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/client.py#L673) |
| `GET` | `/api/my/bookings` | `my_bookings` | [client.py:974](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/client.py#L974) |
| `POST` | `/api/bookings/{booking_id}/cancel` | `cancel_booking` | [client.py:1000](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/client.py#L1000) |
| `POST` | `/api/bookings/{booking_id}/reschedule` | `reschedule_booking` | [client.py:1050](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/client.py#L1050) |
| `GET` | `/api/telegram-photo/{file_id:path}` | `get_telegram_photo` | [client.py:1244](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/client.py#L1244) |
| `POST` | `/api/bookings/{booking_id}/prepayment-sent` | `prepayment_sent` | [client.py:1422](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/client.py#L1422) |
| `POST` | `/api/bookings/{booking_id}/receipt` | `upload_receipt` | [client.py:1446](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/client.py#L1446) |
| `POST` | `/api/bookings/by-token/{token}/prepayment-sent` | `prepayment_sent_by_token` | [client.py:1471](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/client.py#L1471) |
| `POST` | `/api/bookings/by-token/{token}/receipt` | `upload_receipt_by_token` | [client.py:1493](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/client.py#L1493) |
| `GET` | `/api/bookings/by-token/{token}` | `get_booking_by_token` | [client.py:1535](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/client.py#L1535) |
| `GET` | `/api/businesses/{business_id}/rental/slots` | `rental_slots` | [rental.py:84](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/rental.py#L84) |
| `POST` | `/api/businesses/{business_id}/rental/bookings` | `create_rental_booking` | [rental.py:103](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/rental.py#L103) |
| `GET` | `/api/rental/bookings/{tracking_token}` | `rental_booking_status` | [rental.py:270](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/rental.py#L270) |
| `PATCH` | `/api/admin/rental/bookings/{booking_id}/quote` | `update_rental_quote` | [rental.py:281](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/rental.py#L281) |

### BookingBot / BookingBot: уведомления (3)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/api/notifications/vapid-public-key` | `vapid_public_key` | [notifications.py:60](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/notifications.py#L60) |
| `POST` | `/api/notifications/subscribe` | `subscribe` | [notifications.py:70](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/notifications.py#L70) |
| `DELETE` | `/api/notifications/subscribe` | `unsubscribe` | [notifications.py:136](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/notifications.py#L136) |

### BookingBot / BookingBot: AI-запись (15)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `POST` | `/api/ai/chat` | `ai_chat` | [ai.py:106](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/ai.py#L106) |
| `GET` | `/api/ai/history` | `ai_history` | [ai.py:130](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/ai.py#L130) |
| `POST` | `/api/ai/select-slot` | `ai_select_slot` | [ai.py:149](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/ai.py#L149) |
| `POST` | `/api/ai/handoff` | `ai_handoff` | [ai.py:187](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/ai.py#L187) |
| `POST` | `/api/ai/confirm` | `ai_confirm` | [ai.py:213](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/ai.py#L213) |
| `POST` | `/api/ai/reject` | `ai_reject` | [ai.py:251](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/ai.py#L251) |
| `POST` | `/api/ai/clear` | `ai_clear` | [ai.py:264](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/ai.py#L264) |
| `GET` | `/api/admin/ai/businesses/{business_id}/conversations` | `operator_inbox` | [ai.py:285](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/ai.py#L285) |
| `POST` | `/api/admin/ai/conversations/{conversation_id}/reply` | `operator_reply` | [ai.py:307](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/ai.py#L307) |
| `POST` | `/api/admin/ai/conversations/{conversation_id}/resume` | `operator_resume` | [ai.py:321](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/ai.py#L321) |
| `GET` | `/api/admin/ai/businesses/{business_id}/settings` | `owner_settings` | [ai.py:342](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/ai.py#L342) |
| `PUT` | `/api/admin/ai/businesses/{business_id}/settings` | `owner_update_settings` | [ai.py:348](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/ai.py#L348) |
| `GET` | `/api/platform/sales/businesses` | `platform_businesses` | [ai.py:370](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/ai.py#L370) |
| `GET` | `/api/platform/sales/businesses/{business_id}/settings` | `platform_settings` | [ai.py:377](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/ai.py#L377) |
| `PUT` | `/api/platform/sales/businesses/{business_id}/settings` | `platform_update_settings` | [ai.py:384](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/api/routes/ai.py#L384) |

### BookingBot / BookingBot: React-страницы (29)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `PAGE` | `/` | `React route` | [App.tsx:139](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L139) |
| `PAGE` | `/business/:id` | `React route` | [App.tsx:140](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L140) |
| `PAGE` | `/book/:businessId` | `React route` | [App.tsx:141](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L141) |
| `PAGE` | `/b/:token` | `React route` | [App.tsx:142](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L142) |
| `PAGE` | `/rental/b/:token` | `React route` | [App.tsx:143](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L143) |
| `PAGE` | `/my` | `React route` | [App.tsx:144](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L144) |
| `PAGE` | `/subscriptions` | `React route` | [App.tsx:152](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L152) |
| `PAGE` | `/chat` | `React route` | [App.tsx:153](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L153) |
| `PAGE` | `/legal/:docType/:businessId` | `React route` | [App.tsx:154](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L154) |
| `PAGE` | `/login` | `React route` | [App.tsx:155](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L155) |
| `PAGE` | `/login/yandex` | `React route` | [App.tsx:156](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L156) |
| `PAGE` | `/mail` | `React route` | [App.tsx:157](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L157) |
| `PAGE` | `/account` | `React route` | [App.tsx:158](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L158) |
| `PAGE` | `/master` | `React route` | [App.tsx:159](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L159) |
| `PAGE` | `/admin/prepayments` | `React route` | [App.tsx:160](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L160) |
| `PAGE` | `/book` | `React route` | [App.tsx:163](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L163) |
| `PAGE` | `/schedule` | `React route` | [App.tsx:164](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L164) |
| `PAGE` | `/analytics` | `React route` | [App.tsx:165](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L165) |
| `PAGE` | `/calendar` | `React route` | [App.tsx:166](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L166) |
| `PAGE` | `/admin/sales` | `React route` | [App.tsx:169](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L169) |
| `PAGE` | `/admin/sales/settings` | `React route` | [App.tsx:170](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L170) |
| `PAGE` | `/admin` | `React route` | [App.tsx:171](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L171) |
| `PAGE` | `/admin/schedule` | `React route` | [App.tsx:179](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L179) |
| `PAGE` | `/admin/services` | `React route` | [App.tsx:187](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L187) |
| `PAGE` | `/admin/employees` | `React route` | [App.tsx:195](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L195) |
| `PAGE` | `/admin/analytics` | `React route` | [App.tsx:203](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L203) |
| `PAGE` | `/admin/settings` | `React route` | [App.tsx:211](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L211) |
| `PAGE` | `/admin/subscriptions` | `React route` | [App.tsx:219](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L219) |
| `PAGE` | `*` | `React route` | [App.tsx:227](https://github.com/grunvald91/booking_bot/blob/eabd7b6b1321c8a3b147fcdb782241ba688cf47c/web/frontend/src/App.tsx#L227) |

### Growth OS / Аналитика и эффективность (2)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/studio/products/{product_id}/analytics` | `studio_analytics` | [content_studio.py:1947](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1947) |
| `GET` | `/studio/products/{product_id}/speed` | `studio_speed_page` | [content_studio.py:2122](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2122) |

### Growth OS / Внутренние модели и интеграции (3)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/studio/internal/model/{role}` | `model_for_role` | [internal_models.py:40](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/internal_models.py#L40) |
| `GET` | `/studio/internal/companies` | `companies` | [internal_models.py:56](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/internal_models.py#L56) |
| `GET` | `/studio/internal/companies/{product_id}/brief` | `brief` | [internal_models.py:114](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/internal_models.py#L114) |

### Growth OS / Главная и ИИ-диспетчер (7)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/studio/assets/dispatcher.css` | `dispatcher_css` | [dispatcher.py:164](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/dispatcher.py#L164) |
| `GET` | `/studio/assets/dispatcher.js` | `dispatcher_js` | [dispatcher.py:170](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/dispatcher.py#L170) |
| `GET` | `/studio/products/{product_id}/dispatch/state` | `dispatch_state` | [dispatcher.py:176](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/dispatcher.py#L176) |
| `POST` | `/studio/products/{product_id}/dispatch/messages` | `dispatch_message` | [dispatcher.py:183](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/dispatcher.py#L183) |
| `POST` | `/studio/products/{product_id}/dispatch/confirm` | `dispatch_confirm` | [dispatcher.py:199](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/dispatcher.py#L199) |
| `GET` | `/studio/products/{product_id}/dispatch/updates` | `dispatch_updates` | [dispatcher.py:215](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/dispatcher.py#L215) |
| `POST` | `/studio/products/{product_id}/dispatch/new` | `dispatch_new` | [dispatcher.py:230](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/dispatcher.py#L230) |

### Growth OS / Голосовой ввод (3)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `POST` | `/studio/voice/preview` | `preview_in_studio` | [voice.py:110](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/voice.py#L110) |
| `POST` | `/studio/voice/dictate` | `dictate_in_studio` | [voice.py:139](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/voice.py#L139) |
| `POST` | `/studio/voice/service/dictate` | `dictate_for_service` | [voice.py:151](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/voice.py#L151) |

### Growth OS / Задачи и очередь (4)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/studio/products/{product_id}/work` | `studio_work_page` | [content_studio.py:2135](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2135) |
| `GET` | `/studio/products/{product_id}/activity.json` | `studio_activity_json` | [content_studio.py:2149](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2149) |
| `GET` | `/studio/products/{product_id}/tasks/{task_id}` | `studio_task_page` | [content_studio.py:2179](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2179) |
| `POST` | `/studio/products/{product_id}/tasks/{task_id}/retry` | `studio_retry_task` | [content_studio.py:2217](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2217) |

### Growth OS / ИИ-команда, модели и промпты (17)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/studio/team` | `team_page` | [team_dashboard.py:246](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L246) |
| `GET` | `/studio/team/assets/team.css` | `team_css` | [team_dashboard.py:252](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L252) |
| `GET` | `/studio/team/assets/team.js` | `team_js` | [team_dashboard.py:258](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L258) |
| `GET` | `/api/admin/team` | `team_data` | [team_dashboard.py:264](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L264) |
| `GET` | `/studio/team/api` | `team_data` | [team_dashboard.py:265](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L265) |
| `POST` | `/api/admin/team/preview` | `team_preview` | [team_dashboard.py:282](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L282) |
| `POST` | `/studio/team/api/preview` | `team_preview` | [team_dashboard.py:283](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L283) |
| `PUT` | `/api/admin/team` | `team_update` | [team_dashboard.py:291](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L291) |
| `PUT` | `/studio/team/api` | `team_update` | [team_dashboard.py:292](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L292) |
| `GET` | `/api/admin/team/catalog` | `team_catalog` | [team_dashboard.py:313](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L313) |
| `GET` | `/studio/team/api/catalog` | `team_catalog` | [team_dashboard.py:314](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L314) |
| `GET` | `/api/admin/team/usage` | `team_usage` | [team_dashboard.py:335](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L335) |
| `GET` | `/studio/team/api/usage` | `team_usage` | [team_dashboard.py:336](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/team_dashboard.py#L336) |
| `GET` | `/studio/tune` | `tune_prompts` | [tune.py:133](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/tune.py#L133) |
| `POST` | `/studio/tune/prompts/{key}` | `tune_save` | [tune.py:194](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/tune.py#L194) |
| `GET` | `/studio/tune/work` | `tune_work` | [tune.py:218](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/tune.py#L218) |
| `GET` | `/studio/tune/work/{task_id}` | `tune_task` | [tune.py:264](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/tune.py#L264) |

### Growth OS / Комментарии и социальные каналы (8)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/studio/assets/comments.css` | `css` | [comments.py:66](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/comments.py#L66) |
| `GET` | `/studio/assets/comments.js` | `js` | [comments.py:71](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/comments.py#L71) |
| `GET` | `/studio/products/{product_id}/comments` | `page` | [comments.py:76](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/comments.py#L76) |
| `POST` | `/products/{product_id}/comments/settings` | `configure` | [comments.py:191](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/comments.py#L191) |
| `POST` | `/products/{product_id}/comments/connect` | `connect` | [comments.py:201](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/comments.py#L201) |
| `POST` | `/products/{product_id}/comments/channels/{platform}/{action}` | `channel_action` | [comments.py:211](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/comments.py#L211) |
| `POST` | `/products/{product_id}/comments/{mode}` | `create_comment` | [comments.py:234](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/comments.py#L234) |
| `POST` | `/products/{product_id}/comments/items/{comment_id}/{action}` | `comment_action` | [comments.py:252](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/comments.py#L252) |

### Growth OS / Компании и профиль (14)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `POST` | `/products/{product_id}/intake` | `start_intake` | [company_intake.py:58](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/company_intake.py#L58) |
| `GET` | `/products/{product_id}/intake/{session_id}` | `get_session` | [company_intake.py:66](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/company_intake.py#L66) |
| `POST` | `/products/{product_id}/intake/{session_id}/sources` | `add_source` | [company_intake.py:74](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/company_intake.py#L74) |
| `POST` | `/products/{product_id}/intake/{session_id}/draft` | `build_draft` | [company_intake.py:99](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/company_intake.py#L99) |
| `POST` | `/products/{product_id}/intake/{session_id}/unknown` | `unknown_answer` | [company_intake.py:115](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/company_intake.py#L115) |
| `POST` | `/products/{product_id}/intake/{session_id}/approve` | `approve_profile` | [company_intake.py:123](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/company_intake.py#L123) |
| `POST` | `/products/{product_id}/intake/{session_id}/cancel` | `cancel_intake` | [company_intake.py:131](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/company_intake.py#L131) |
| `GET` | `/products/{product_id}/company-profile` | `company_profile` | [company_profiles.py:29](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/company_profiles.py#L29) |
| `GET` | `/studio/new` | `new_company_form` | [content_studio_company.py:200](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio_company.py#L200) |
| `POST` | `/studio/new` | `new_company_submit` | [content_studio_company.py:246](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio_company.py#L246) |
| `GET` | `/studio/products/{product_id}/profile` | `company_profile` | [content_studio_company.py:484](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio_company.py#L484) |
| `GET` | `/studio/products/{product_id}/profile/edit` | `company_profile_edit` | [content_studio_company.py:505](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio_company.py#L505) |
| `GET` | `/studio/products/{product_id}/profile/delete` | `company_delete_page` | [content_studio_company.py:524](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio_company.py#L524) |
| `POST` | `/studio/products/{product_id}/profile/{action}` | `company_profile_action` | [content_studio_company.py:552](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio_company.py#L552) |

### Growth OS / Контент-план и календарь (6)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/studio/products/{product_id}/plan` | `studio_plan` | [content_studio.py:1851](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1851) |
| `GET` | `/studio/products/{product_id}/calendar` | `studio_calendar` | [content_studio.py:1863](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1863) |
| `POST` | `/studio/products/{product_id}/plans/{plan_id}/approve` | `studio_approve` | [content_studio.py:1957](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1957) |
| `POST` | `/studio/products/{product_id}/plans/{plan_id}/pause` | `studio_pause` | [content_studio.py:1969](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1969) |
| `POST` | `/studio/products/{product_id}/plans/{plan_id}/slots/{slot_id}/move` | `studio_slot_move` | [content_studio.py:2396](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2396) |
| `POST` | `/studio/products/{product_id}/plan/generate` | `studio_generate_plan` | [content_studio.py:2416](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2416) |

### Growth OS / Материалы и согласование (8)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/studio/products/{product_id}/materials` | `studio_materials` | [content_studio.py:1873](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1873) |
| `GET` | `/studio/products/{product_id}/materials/{artifact_id}` | `studio_material_detail` | [content_studio.py:1886](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1886) |
| `GET` | `/studio/products/{product_id}/packages/{package_key}` | `studio_package_detail` | [content_studio.py:1937](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1937) |
| `POST` | `/studio/products/{product_id}/plans/{plan_id}/packages/{package_key}/queue` | `studio_queue_package` | [content_studio.py:1981](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1981) |
| `POST` | `/studio/products/{product_id}/materials/{artifact_id}/edit` | `studio_manual_edit` | [content_studio.py:2063](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2063) |
| `GET` | `/studio/products/{product_id}/review` | `studio_review_page` | [content_studio.py:2241](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2241) |
| `POST` | `/studio/products/{product_id}/review/{artifact_id}/approve` | `studio_material_approve` | [content_studio.py:2260](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2260) |
| `POST` | `/studio/products/{product_id}/review/{artifact_id}/revise` | `studio_material_revise` | [content_studio.py:2275](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2275) |

### Growth OS / Простое видео (whiteboard) (14)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `POST` | `/products/{product_id}/whiteboard/jobs` | `api_create` | [whiteboard.py:128](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/whiteboard.py#L128) |
| `GET` | `/products/{product_id}/whiteboard/jobs` | `api_list` | [whiteboard.py:135](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/whiteboard.py#L135) |
| `GET` | `/studio/products/{product_id}/whiteboard/jobs.json` | `api_list` | [whiteboard.py:137](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/whiteboard.py#L137) |
| `GET` | `/products/{product_id}/whiteboard/jobs/{task_id}` | `api_job` | [whiteboard.py:145](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/whiteboard.py#L145) |
| `POST` | `/products/{product_id}/whiteboard/jobs/{task_id}/approve` | `api_approve` | [whiteboard.py:150](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/whiteboard.py#L150) |
| `POST` | `/products/{product_id}/whiteboard/jobs/{task_id}/cancel` | `api_cancel` | [whiteboard.py:157](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/whiteboard.py#L157) |
| `GET` | `/studio/products/{product_id}/whiteboard` | `studio_index` | [whiteboard.py:399](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/whiteboard.py#L399) |
| `POST` | `/studio/products/{product_id}/whiteboard` | `studio_create` | [whiteboard.py:416](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/whiteboard.py#L416) |
| `GET` | `/studio/products/{product_id}/whiteboard/{task_id}` | `studio_job` | [whiteboard.py:436](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/whiteboard.py#L436) |
| `POST` | `/studio/products/{product_id}/whiteboard/{task_id}/approve` | `studio_approve` | [whiteboard.py:443](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/whiteboard.py#L443) |
| `POST` | `/studio/products/{product_id}/whiteboard/{task_id}/cancel` | `studio_cancel` | [whiteboard.py:461](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/whiteboard.py#L461) |
| `POST` | `/studio/products/{product_id}/whiteboard/{task_id}/resume` | `studio_resume` | [whiteboard.py:468](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/whiteboard.py#L468) |
| `POST` | `/studio/products/{product_id}/whiteboard/{task_id}/repair` | `studio_repair` | [whiteboard.py:484](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/whiteboard.py#L484) |
| `GET` | `/studio/products/{product_id}/whiteboard/{task_id}/files/{filename}` | `studio_file` | [whiteboard.py:497](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/whiteboard.py#L497) |

### Growth OS / Прототип редизайна (1)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/studio/design-preview` | `design_preview` | [growth_design_preview.py:20](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/growth_design_preview.py#L20) |

### Growth OS / Публикация и каналы (5)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/studio/products/{product_id}/channels` | `studio_channels` | [content_studio.py:2290](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2290) |
| `POST` | `/studio/products/{product_id}/channels/connect` | `studio_connect_channel` | [content_studio.py:2331](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2331) |
| `POST` | `/studio/products/{product_id}/publishing/schedule` | `studio_schedule_post` | [content_studio.py:2350](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2350) |
| `POST` | `/studio/products/{product_id}/publishing/{publication_id}/reconcile` | `studio_reconcile_publication` | [content_studio.py:2368](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2368) |
| `POST` | `/studio/products/{product_id}/publishing/{publication_id}/cancel` | `studio_cancel_publication` | [content_studio.py:2383](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2383) |

### Growth OS / Публичный блог (2)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/public/blog/{product_slug}/articles` | `list_articles` | [public_blog.py:63](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/public_blog.py#L63) |
| `GET` | `/public/blog/{product_slug}/articles/{slug}` | `get_article` | [public_blog.py:72](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/public_blog.py#L72) |

### Growth OS / Разведка конкурентов (10)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `POST` | `/competitive-intelligence/runs` | `start_run` | [competitive_intelligence.py:33](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/competitive_intelligence.py#L33) |
| `GET` | `/competitive-intelligence/runs` | `list_runs` | [competitive_intelligence.py:41](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/competitive_intelligence.py#L41) |
| `GET` | `/competitive-intelligence/runs/{run_id}` | `get_run` | [competitive_intelligence.py:46](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/competitive_intelligence.py#L46) |
| `GET` | `/competitive-intelligence/runs/{run_id}/evidence` | `get_evidence` | [competitive_intelligence.py:51](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/competitive_intelligence.py#L51) |
| `GET` | `/competitive-intelligence/runs/{run_id}/report` | `get_report` | [competitive_intelligence.py:59](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/competitive_intelligence.py#L59) |
| `POST` | `/competitive-intelligence/runs/{run_id}/follow-up` | `follow_up` | [competitive_intelligence.py:68](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/competitive_intelligence.py#L68) |
| `POST` | `/competitive-intelligence/runs/{run_id}/export` | `export` | [competitive_intelligence.py:74](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/competitive_intelligence.py#L74) |
| `GET` | `/competitive-intelligence/runs/{run_id}/events` | `events` | [competitive_intelligence.py:85](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/competitive_intelligence.py#L85) |
| `GET` | `/intelligence` | `intelligence_page` | [competitive_intelligence.py:112](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/competitive_intelligence.py#L112) |
| `GET` | `/intelligence/assets/page.js` | `page_script` | [competitive_intelligence.py:121](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/competitive_intelligence.py#L121) |

### Growth OS / Системный мониторинг (2)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/health` | `health` | [health.py:6](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/health.py#L6) |
| `GET` | `/health/version` | `version` | [health.py:11](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/health.py#L11) |

### Growth OS / Создание контента (2)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/studio/products/{product_id}/create` | `studio_create_page` | [content_studio.py:2075](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2075) |
| `POST` | `/studio/products/{product_id}/create` | `studio_create_submit` | [content_studio.py:2092](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2092) |

### Growth OS / Учётные записи и доступ (11)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/studio/products/{product_id}/accounts` | `accounts_page` | [studio_accounts.py:143](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/studio_accounts.py#L143) |
| `POST` | `/studio/products/{product_id}/accounts` | `accounts_create` | [studio_accounts.py:151](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/studio_accounts.py#L151) |
| `POST` | `/studio/products/{product_id}/accounts/{username}/password` | `accounts_password` | [studio_accounts.py:172](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/studio_accounts.py#L172) |
| `POST` | `/studio/products/{product_id}/accounts/{username}/delete` | `accounts_delete` | [studio_accounts.py:185](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/studio_accounts.py#L185) |
| `POST` | `/studio/products/{product_id}/connections` | `connections_create` | [studio_accounts.py:198](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/studio_accounts.py#L198) |
| `POST` | `/studio/products/{product_id}/connections/{link_id}/delete` | `connections_delete` | [studio_accounts.py:212](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/studio_accounts.py#L212) |
| `GET` | `/studio/login` | `login_form` | [studio_login.py:97](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/studio_login.py#L97) |
| `POST` | `/studio/login` | `login_submit` | [studio_login.py:103](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/studio_login.py#L103) |
| `GET` | `/studio/logout` | `logout` | [studio_login.py:133](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/studio_login.py#L133) |
| `POST` | `/studio/sso/create` | `sso_create` | [studio_sso.py:144](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/studio_sso.py#L144) |
| `GET` | `/studio/sso` | `sso_landing` | [studio_sso.py:204](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/studio_sso.py#L204) |

### Growth OS / Эксперименты, команда и исследования (14)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/control/status` | `control_status` | [control.py:14](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/control.py#L14) |
| `GET` | `/products/{product_id}/control/status` | `product_control_status` | [control.py:19](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/control.py#L19) |
| `GET` | `/products/{product_id}/control/inbox` | `product_control_inbox` | [control.py:33](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/control.py#L33) |
| `POST` | `/products` | `create_product` | [products.py:31](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/products.py#L31) |
| `GET` | `/products` | `products` | [products.py:43](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/products.py#L43) |
| `GET` | `/products/{product_id}` | `product` | [products.py:51](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/products.py#L51) |
| `POST` | `/products/{product_id}/goals` | `create_goal` | [products.py:55](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/products.py#L55) |
| `POST` | `/products/{product_id}/experiments` | `create_experiment` | [products.py:66](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/products.py#L66) |
| `POST` | `/products/{product_id}/daily-growth-review` | `daily_growth_review` | [products.py:83](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/products.py#L83) |
| `POST` | `/products/{product_id}/experiments/from-content-plan` | `experiment_from_content_plan` | [products.py:89](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/products.py#L89) |
| `POST` | `/products/{product_id}/team-cycle` | `team_cycle` | [products.py:113](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/products.py#L113) |
| `POST` | `/products/{product_id}/articles` | `produce_article` | [products.py:127](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/products.py#L127) |
| `GET` | `/products/{product_id}/artifacts/{artifact_id}` | `artifact` | [products.py:142](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/products.py#L142) |
| `POST` | `/products/{product_id}/research-sources` | `add_research_sources` | [research_sources.py:15](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/research_sources.py#L15) |

### Growth OS / Studio: навигация и ресурсы (8)

| Метод | Маршрут | Обработчик | Исходник |
|---|---|---|---|
| `GET` | `/studio/assets/servgo.svg` | `studio_official_logo` | [content_studio.py:1789](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1789) |
| `GET` | `/studio/assets/studio.css` | `studio_css` | [content_studio.py:1797](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1797) |
| `GET` | `/studio/assets/studio.js` | `studio_js` | [content_studio.py:1804](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1804) |
| `GET` | `/studio` | `studio` | [content_studio.py:1811](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1811) |
| `GET` | `/studio/products/{product_id}` | `studio_product` | [content_studio.py:1826](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1826) |
| `GET` | `/studio/products/{product_id}/classic` | `studio_product_classic` | [content_studio.py:1840](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1840) |
| `GET` | `/studio/products/{product_id}/renders/{render_id}/{filename}` | `studio_rendered_asset` | [content_studio.py:1905](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L1905) |
| `POST` | `/studio/products/{product_id}/notifications/read-all` | `studio_notifications_read_all` | [content_studio.py:2165](https://github.com/grunvald91/Ai_growth_os/blob/design/growth-os-v4-review-20261030/app/api/content_studio.py#L2165) |

## Правила актуализации

После изменения маршрутов или функций обновлять одновременно CSV, JSON и матрицу бизнес-функций. Изменения авторизации, SMTP, оплаты и автопубликации требуют отдельного теста прав и запрета действий без подтверждения. Ни одно наличие маршрута здесь не отменяет серверной проверки.
