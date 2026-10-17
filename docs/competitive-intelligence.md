# Бизнес-разведка — issue #126

## Уточнённый план

Phase 1 — один законченный сценарий на сохранённых/введённых источниках:

1. Расширить `ResearchSource` полями `published_at` и `source_type`, сохранив
   совместимость ранее сохранённых bundle. Вынести общий bounded loader для
   ResearchDepartment и нового сервиса; не создавать второй исследовательский отдел.
2. Сохранять запуск как существующую Task и `competitive_intelligence_run`,
   замораживать до 50 исходных материалов при постановке в очередь. Использовать
   существующий worker, атомарное получение задачи, журнал стадий и SSE с replay.
3. Сильная модель планирует исследование. Jeff получает до восьми коротких
   выдержек с ограниченными вариантами relevance/topic/sentiment. Confidence —
   минимальная уверенность этих решений. Порог настраивается. Ошибка, неполный
   batch или низкая уверенность требуют сильной модели; неуверенный fallback
   исключает материал. Недоступность Jeff не маскируется как работа Jeff.
4. Нормализация, дедупликация, фильтр дат, freshness и reference/quote validation
   выполняются кодом. Дата наблюдения не заменяет дату публикации. Старые источники
   сохраняются с пониженным весом; при явно выбранном периоде исключаются материалы
   вне периода и без даты публикации. Исключённые материалы и причины доступны.
5. Режимы MVP: отзывы, слабости, цены. Представления: выводы, слабости, цены,
   возможности, источники и методика. Возможности уже в MVP, но только как
   проверяемые гипотезы; полноценный поиск незанятых ниш остаётся в Phase 2.
6. Создание задачи/контент-гипотезы/гипотезы оффера включено в MVP, с исходными
   evidence и ссылкой на отчёт. Повторное создание той же гипотезы из того же
   вывода возвращает прежний результат. Это внутренние drafts, без публикации.
7. ResearchDepartment автоматически использует тот же сервис при наличии
   source bundles и передаёт его отчёт и ID в synthesis. VOC получает отобранные
   отзывы. Ошибка дополнительного исследования не останавливает прежний workflow.
   Контент-план получает новые report kinds как часть существующего context.
8. Тесты проверяют lifecycle, три режима, routing/fallback, отсутствие evidence,
   дедупликацию, даты, неверные ссылки/цитаты/числа, противоречия, privacy,
   идемпотентность экспортов, очередь, восстановление после сбоя, API/SSE и UI.

Phase 2: live search и автоматический подбор конкурентов, свободный исследовательский
brief с автоматическим выбором режима, positioning, полноценный market-gap анализ,
scheduled refresh, история цен и alerts. URL сам по себе в MVP не загружается:
нужна выдержка или ранее сохранённый `ResearchSourceBundle`.

## Запуск

Страница: `/intelligence?product_id=<UUID>`, доступна из навигации Growth Studio.
Выберите компанию, задайте вопрос, режим, регион, период и глубину. Конкуренты
задаются вручную; пустой список означает всех конкурентов в сохранённых материалах.
В форме можно добавить URL и точную выдержку каждого источника. Без новых источников
используются последние сохранённые bundle компании (до 10 bundle / 50 материалов).
Это ограниченная выборка, а не полный рынок. Глубина задаёт максимум классифицируемых
материалов: Быстро — 10, Стандарт — 25, Глубоко — 50; остальные сохраняются как исключённые.

`POST /competitive-intelligence/runs` возвращает `202` и ID существующей Task.
Для выполнения нужен существующий процесс `python -m app.team_worker` с тем же
`DATABASE_URL` и `PRODUCTS_ROOT`, что API; production Compose уже содержит worker.
HTTP endpoints сохраняют existing local-owner boundary приложения: production API
слушает loopback и должен находиться за авторизованным owner gateway.

Jeff необязателен. Поддерживаемая граница — OpenAI-compatible Chat Completions с
JSON schema через существующий `LLMProvider` adapter. Для подключения задайте:

```dotenv
JEFF_BASE_URL=https://<your-compatible-decision-endpoint>/v1
JEFF_API_KEY=<runtime-secret>
JEFF_MODEL=<available-model-id>
INTELLIGENCE_CONFIDENCE_THRESHOLD=0.8
INTELLIGENCE_CAMPAIGN_ENABLED=true
```

