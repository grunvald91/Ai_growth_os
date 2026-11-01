# ServGo — единый вход в Growth и возврат из Mail

**Дата:** 01.11.2026. **Статус:** backend и frontend этапа выпущены,
Growth-bridge выпущен; canonical nginx gateway **не активирован**.
Флаги `SERVGO_GROWTH_GATEWAY_ENABLED`, `SERVGO_MAIL_GATEWAY_ENABLED`,
`STUDIO_SERVGO_GATEWAY_ENABLED` оставлены выключенными.
Полноценный same-origin SSO/E2E на реальных ролях ещё не подтверждён.


## Фактический выпуск совместимых компонентов (01.11.2026)

Все ссылки ведут к конкретным GitHub Actions run. Production-действия
выполнены только на соответствующих целевых сервисах, без включения
межсервисного шлюза.

| Этап | Подтверждение | Факт |
|---|---|---|
| Совмещённый кандидат | [PR #119](https://github.com/grunvald91/booking_bot/pull/119), [CI](https://github.com/grunvald91/booking_bot/actions/runs/41317979607) | Содержимое #117 и #118 проверено вместе, объединено в BookingBot main `851e2eee891b28b026fb1ca72037470f620c44bf` |
| Серверные контрольные версии и SSO | [preflight](https://github.com/grunvald91/booking_bot/actions/runs/41317945124) | Все 16 файлов backend допускали безопасное обновление, оба пары SSO-секретов `MATCH` |
| BookingBot API | [release](https://github.com/grunvald91/booking_bot/actions/runs/41318261158) | Обновлены 16 файлов; `api_release_ok=true`, service active, API HTTP 200; backup `/opt/booking_bot/.servgo-pwa-api-releases/1793544706`, откат не потребовался |
| Growth Air + canonical origin guard | [PR #205](https://github.com/grunvald91/Ai_growth_os/pull/205), [deploy](https://github.com/grunvald91/Ai_growth_os/actions/runs/41318779547) | Growth main `1abb79287b825a47617f58559e26f0ddac8f91a9`, `DEPLOY_OK`, HTTP 200; автошлюз выключен |
| Canonical nginx candidate | [syntax + backup](https://github.com/grunvald91/booking_bot/actions/runs/41318887929) | 7 точечных locations проверены через `nginx -t` в копии фактической TLS-конфигурации; оригинал не изменён |
| BookingBot React PWA | [frontend release](https://github.com/grunvald91/booking_bot/actions/runs/41319068062) | `frontend_release_ok=true`, `backend_ready=true`, 37 assets проверены, SHA `851e2eee` |

**Откат nginx:** сохранена проверенная копия активного
`/etc/nginx/sites-enabled/webapp` по адресу
`/opt/booking_bot/.servgo-gateway-backups/servgo-443-pre-gateway-20261101T145932Z.conf`.
`ROLLBACK_BACKUP_SHA256_MATCH=YES`, `PRODUCTION_NGINX_UNCHANGED=YES`.
Рабочий nginx **не перезагружался** в этом этапе; backup только подготовлен.

**После API-релиза** проверены: `servgo.ru/api/health` 200,
`growth.servgo.ru/studio` 401 (без сессии),
`mail.servgo.ru/api/status` 401 (без сессии),
`servgo.ru/api/owner-mail/workspace` 404 (флаг выключен).
Эти анонимные пробы не равны успешной пользовательской авторизации.

### Оставшийся gate для фактического объединения PWA

1. Проверка работающей в браузере версии Mail V2 и Growth Air на телефоне,
   включая тему и плавную главную. Тестовые письма — только mock.
2. Нужен **положительный** тест SSO для реально авторизованного владельца и
   администратора, отрицательный для отсутствия роли и чужого бизнеса;
   проверка replay, CSRF/Origin, logout, cookies и session persistence.
   Сами совпадающие секреты и unit-тесты не заменяют этот тест.
3. Только после этого установить проверенный nginx include в реальном
   TLS-vhost с атомарным откатом и выполнить `nginx -t` + безопасный reload,
   smoke GET/POST и MIME.
4. Включить принимающий Growth gateway, проверить новую origin-ветку,
   затем отдельно Mail (`mail_owner`) и только последним BookingBot issuer.
   Старые хосты оставить в работе без редиректа.
5. Физические Android/iOS/desktop standalone проверки, 7-дневное offline
   расписание (только чтение), logout, back/forward/deep links и отсутствие
   приватного кэша Mail/Growth.

## Результат read-only аудита действующего сервера

Отчёт: https://github.com/grunvald91/booking_bot/actions/runs/41314587890

- `nginx -t` — OK; на каждом из `servgo.ru`, `growth.servgo.ru`,
  `mail.servgo.ru` ровно один TLS server.
- `growth.servgo.ru/studio` — 401 без сессии; `mail.servgo.ru/api/status`
  — 401; `growth` loopback 8091/health — 200.
- У `servgo.ru` отсутствуют отдельные location `/studio` и `/studio/*`.
  `servgo.ru/studio` — 200, но это **не подтверждает работу Growth**:
  на сервере нет правила проксирования; вероятен fallback на React.
  Проверять CSS не только по HTTP 200, но и по Content-Type.
- `servgo.ru/api/owner-mail/workspace` — 404: canonical Mail ещё не активирован.
- Это проверка **анонимных** GET и nginx. Реальный пользовательский SSO,
  роли и сохранение сессий на новом origin пока не подтверждены.

## Выполнено в ветке

- Новый экран React `/workspace/growth` внутри основной PWA: список бизнесов
  с сервера, по нажатию — одноразовый `/studio/ticket` (проверка прав
  осуществляется на backend). Никакого прямого перехода без билета.
- Адрес билета ограничен двумя доверенными HTTPS origin
  `servgo.ru` и `growth.servgo.ru`, путём `/studio/sso`, параметром `t`.
  На старом origin маршрут не меняется, пока флаг отключён.
- Область Mail на том же origin получает текстовые переходы к ServGo и
  экрану выбора бизнеса, не отдельную фиксированную плашку внизу,
  перекрывающую мобильную навигацию.
- Исправлен шаблон nginx: он больше не перехватывает
  `/workspace/growth` (SPA должна сама выбрать бизнес и получить билет).
  Точечные `/studio/*` upstream locations сохранены.
- Права `mail_owner` не расширяются, почтовые API и отправка писем не меняются.
  Обновлены регрессионные тесты и существующий PWA CI без отдельного job.

## Исторический план запуска (частично выполнен; актуальные статусы выше)

1. Принимать Mail Air V2 #117 и Growth bridge #205 как отдельные проверенные
   срезы. Прежде чем объединять, проверить их с новым экраном и CSS.
2. Выпустить BookingBot API с `mail_pwa` маршрутами и корректным
   `/studio/ticket`; сохранить старые домены и выключенные флаги.
3. Взять backup действующего nginx и проверить реальный TLS-vhost,
   сертификаты, доступность loopback 8091; собрать **candidate** include
   и выполнить `nginx -t` в реальном контексте, прежде чем делать reload.
4. Проверить сессии и права на staging: владелец A и B, пользователь без
   `mail_owner`, администратор, expired/replayed SSO-ticket, GET/POST Origin,
   CSRF, host-only cookies, logout и deep link.
5. Активировать `STUDIO_SERVGO_GATEWAY_ENABLED` и
   `SERVGO_GROWTH_GATEWAY_ENABLED` только когда оба приложения и nginx
   маршрутизируют `/studio`. Mail `SERVGO_MAIL_GATEWAY_ENABLED` включать
   отдельно после проверки API, темы и `mail_owner`.
6. После выпуска проверить статусы и **MIME**:
   `servgo.ru/studio/assets/growth-live.css` не должен возвращать HTML 200;
   `growth.servgo.ru/studio` должен сохранить прежний защищённый вход.
   Тесты не делают реальных писем, публикаций и затратных AI-запросов.
7. Проверить установленную PWA Android Chrome, iOS Safari и десктоп:
   клиент → админка → Growth → Mail → Growth → админка, Light/Dark/System,
   возвраты, вход после logout, сети online/offline; приватные данные
   Mail/Growth не кешировать. При ошибке — откат flags/include по backup.

**Блокер на 01.11:** отдельный nginx location /studio на `servgo.ru`
отсутствует. Нельзя делать вывод о готовности unified PWA лишь по HTTP 200
на неизвестном SPA-маршруте.
