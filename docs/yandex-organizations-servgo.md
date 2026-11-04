# Яндекс Поиск по организациям в ServGo

## Реализация

Ключ `API_YANDEX` читается только серверным процессом AI Growth OS. Для поиска
используется официальный API `https://search-maps.yandex.ru/v1/` с
`type=biz`, `text`, `lang`, `results` и `skip`.
Документация:
https://yandex.ru/maps-api/docs/geosearch-api/request.html
https://yandex.ru/maps-api/docs/geosearch-api/response.html

Доступность определяется **только** ключом в runtime. Дополнительных флагов
подтверждения не требуется. Суточный локальный счётчик по умолчанию равен
1000, настраивается `SERVGO_YANDEX_PPO_DAILY_BUDGET`.
Это лимит нашего приложения, а не проверенный баланс кабинета Яндекса.

### Пять точек применения

1. **Mail** — третий провайдер поиска рядом с 2ГИС и Exa.
   Mail получает сведения через защищённый `X-Studio-Key` API Growth.
   Сайт организации проверяется отдельно для обнаружения email;
   рассылка не отправляется автоматически, старый механизм согласования
   сообщений/кампаний остаётся неизменным.
2. **Бизнес-разведка** — `/intelligence`, кнопка «Найти организации».
   Карточки с адресами, категориями и ссылками на карты добавляются как
   `website` evidence в `ResearchSourceBundle`, не как отзывы или цены.
3. **Создание и редактирование компании** — `/studio/new` и форма
   `/studio/products/{id}/profile`. Поиск по названию и городу,
   выбор карточки, подстановка сайта и сохранение карточки как
   `public_third_party` intake source; профиль проходит прежнее ревью.
4. **Growth / исследование рынка** — `/studio/products/{id}/market-research`.
   Поиск, подсчёт категорий в выбранной выдаче, явный выбор организаций,
   сохранение источников для бизнес-разведки и ссылка в интерфейсе Create.
5. **SEO-завод** — фоновая выборка до 10 локальных компаний по отрасли
   и географии из профиля именно этой компании. Полученный `seo_local_market`
   артефакт добавляется в контекст планирования; он не подменяет
   реальные поисковые подсказки или частотность.

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

Значение `API_YANDEX` должно оказаться в рабочем окружении контейнеров
Growth (не в исходниках). Привязка переменной добавлена в
`docker-compose.prod.yml` и `deploy/server/deploy_growth.py`.
GitHub Actions Secret сам по себе не означает, что это значение уже есть
в серверном `/opt/ai_growth_os/.env`; способ доставки секрета нужно
проверить отдельно. Секрет никогда не выводится в журнал.

### Тесты

```bash
pytest -q tests/test_yandex_organizations.py tests/test_yandex_workflows.py
```

В репозитории `booking_bot`:

```bash
pytest -q tests/test_yandex_mail_source.py tests/test_mail_company_search.py
```