В репозитории нет подтверждённого native Jeff API или credentials. Совместимость
конкретного endpoint нужно проверять при его подключении; наличие env names не
доказывает поддержку его native protocol. Без endpoint весь triage выполняет
сильная модель через существующие `LLM_PROVIDER`, `LLM_MODEL`, fallback chain и
PrivacyGateway. Методика сохраняет `jeff_available`, `jeff_processed`, escalations
и validator каждого материала. Ключи никогда не передаются доменному workflow.

## Контракт и границы доказательств

Каждый материал хранит URL, competitor, source type, excerpt, rating (только если
передан), отдельные publication/observation timestamps, hash, relevance/recency,
confidence, validator и disposition. Observed_at без корректной даты заменяется
моментом нормализации; исходное значение отдельно хранится в supplied_observed_at.
Unknown publication dates остаются null.

Выводы имеют тип signal/inference/conflict, evidence IDs, точные quotes и
детерминированные `evidence_count`/`source_count`/period. Unknown ID или несуществующая
цитата отклоняют вывод; цены требуют источников типа pricing; числовые утверждения,
которых нет в cited excerpts, отклоняются. Противоположные sentiment для одного
competitor/topic устанавливают conflict. Confidence ограничивается уверенностью
классификации. Возможности всегда остаются гипотезами для проверки.

Проверка provenance и совпадения цитат не является независимой проверкой истинности
материала или семантической entailment каждого предложения. Числовое совпадение
тоже не подтверждает единицу измерения/тариф/период. Пользователь открывает первоисточник
и видит границы выборки; модель не получает разрешения объявлять рыночную истину.
Без retained evidence отчёт имеет status `needs_sources` и не содержит выводов.

Состояния: queued → running → completed/partial/needs_sources либо failed. Ошибки
planning/synthesis оставляют безопасный error code; сохранённый evidence доступен.
Deadline запуска — 20 минут, этапа сильной модели — 120 секунд, Jeff — 30 секунд.
Worker помечает RUNNING старше 25 минут как interrupted без автоматического
повторения model spend. Follow-up создаёт новый запуск с теми же замороженными
материалами. Tasks, Artifact kinds, AgentRun и CostRecord используются без новых
таблиц и без миграции; денежная цена неизвестна и явно помечается `cost_known=false`.

## API

| Метод | URL | Результат |
| --- | --- | --- |
| POST | `/competitive-intelligence/runs` | enqueue research (`RunInput`) |
| GET | `/competitive-intelligence/runs?product_id=…` | 20 последних запусков компании |
| GET | `/competitive-intelligence/runs/{id}` | stage, status, counts, plan, artifact IDs |
| GET | `/competitive-intelligence/runs/{id}/events` | SSE; `Last-Event-ID` / `after` replay |
| GET | `/competitive-intelligence/runs/{id}/evidence` | retained + excluded; optional `disposition` filter |
| GET | `/competitive-intelligence/runs/{id}/report` | validated report, `409` until ready |
| POST | `/competitive-intelligence/runs/{id}/follow-up` | `{ "brief": "…" }` → новый запуск |
| POST | `/competitive-intelligence/runs/{id}/export` | `{ "insight_id": "i1", "target": "task" }` |

`target`: task / content_hypothesis / offer_hypothesis. Export creates a queued
specialist Task with a task_brief and a source-grounded draft Artifact. Existing
team-cycle API can process it; it does not publish content or change pricing.

Example request:

```json
{
  "product_id": "<existing-product-uuid>",
  "brief": "Сравнить цены конкурентов",
  "mode": "pricing",
  "depth": "standard",
  "competitors": ["Example"],
  "source_bundle": {
    "label": "Тарифы",
    "sources": [{
      "url": "https://example.com/pricing",
      "title": "Публичный тариф",
      "platform": "Website",
      "competitor": "Example",
      "source_type": "pricing",
      "excerpt": "<exact excerpt from the source>",
      "published_at": null,
      "observed_at": "<actual ISO observation time>"
    }]
  }
}
```
