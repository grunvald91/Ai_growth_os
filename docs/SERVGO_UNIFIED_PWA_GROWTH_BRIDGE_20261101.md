# ServGo Growth ↔ Mail — подготовка единого PWA

Дата: 2026-11-01. Ветка: `feature/servgo-pwa-growth-bridge-20261101`.
База: Growth main с восстановленной плавающей главной (#203).
**Стадия: кодовая подготовка. Никакие gateway-флаги на production не включаются.**

## Что сделано в этом срезе

- Growth V4 использует общие семантические токены ServGo Air и значение
  `servgo-theme` Light/Dark/System, как клиентская запись, админка и Mail.
- На едином origin `servgo.ru` настройки темы будут общими через localStorage;
  на старом `growth.servgo.ru` прежняя сессия и оформление сохраняются.
- `STUDIO_SERVGO_GATEWAY_ENABLED=false` по умолчанию. При явном включении
  Growth принимает POST с Origin `https://servgo.ru` исключительно при
  фактическом HTTPS Host `servgo.ru`. Межхостовые POST запрещены.
- Legacy `https://growth.servgo.ru` сохраняет отдельный вход и разрешённый Origin.
- Существующие бизнесовые операции и voice/dispatcher не изменяются.

## Для выпуска единого PWA требуется отдельно

1. Визуальный и браузерный тест Growth (включая плавающий композер, 320/390 px).
2. Аудит **фактического** nginx TLS-vhost: маршруты `/studio`, `/studio/*`
   и allowlist должны проксироваться в Growth, не перехватывая BookingBot `/api/*`.
3. Проверка работающего SW v4 и установленной PWA, SSO-билетов и серверных прав,
   host-only cookies, Origin/CSRF, CSP, logout и deep links.
4. Сравнить старый и новый Growth адреса; никакого редиректа или отключения
   `growth.servgo.ru`. Проверить сохранение `mail.servgo.ru`.
5. Отдельно принять Mail Air V2 (<https://github.com/grunvald91/booking_bot/pull/117>),
   затем тестировать клиент → админка → Growth → Mail → обратно в одной PWA.
   Тесты не должны отправлять настоящие письма/массовые рассылки.

Связанные планы: BookingBot `docs/unified-pwa-implementation-plan.md`,
`deploy/nginx/servgo-growth-gateway.inc.example`,
Growth #203, BookingBot #109 и #117.
