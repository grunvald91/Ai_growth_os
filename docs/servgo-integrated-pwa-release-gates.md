# ServGo — интеграционная ревизия и условия выпуска единого PWA

Состояние на 31.10.2026. **Интеграционная ветка, не production.**
**Текущие кандидаты:** [BookingBot #109](https://github.com/grunvald91/booking_bot/pull/109) и [Growth #186](https://github.com/grunvald91/Ai_growth_os/pull/186). Исходные #107/#108/#184 остаются историческими review-PR. Production по-прежнему SW v3; SW v4 и Gateway не развёрнуты.

## Финальная кодовая ревизия SEO + PWA (31.10.2026)

Этот пункт фиксирует **кодовую**, а не серверную готовность. В production
не вносились изменения в рамках этой ревизии; объединение #109 в main
запустит автоматический деплой frontend и требует отдельного решения.

| Ранее выявленный P1 | Статус в кандидатной ветке | Подтверждающий путь |
|---|---|---|
| Logout расписания восстанавливал общую cookie-сессию | Исправлено в коде: `clearLogin()` устанавливает общий tombstone, удаляет JWT, вызывает `POST /auth/logout`; backend удаляет cookie | `web/frontend/src/authUtils.ts`, `web/frontend/src/App.tsx`, `web/api/routes/auth.py` |
| SW мог кешировать личный HTML и выдавать его offline | Исправлено в коде: network-only private navigation, static offline shell, изолированные IndexedDB-снимки на 7 дней, очистка при logout; офлайн-правки запрещены | `web/frontend/public/sw.js`, `web/frontend/src/utils/offlineStore.ts`, `web/frontend/tests/sw-security.test.mjs`, `web/frontend/tests/offline-store.test.mjs` |
| Неудачная проверка предоплаты блокировала offline-расписание | Исправлено: при offline/error уведомление без блокирующей модалки; даже при новой предоплате окно можно свернуть, не отменяя проверку | `web/frontend/src/components/AdminPaymentAttention.tsx`, `web/frontend/tests/prepayment-modal.test.mjs` |
| Одновременные изменения статуса кампании могли конфликтовать | Исправлено в Growth #186: атомарный compare-and-swap по прежнему статусу, отказ в случае гонки | `Ai_growth_os/app/services/campaign_lifecycle.py`, `Ai_growth_os/tests/test_growth_campaign_lifecycle.py` |
| Backend-релиз не доставлял маршруты блога компаний | Подготовлен отдельный guarded API-only rollout с зависимостями; **сам backend пока не выпущен** | `scripts/servgo_unified_api_release.py`, `tests/test_servgo_unified_api_release.py` |

До последнего frontend-изменения **BookingBot PWA CI** был зелёным:
https://github.com/grunvald91/booking_bot/actions/runs/41236696139 .
Для Growth #186 зелёный полный CI:
https://github.com/grunvald91/Ai_growth_os/actions/runs/41236956656 .
Каждый новый head требует собственного подтверждённого CI, а не
переноса зелёного статуса с предыдущего SHA.

**Остаток, который невозможно закрыть без отдельного практического
выпуска:** backup и `nginx -t` действующего конфигурационного контекста,
доставка совместимых API/HTML/sitemap и Growth migrations, проверка
на настоящем сервере (включая оба старых домена), внешний HTTP/robots
и фактическая доступность `OAI-SearchBot`/Googlebot/YandexBot;
подключение реальных учётных данных Search Console и Вебмастера.
Без этих действий не утверждать, что страницы уже индексируются
или цитируются AI-поиском.

**Не менять флаги gateway, не запускать платные фабрики на demo,
не делать merge/deploy при одной только успешной CI.**

## Что вошло в проверяемую сборку

| Область | Исходный PR | Интеграционная версия |
|---|---|---|
| Безопасный PWA cache/offline | [#102](https://github.com/grunvald91/booking_bot/pull/102) | Перенесено в #107; SW v3, no-cache приватных навигаций |
| UI ServGo Air и Light/Dark/System | [#103](https://github.com/grunvald91/booking_bot/pull/103) | Включён React и Mail UI |
| Growth one-origin SSO | [#104](https://github.com/grunvald91/booking_bot/pull/104) | Флаг выключен, строгая проверка ticket URL |
| Mail PWA | [#105](https://github.com/grunvald91/booking_bot/pull/105) | Флаг выключен, `mail_owner`, path-scoped cookie |
| Growth theme + same-origin CSRF | [#182](https://github.com/grunvald91/Ai_growth_os/pull/182), [#183](https://github.com/grunvald91/Ai_growth_os/pull/183) | Комбинация #184, флаг выключен |

Изменения исходных PR собраны вручную в review-ветках из-за конфликта файла CI и зависимости #105 от #103.
Сводный CI проверяет **совместное** выполнение тестов SW, темы, Mail, SSO, nginx и сборки React.

## Выпуск блокируется до выполнения этих условий

### P0 — безопасность и правильный порядок

- [x] Все три feature flags выключены по умолчанию: `SERVGO_GROWTH_GATEWAY_ENABLED`, `STUDIO_SERVGO_GATEWAY_ENABLED`, `SERVGO_MAIL_GATEWAY_ENABLED`.
- [x] В SW v3 HTML-навигации всегда network-only. Новый offline не содержит данных аккаунта.
- [x] Mail `GET /api/logout` очищает cookie с правильным `Path=/api/owner-mail` на canonical origin, и `Path=/` на legacy host. Регрессионный тест добавлен.
- [x] Mail не выдаёт `mail_owner` другим бизнес-пользователям; SSO ticket одноразовый, привязан к origin.
- [x] Growth принимает POST второго origin только при флаге и реальном HTTPS Host `servgo.ru`.
- [x] **Провести live-аудит действующего production nginx:** после сохранённого backup и удаления дубля остаётся один `servgo.ru:443`; `nginx -t` успешен, предупреждений 0, upstream BookingBot `/api` → `127.0.0.1:8000`, Growth legacy → `127.0.0.1:8091`. Подтверждено [независимым SSH-аудитом после исправления, attempt 2](https://github.com/grunvald91/booking_bot/actions/runs/41183811915).
- [ ] **Проверить будущую конфигурацию gateway в реальном TLS-vhost:** `/studio` и `/workspace/growth` пока не активированы на `servgo.ru`. До включения сверить candidate nginx include с оставшимся блоком, сертификатом, proxy headers, API routes и резервной копией; проверить SSO на staging.
- [ ] **Проверить точечный include** `deploy/nginx/servgo-growth-gateway.inc.example` именно в контексте действующего TLS server. `nginx -t` в CI проверяет только изолированный шаблон.
- [ ] **Сохранить `growth.servgo.ru` без срока отключения**, даже после появления `servgo.ru/studio`: никаких принудительных редиректов/смены URL, прежний вход, отдельная сессия, bookmarks, deep links к проектам/задачам, GET/POST/CSRF, роль/tenant isolation и данные должны оставаться доступными. Это release-blocking требование до отдельного решения владельца.
- [ ] Сохранить безопасный доступ к `mail.servgo.ru` на время миграции; изменения Mail не должны нарушить старую почтовую ссылку.
- [ ] **Двухдоменный regression E2E до и после gateway rollout**: проверить `growth.servgo.ru/studio` и вложенные пути как с активной legacy-сессией, так и после новой авторизации; параллельно проверить новый `servgo.ru/studio`, отсутствие cross-host CSRF и отсутствие redirect со старого домена.
- [ ] Выполнить staging E2E с валидным входом и разными ролями: 401/403, чужая компания, logout, CSRF, replay, referer, cookies и no-store.
- [ ] Проверить точность SSO-разрешений и сроков жизни билетов на реальной БД/бекенде (не в CI с фейковыми пользователями).

### P0 — автоматический деплой BookingBot при merge

**Не сливать #107 в `main` до подготовки выпуска.** В [`.github/workflows/deploy.yml`](https://github.com/grunvald91/booking_bot/blob/main/.github/workflows/deploy.yml) задан `on.push.branches: main` + `web/frontend/**`; следовательно, merge #107 запускает автоматическую загрузку frontend на production **до** отдельного развёртывания API и проверки nginx. Для этого релиза требуется заранее согласованный двухрепозиторный rollout и откат.

Growth OS [`deploy-growth.yml`](https://github.com/grunvald91/Ai_growth_os/blob/main/.github/workflows/deploy-growth.yml) выполняется вручную с отдельными gate.

### P1 — визуальная и функциональная приёмка

- [ ] Android Chrome: установленная одна ServGo PWA открывает клиент → admin → Growth → Mail → обратно **в одном standalone-окне**.
- [ ] iOS Safari и desktop PWA: проверить link capture, различия OS.
- [ ] Light/Dark/System: один выбор отображается без white flash во всех четырёх рабочих областях.
- [ ] Референс ServGo Air на светлой теме: главный клиентский путь, карточка заведения, выбор услуги должны остаться визуально близкими к исходным скриншотам.
- [ ] Growth Home остаётся пустой до команды; исправлены отдельные визуальные проблемы на экране ИИ-команды/CSP и переполнение 320px.
- [ ] Переходы refresh, back/forward, deep links, режим offline, уведомления и возвращение из почты не открывают лишнюю вкладку браузера.
- [ ] Mail: поиск компании, адресатов, черновики, письма и кампании работают, но массовая реальная рассылка **не используется** для тестов.
- [ ] Проверка функционального паритета 61 сценария и подтверждённых ролей.

## Согласованная офлайн-функциональность BookingBot (31.10.2026)

- [x] Владелец **разрешил локальное хранение полноценного расписания**, включая имена и телефоны клиентов, услуги и комментарии, для пользователей с существующими правами. Не трактовать SW v3 network-only как постоянный запрет на локальные рабочие данные.
- [ ] Спроектировать и проверить offline app shell + IndexedDB-слой данных: ранее загруженное расписание, записи клиентов и данные мастера показываются при холодном запуске без сети; указать последнее успешное обновление.
- [ ] Проверить границы существующих бизнесов/ролей, синхронизацию и невозможность показать данные **другого** аккаунта при переключении, не вводя несогласованных ограничений на данные текущего пользователя.
- [ ] На физическом Android/iOS проверить отсутствие сети, закрытие и запуск PWA, отображение телефонов и комментариев, восстановление сети и повторное обновление.
- [x] Владелец утвердил: 7 суток, очистка при logout, офлайн только чтение, без очереди изменений. Не согласованы дополнительные PIN/шифрование, Mail/Growth офлайн и другие ограничения — не вводить их самостоятельно.
- [ ] Удалить/скорректировать после реализации устаревшую формулировку в `offline.html` о том, что записи не сохраняются для офлайн-просмотра. До внедрения она отражает текущее состояние, а не желаемую политику.

## Release record: SW v3 production, 31.10.2026

- [x] Отдельный [PR #102](https://github.com/grunvald91/booking_bot/pull/102) слит в main, коммит [fe6777b](https://github.com/grunvald91/booking_bot/commit/fe6777bf5238559ead8e660157d570dca277fed8); остальные интеграционные feature flags и маршруты не включались.
- [x] [Автодеплой фронтенда](https://github.com/grunvald91/booking_bot/actions/runs/41186280994) завершён успешно.
- [x] [Read-only smoke до/после](https://github.com/grunvald91/booking_bot/actions/runs/41186243838): после выпуска HTTPS `/sw.js` — **v3**, совпадает с серверным dist, `/offline.html` доступен; `/api/health`, manifest, основной домен, старые домены Growth/Mail отвечают прежними статусами; nginx warnings 0.
- [ ] Проверка существующего установленного PWA на физическом Android/iOS: автоматический update SW v2→v3, очистка старого CacheStorage, оффлайн-восстановление, push click, logout и запись клиента. Одних CI/HTTP-проверок для этой галочки недостаточно.
- [ ] После device-проверки — подготовить gateway-срез и staging E2E, сохранив `growth.servgo.ru` постоянно действующим адресом без редиректа и без сброса старых сессий.

## Точка продолжения 31.10.2026

Независимый повторный аудит nginx завершён, поэтому исправление дубля повторять **не нужно**. Следующий невыполненный шаг — отдельно выпустить SW v3 после проверки [PR #102](https://github.com/grunvald91/booking_bot/pull/102), не включая gateway. Контрольная последовательность и ограничения обновлены в [живом плане](unified-pwa-implementation-plan.md). Никакого merge в main/переключения production в рамках этого обновления документации.

## Операторский порядок staged release (не выполнен)

1. Подтвердить релизный план с rollback и завершить визуальное ревью.
2. Развернуть и проверить SW v3 в BookingBot **до** проксирования приватных страниц.
3. Подготовить nginx vhost, выполнить `nginx -t`, включить только узкие пути Growth, проверить upstream; не трогать BookingBot `/api`.
4. Развернуть Growth OS с отключённым флагом gateway; выполнить smoke tests legacy сайта и API.
5. Включить `STUDIO_SERVGO_GATEWAY_ENABLED` на Growth; проверить same-origin POST и старую сессию.
6. Развернуть Mail/BookingBot API с отключённым флагом; проверить старый mail.
7. Включить `SERVGO_MAIL_GATEWAY_ENABLED` и проверить one-time email ticket, owner role и cookie.
8. Только последним включить `SERVGO_GROWTH_GATEWAY_ENABLED` в BookingBot; проверить live SSO и PWA навигацию.
9. Выполнить Android/iOS/desktop E2E. При ошибках — сначала выключить **issuer flags** в BookingBot, затем после истечения/завершения новых сессий откатить gateway и nginx.

Текущий git-статус: **новые флаги выключены; main и production не переключены**. Проверки CI не эквивалентны тесту установленного приложения на реальном сервере.


## Повторная ревизия пяти P1 перед объединением (31.10.2026)

Эта секция описывает **код и CI review-ветки**, не фактический production.

1. **Выход и cookie-сессия.** `web/frontend/src/authUtils.ts`
   устанавливает `servgoExplicitLogout` в localStorage/sessionStorage,
   вызывает серверный `POST /auth/logout`, затем удаляет офлайн-снимки.
   `web/api/routes/auth.py` удаляет HttpOnly-cookie с тем же path/domain.
   `App.tsx` не выполняет повторный silent login, когда установлен флаг
   явного выхода. Возврат offline после безуспешного сетевого logout
   блокируется локально, но удаление cookie на сервере требует соединения.
2. **Обновление Service Worker.** `web/frontend/public/sw.js`
   использует временное поколение кеша и сохраняет предыдущее успешное.
   `web/frontend/tests/sw-security.test.mjs` содержит тест падения при
   precache и сохранения рабочего app shell.
3. **Предоплаты и офлайн-расписание.**
   `web/frontend/src/components/AdminPaymentAttention.tsx` при
   отсутствии сети очищает устаревший платежный контекст; сбой запроса
   выводится небольшим уведомлением, а не блокирующей модалкой.
4. **Сохранение уже согласованной кампании.**
   `Growth app/api/growth_campaigns.py` выполняет SQL UPDATE только
   при `status=draft`, с проверкой `rowcount==1`.
   Если другая транзакция успела перевести статус, возвращается HTTP 409.
5. **Полный релиз backend блога.** `scripts/servgo_unified_api_release.py`
   включает `web/api/routes/seo_pages.py`, `seo_reporting.py`,
   `seo_referrals.py` и зависимые модули. Workflow
   `servgo-unified-api-release.yml` по умолчанию запускается в режиме
   read-only; реальные записи требуют отдельного `workflow_dispatch`
   с `apply=true`.

Это конкретные исправления по пяти пунктам аудита, а **не снятие всех
релизных ограничений**. Для общего выпуска ещё нужны миграции Growth
`0011_seo_factory_opt_in.py` и Booking
`migrations/add_seo_booking_attributions.sql`, настройка nginx rewrite
`deploy/nginx/servgo-company-blog.conf`, секрет атрибуции, настройка
Growth public blog upstream и проверка действующих HTTPS-путей.
`growth.servgo.ru` и `mail.servgo.ru` сохраняются без редиректа.
Автоматическое объединение PR или включение gateway-флагов здесь не
выполнялось.
