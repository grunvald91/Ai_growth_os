# Яндекс Поиск по организациям в ServGo

## Реализация

Ключ `API_YANDEX` читается только серверным процессом AI Growth OS. Для поиска
используется официальный API `https://search-maps.yandex.ru/v1/` с
`type=biz`, `text`, `lang`, `results` и `skip`.
Документация:
https://yandex.ru/maps-api/docs/geosearch-api/request.html
https://yandex.ru/maps-api/docs/geosearch-api/response.html

Доступность определяется **только** ключом в runtime. Дополнительных флагов
подтверждения не требуется. Учёт запросов ведётся в ServGo без навязанного
суточного потолка. Если понадобится, владелец может установить собственный
лимит переменной `SERVGO_YANDEX_PPO_DAILY_BUDGET`.

### Пять точек применения

1. **Mail** — третий провайдер поиска рядом с 2ГИС и Exa.
   Mail получает сведения через защищённый `X-Studio-Key` API Growth.
   Сайт организации проверяется отдельно для обнаружения email;
   рассылка не отправляется автоматически, старый механизм согласования
   сообщений/кампаний остаётся неизменным.
2. **Бизнес-разведка** — `/intelligence`, кнопка «Найти организации».
   При запуске исследования агент автоматически находит организации по отрасли,
   городу и именам конкурентов и безопасно проверяет до двух публичных сайтов.
   Справочные карточки добавляются как `website` evidence, не как отзывы или цены.
3. **Создание и редактирование компании** — `/studio/new` и форма
   `/studio/products/{id}/profile`. Поиск по названию и городу,
   выбор карточки, подстановка сайта и сохранение как `public_third_party`
   intake source; результаты подписаны сервером и привязаны к компании,
   срок действия подписи — 30 минут; профиль проходит прежнее ревью.
4. **Growth / исследование рынка** — `/studio/products/{id}/market-research`.
   Поиск, подсчёт категорий в выбранной выдаче, явный выбор организаций,
   сохранение подписанных результатов для бизнес-разведки **без повторного
   запроса** к Яндексу и ссылка в интерфейсе Create.
5. **SEO-завод** — фоновая выборка до 10 локальных компаний по отрасли
   и географии из профиля именно этой компании. Полученный `seo_local_market`
   артефакт добавляется в контекст планирования; он не подменяет
   реальные поисковые подсказки или частотность. Состояние источника и
   причины отказа доступны в карточке SEO-завода.

### Внутренние адреса

- `GET /studio/internal/yandex-organizations/capabilities`
- `GET /studio/internal/yandex-organizations/search?query=...&city=...&skip=...`
- `GET /studio/products/{product_id}/organizations/search?query=...&city=...`
- `GET /studio/organizations/search?query=...&city=...` — только оператор
- `GET /studio/products/{product_id}/market-research`
- `POST /studio/products/{product_id}/market-research/sources`

Studio / Growth применяет текущую авторизацию, CSRF для POST и
проверку доступа к компании. API возвращает адрес, категории,
сайт, телефоны и ссылку на Яндекс Карты. **Email напрямую API
не возвращает.**

### Деплой

Привязка переменной есть в `docker-compose.prod.yml` и
`deploy/server/deploy_growth.py`. В `.github/workflows/deploy-growth.yml`
добавлена автоматическая передача ранее созданного GitHub Actions Secret
`API_YANDEX` по защищённому SSH-каналу (через путь `SERVER_SSH_PASSWORD`),
после чего `deploy/ops/sync_yandex_env.py` обновляет серверные
`/opt/ai_growth_os/.env` и ссылку в `deploy.json`.
Значение не записывается в Git и не выводится в логах.
После выкладки workflow проверяет наличие переменной в контейнере, не показывая
значение. Если серверный password SSH-секрет отсутствует, этот способ
доставки не сработает — CI/деплой сообщит об ошибке; реальный production
вызов отдельно не проверялся.

### Тесты

```bash
pytest -q tests/test_yandex_organizations.py tests/test_yandex_workflows.py tests/test_yandex_evidence.py tests/test_yandex_intelligence.py tests/test_yandex_deploy.py
```

В репозитории `booking_bot`:

```bash
pytest -q tests/test_yandex_mail_source.py tests/test_mail_company_search.py
```
