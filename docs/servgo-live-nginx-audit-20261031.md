# ServGo — проверка настоящего nginx на сервере (31.10.2026)

**Источник:** [разовый SSH-аудит GitHub Actions](https://github.com/grunvald91/booking_bot/actions/runs/41183496701), 08:10 UTC. Только чтение: без перезагрузки nginx, изменения конфигурации, секретов, БД, сервисов и деплоя.

## Что подтвердил сервер

- Nginx active; `nginx -t` и `nginx -T` успешно выполнены. Слепок текущей **файловой конфигурации** nginx SHA256: `d0973ed14aec6e5f73ad6f360d86a318a4bf8f2633fced38e7fc73f92aa34964`.
- `booking-web-api`, `booking-bot` — active; среди контейнеров обнаружен `ai-growth-os-api-1`.
- `servgo.ru` уже маршрутизирует `/api/` на `127.0.0.1:8000`. `/api/legacy/` и `/api/media/` идут на `8080`, `/dating/` — на `8081`. Есть отдельные правила `/assets/`, `/sw.js`, `/manifest.webmanifest`.
- `growth.servgo.ru:443` обслуживает `/studio` через `127.0.0.1:8091` с передачей Host и X-Forwarded-Proto; также `/public/blog/`, отдельная разведка и комментарии.
- **Нет соответствующего `/studio` или `/studio/` под `servgo.ru:443`** — PWA ещё не обслуживает Growth на своём origin.
- `mail.servgo.ru:443` существует как отдельный виртуальный хост. Отсутствие его `location` в сводке не означает отсутствия обработки внутри server-level directives.

## Обнаружена дублирующая декларация servgo.ru:443

`nginx -T` показывает **два server-блока** с `server_name servgo.ru` и `listen 443`. `nginx -t` возвращает 0, но это не подтверждает отсутствие предупреждения `conflicting server name` — stderr намеренно не выводился, чтобы не показать секреты.


**Уточнено отдельной read-only проверкой 08:15 UTC:** [лог GitHub Actions #41183811915](https://github.com/grunvald91/booking_bot/actions/runs/41183811915).
- `nginx -T` / `nginx -t` вернули успех, но nginx сообщил **8 предупреждений о конфликтующих server_name** (общий счёт по всем доменам), включая **конфликт для `servgo.ru:443`**.
- TLS-блок №1: `/etc/nginx/sites-enabled/webapp`, `listen 443 ssl`, содержит `location = /sw.js` и **не содержит** `/studio`.
- TLS-блок №2: `/etc/nginx/sites-enabled/webapp.bak_auth3_`, `listen 443 ssl`, **не содержит** `location = /sw.js` и **не содержит** `/studio`.
- С большой вероятностью резервная копия ошибочно оставлена в каталоге активных nginx include; важно **не удалять её без проверки остальных server_name и резервной копии действующей конфигурации**. Конкретный конфиг **на диске** установлен, однако текущий live worker следует проверять HTTP-ответом и после reload.
- План безопасного исправления: проверить, какие другие серверные блоки/доменны присутствуют в `webapp.bak_auth3_`; переместить/отключить только лишнюю резервную конфигурацию из `sites-enabled` (не уничтожая файл), выполнить `nginx -t` и проверку warnings **до** reload; сохранить резервный путь для rollback. **На сервере пока ничего не перемещалось.**

**Блокер:** до переноса Growth установить, какие именно конфигурационные файлы содержат два блока, их фактический IP:port и какой блок обрабатывает запросы. Иначе можно добавить `location` в игнорируемый nginx server. Проверить предупреждения, бэкап, подготовить точечный diff действующего файла, `nginx -t`, безопасный reload и smoke test. Не перехватывать существующий BookingBot `/api/*`.

Официально: [nginx request processing](https://nginx.org/en/docs/http/request_processing.html), [server_name](https://nginx.org/en/docs/http/server_names.html).

## Действительно ли исходники на сервере синхронизированы с GitHub?

Да, **для четырёх проверенных файлов** на момент аудита. SHA256 содержимого главной ветки BookingBot (коммит `6a9cf3279386b5460fa67122233688291ab18272`) совпал побайтово с SHA256 фактических production-файлов:

| Main GitHub → production | Итог |
|---|---|
| `web/api/main.py` → `/opt/booking_bot/web/api/main.py` | Совпадает |
| `web/api/routes/owner_mail.py` → одноимённый файл | Совпадает |
| `web/api/routes/studio_sso.py` → одноимённый файл | Совпадает |
| `web/frontend/public/sw.js` → `web/frontend/dist/sw.js` | Совпадает |
| `web/frontend/dist/index.html` на сервере | Хеш получен, но нет точной сборки из GitHub для сравнения |

Это выборочная проверка, **не доказательство синхронизации всех исходников, контейнеров, nginx и runtime**. Неслитые изменения из [BookingBot #107](https://github.com/grunvald91/booking_bot/pull/107) и [Growth #184](https://github.com/grunvald91/Ai_growth_os/pull/184) в `main` и на сервер ещё не попадали.

[BookingBot deploy.yml](https://github.com/grunvald91/booking_bot/blob/main/.github/workflows/deploy.yml) переносит файлы frontend/webapp и **не обновляет /etc/nginx**; у Growth [другой механизм деплоя](https://github.com/grunvald91/Ai_growth_os/blob/main/.github/workflows/deploy-growth.yml).

## Следующие действия

1. [x] Получить живую карту nginx маршрутов, upstream портов и проверенные SHA256.
2. [ ] Уточнить конфликт двух TLS-блоков `servgo.ru`, активные имена файлов и предупреждения nginx; проверить, какой server-block действительно обслуживает запросы.
3. [ ] Сверить конфигурацию на диске с реально применённой; сделать точечный diff для действующего TLS-vhost (без вывода паролей).
4. [ ] Включить шлюз Growth **только после** выпуска безопасного SW v3 и проверки внутренних путей, Origin/CSRF, SSO/роль и обратной совместимости; реальный сервер пока не изменять.
5. [ ] Android/iOS/desktop PWA — переходы клиент → admin → Growth → Mail → назад, единый light/dark/system и правильный logout; затем production rollout с rollback.

**Итог:** GitHub-код выборочно синхронизирован, но nginx управляется отдельно и сейчас не реализует основное требование «единый origin для Growth».

## Исправлено на production: 31.10.2026, 08:23 UTC

**Точечное исправление выполнено.** [Журнал GitHub Actions](https://github.com/grunvald91/booking_bot/actions/runs/41184386213).

Скрипт проверил, что все 4 server-блока в `webapp.bak_auth3_` имеют эквивалентные привязки к именам и портам в других включённых конфигурациях, а основной `webapp` содержит HTTPS ServGo и `/sw.js`. Исходный SHA256 `nginx -T` совпал с ранее проверенным. Затем резервный конфиг атомарно перенесён из `sites-enabled` в `/etc/nginx/disabled-site-backups/webapp.bak_auth3_.20261031T082320Z`, не удалён.

- До: 8 предупреждений `conflicting server name`; после `nginx -t`: **0 предупреждений**.
- `systemctl reload nginx` успешен; сервер активен.
- Шесть локальных HTTPS-smoke checks **не изменили статусы**: `servgo.ru/` 200, `/sw.js` 200, `/manifest.webmanifest` 200, `/api/health` 200, `growth.servgo.ru/studio` 401 без сессии, `mail.servgo.ru/` 200.
- `rollback_needed=no`, `repair_result=success`.
- Файл `/etc/nginx/sites-enabled/webapp` и бизнес-код не изменялись. Добавление `servgo.ru/studio` НЕ выполнялось.

**Особенность аудита:** GitHub Actions отметил job как `failure` из-за ошибки обёртки скрипта после успешного завершения: `sys.exit(0)` был перехвачен `except BaseException`, что ложно выставило `repair_result=not_applied`. Этот дефект исправлен в [операционной ветке](https://github.com/grunvald91/booking_bot/blob/ops/servgo-nginx-duplicate-repair-20261031/scripts/ops_fix_nginx_duplicate.py) **без повторного применения**. Фактический результат подтверждён предыдущими выводами `repair_result=success`, шестью статусами и нулём предупреждений. Независимый повторный SSH-аудит после изменения не выполнялся.

**Следующий шаг:** подготовить и проверить точечное добавление прокси `/studio` → `127.0.0.1:8091` в **единственный оставшийся активный HTTPS-vhost**, с предварительным выпуском безопасного PWA SW и сохранением правил `/api/*`. Смена маршрутизации пока не произведена.



## Независимая перепроверка после исправления — 31.10.2026, 08:36 UTC

**Проверка на чтение успешна:** [GitHub Actions run #41183811915, attempt 2](https://github.com/grunvald91/booking_bot/actions/runs/41183811915). Это новый SSH-сеанс ПОСЛЕ nginx reload, а не повторное применение скрипта.

- `nginx_systemd=active`, `nginx_test_ok=true`, `nginx_dump_ok=true`.
- `nginx_duplicate_server_warnings=0`, `servgo_https_duplicate_warning=false`.
- Текущий on-disk `nginx -T` SHA256: `6eb38d28950fec8265926eca3c16438a9fd6bceb0a768f580cb71d949d0bd6ad`.
- Из первоначальных восьми подходящих server-блоков осталось **шесть**, что соответствует удалению двух блоков-дубликатов HTTPS/HTTP из активного include.
- Для `servgo.ru:443` остался **один** блок из `/etc/nginx/sites-enabled/webapp`. Правило `= /sw.js` присутствует; `/studio` на основном домене по-прежнему **не включалось**.
- Маршруты остались: `servgo.ru/api/ → 127.0.0.1:8000`, `/api/legacy/` и `/api/media/ → 8080`; `growth.servgo.ru/studio → 8091`; `mail.servgo.ru` на отдельном TLS vhost.
- Сервисы `booking-web-api`, `booking-bot` и `nginx` — **active**; контейнеры `ai-growth-os-api-1`, worker, Telegram, postgres присутствуют.
- SHA256 серверных файлов `web/api/main.py`, `web/api/routes/owner_mail.py`, `web/api/routes/studio_sso.py` и `web/frontend/dist/sw.js` **совпадают с предыдущим аудитом** и с четырьмя файлами main GitHub.
- Новая проверка **не выполняла reload, не меняла конфигурацию и не запускала deployment**. Проверка статусов шести HTTPS-маршрутов 200/200/200/200/401/200 была выполнена в процессе самого исправления и не была повторена в независимом read-only запуске.

**Вывод:** обнаруженное дублирование устранено и исправление устойчиво по состоянию файловой конфигурации/активных служб. Нет признаков случайного переключения code branch, backend ports или PWA gateway. Полный пользовательский E2E (запись, админка, вход владельца, почта) отдельно не проводился, поэтому абсолютную гарантию отсутствия любых функциональных дефектов этот аудит не даёт.
