# ServGo — единый вход в Growth и возврат из Mail

**Дата:** 01.11.2026. **Статус:** код в отдельной ветке BookingBot,
gateway-флаги отключены, production не изменён этим этапом.

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

## Порядок запуска (пока НЕ выполнен)

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
