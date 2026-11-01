# ServGo PWA — этап 4A: Growth на одном origin (staging-подготовка)

**31.10.2026. Статус: код + шаблон nginx + CI, НЕ production.**

Это первый безопасный срез следующего этапа [общего PWA](https://github.com/grunvald91/booking_bot/blob/feat/servgo-unified-pwa-foundation-20261031/docs/unified-pwa-implementation-plan.md). Режим **выключен по умолчанию** и не меняет текущую отправку клиентов на `growth.servgo.ru`.

## Что уже сделано

- BookingBot выдаёт тот же **подписанный 60-секундный одноразовый SSO-билет**, но его назначение зависит от серверного флага `SERVGO_GROWTH_GATEWAY_ENABLED`. По умолчанию: `https://growth.servgo.ru/studio/sso`; при `true`: `https://servgo.ru/studio/sso`. Домен нельзя подменить пользовательским вводом или заголовком Host.
- Growth OS принимает POST от `https://servgo.ru` только при флаге `STUDIO_SERVGO_GATEWAY_ENABLED=true` **и только если само обращение действительно пришло на HTTPS-хост servgo.ru**. Основной старый адрес из `STUDIO_PUBLIC_ORIGIN` остаётся активным. Запрос на один хост с `Origin` второго запрещён.
- [Неразвёрнутый nginx include](../deploy/nginx/servgo-growth-gateway.inc.example) предлагает точечные `/studio`, `/studio/*`, `/intelligence`, отдельные пути разведки/комментариев; не перехватывает `/api/*` BookingBot. `/workspace/growth` — входной адрес, который пока перенаправляет **в пределах servgo.ru** на `/studio`.
- Автотесты проверяют прежнюю аудиторию и подписание SSO, ограничения прав, разные Origin/Host, недопустимые URL и синтаксис nginx (только на временном тестовом сервере).

## Реальная архитектура этого этапа

```
Пользователь в установленном ServGo PWA (https://servgo.ru)
    |
    | Нажал «Маркетинг», Backend BookingBot проверил право на business_id
    | POST /api/studio/ticket => {url:"https://servgo.ru/studio/sso?t=..."}
    v
Текущий TLS virtual host servgo.ru (nginx)
    |
    | GET /studio/sso?t=...     # same-origin, никакого нового PWA
    | /studio/*, строго перечисленные legacy Growth endpoints
    v
Growth ASGI на loopback 127.0.0.1:8091
    |
    | проверка подписи, TTL, jti, проверка прав в BookingBot
    | host-only Secure HttpOnly Growth cookie (НЕ cookie BookingBot)
    v
/studio/products/:uuid/... внутри того же origin и PWA
```

Примечание: клиентский PWA **не хранит SSO токен**, а получает временный URL и направляет браузер. Нельзя логировать значения `t=`, передавать их в аналитики, отдавать внешним Referer или кешировать такие страницы.

## Зависимости — прежде чем можно включить

1. **Сначала** успешно проверить и выпустить [BookingBot PR #102 — SW v3](https://github.com/grunvald91/booking_bot/pull/102). Пока старый service worker кэширует `/index.html` от произвольной навигации, проксирование приватного Growth в тот же origin небезопасно.
2. Сверить код и статус [BookingBot PR #103 — темы](https://github.com/grunvald91/booking_bot/pull/103) и [Growth OS PR #182 — стили](https://github.com/grunvald91/Ai_growth_os/pull/182). **Эти PR независимы** от этого gateway, их merge не делается автоматически.
3. Получить **реальную** текущую конфигурацию HTTPS `server_name servgo.ru` из сервера; `web/nginx.conf` в репозитории относится к `artur.grunvald1991.fvds.ru:80`, а `deploy/server-snapshot/20261018/nginx/growth-servgo.conf` устарел и описывает только поддомен Growth. Нельзя заменять production конфигурацию по старым снимкам.
4. Убедиться, что Growth API действительно слушает `127.0.0.1:8091` в этом окружении, а не другой порт/хост. Прокси-адрес задан **только как проверяемый предположительный target**.
5. Проверить отсутствие конфликтов `location = /studio`, `location ^~ /studio/`, `/intelligence`, `/products/:uuid/comments`, `/competitive-intelligence/runs` на `servgo.ru`. Если есть конфликт — пересмотреть карту, не добавлять второй одинаковый location.
6. Сделать `nginx -t`, тесты без авторизации, тесты SSO, права компаний, Origin/CSRF, CSP/стили/JS и проверку светлой/тёмной темы на Android PWA и десктопе.

## Порядок безопасного будущего включения (операторский чек-лист)

- [ ] Обновлённый SW v3 реально отдан с `https://servgo.ru/sw.js`, прежние навигации/почта/записи не попали в cache storage; push после апгрейда работает.
- [ ] Сделан backup **действующего** nginx TLS virtual host; проверено происхождение конфигурации и обратный прокси до Growth. Секреты не публиковать в CI/logs.
- [ ] Точечные locations добавлены к `server_name servgo.ru`. `nginx -t` успешен; reload только после одобрения и проверки.
- [ ] В Growth установлен `STUDIO_SERVGO_GATEWAY_ENABLED=true`; `STUDIO_PUBLIC_ORIGIN=https://growth.servgo.ru` оставлен для старых вкладок.
- [ ] Неавторизованный `GET https://servgo.ru/studio` ведёт к безопасной форме входа/401; никакой приватный HTML не возвращается.
- [ ] `POST` к защищённому Growth API с `Origin: https://evil.example` или с перекрёстным Origin/Host отклоняется 403; ожидаемый same-origin запрос проходит проверку CSRF.
- [ ] Получен билет из авторизованного BookingBot; проверен одноразовый вход, принадлежность бизнесу, изменение прав, доступ к /studio/assets, команда/голос, Reload/back, разрешения администратора и оператора.
- [ ] Старый `growth.servgo.ru` продолжает работать и принимать записи; reverse-proxy не пропустил `/api/*` BookingBot в Growth.
- [ ] И только после этого в BookingBot установлен `SERVGO_GROWTH_GATEWAY_ENABLED=true`.
- [ ] Проверено устройство: PWA → Кабинет → Маркетинг → обратно, **в том же standalone-окне и на одном origin** (специально Chrome Android, десктоп, iOS Safari).

## Минимальная проверка после включения

- `curl -I https://servgo.ru/workspace/growth` — 302 `Location: /studio`.
- `curl -I https://servgo.ru/studio` — отсутствие публичного доступа к приватному документу; конкретный статус зависит от текущей конфигурации авторизации.
- Запрос к `https://servgo.ru/api/...` должен продолжать обслуживаться **BookingBot**, не Growth.
- `curl` по `/studio/sso?t=...` с невалидным токеном должен отказывать; не использовать реальный билет в shell-history и CI/logs.
- Сессия под пользователем одной компании не даёт доступ к `/studio/products/<другая компания>`.
- Проверить `Cache-Control: no-store` и отсутствие кеша HTML со стороны service worker, logout и уведомлений.

## Rollback

1. Вернуть `SERVGO_GROWTH_GATEWAY_ENABLED=false` в BookingBot — новые SSO билеты идут на `growth.servgo.ru`.
2. Не удалять nginx locations, пока есть живые сессии по новому адресу; остановить новые входы и проверить последствия.
3. После проверки удалить/отключить только добавленные точечные nginx locations, выполнить `nginx -t` и reload по отдельному разрешению. Существующие данные и отдельный сервис Growth не изменяются.

**Важно:** это не завершение общего PWA. Для Mail потребуется отдельная работа с `/owner-mail/api/*`, одноразовым входом, Cookie/Origin и абсолютными путями на HTML-страницах. Пока Mail остаётся на `mail.servgo.ru`. Серверные операции рассылок/SMTP этим этапом не изменяются.

**Никаких изменений на сервере этим PR не выполняется.**
