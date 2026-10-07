# AI Growth OS

Бизнес-разведка: отдельная страница `/intelligence`, три режима (отзывы, слабости,
цены), проверяемые источники, Jeff triage с fallback, прогресс и создание внутренних
задач/гипотез. [План, настройка и API](docs/competitive-intelligence.md).

AI Growth OS is a modular monolith foundation for a safe, auditable growth operating system. Phase 1A provides the FastAPI health endpoint, PostgreSQL persistence models, and migration/container scaffolding. Phase 1B adds core services and the provider-neutral LLM/privacy boundary. Phase 1C adds in-process agent contracts, single-skill loading, fake tools, and deterministic approval policy. Phase 1D adds a bounded organic-first daily growth review and typed product APIs. Production integrations remain out of scope.

## Local setup

Requirements: Python 3.12+, Docker, and Docker Compose.

### Run with Docker Compose

```bash
docker compose up --build
```

The API is available at <http://localhost:8000/health>. PostgreSQL is exposed on port `5432` with the local development database `ai_growth` and user/password `ai_growth`.

Apply the initial schema from a shell in the API container:

```bash
docker compose exec api alembic upgrade head
```

### Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
uvicorn app.main:app --reload
```

Set `DATABASE_URL` in `.env` to a reachable PostgreSQL instance before running migrations:

```bash
alembic upgrade head
```

Run tests with:

```bash
pytest
```

## Configuration

Application settings are loaded from environment variables or `.env`. Set `APP_ENV` and `DATABASE_URL` for the application database. LLM access is provider-neutral and configured with `LLM_PROVIDER` (default `anthropic`) and `LLM_MODEL` (default `claude-opus-5-5`). The Anthropic adapter reads `ANTHROPIC_API_KEY` and supports an Anthropic-compatible `ANTHROPIC_BASE_URL` such as `https://api.anthropic.com`. Keep credentials out of prompts and never commit them.

## Phase 1C boundaries

Set `SKILLS_ROOT` (default `skills`, relative to the working directory) to a local checkout or vendored copy of [claude-skills](https://github.com/alirezarezvani/claude-skills). Construct `SkillLoader(get_settings().skills_root)` and call `load("relative/skill-directory")` to read exactly `<root>/relative/skill-directory/SKILL.md`. Nothing is downloaded or bulk-loaded. Missing files raise `SkillNotFoundError`; invalid or out-of-root paths raise `SkillLoadError`. Keep the local skill tree trusted and read-only during execution.

`Agent.run(...)` selects one skill, calls `LLMService` once, validates one structured tool proposal against caller-supplied UUID references, checks role capabilities and approval policy, and executes at most one fake tool. See `tests/test_agents.py` for runnable offline integration examples using `FakeLLMProvider`, `PrivacyGateway`, `SkillLoader`, and `FakeToolGateway`.

Agents are logical roles inside this application, not background services. Each invocation is bounded; reasoning uses `LLMService`, which sanitizes requests through `PrivacyGateway` before calling a provider. CMO delegates specialist work; Content / SEO drafts but cannot publish.

Tool requests carry typed operations and UUID resource references, not credentials or arbitrary adapter arguments. Policy is deterministic code: analytics reads, research, drafts, and calculations are GREEN; publishing, email, website changes, and ad launches are YELLOW; budget increases, pricing, destructive data actions, and billing/credential/access changes are RED. YELLOW and RED requests stop at approval-required. Phase 1D persists pending approvals linked through task briefs, but never executes approved writes. A future execution boundary must validate a persisted approval against the exact operation and resources before any real adapter runs.

The fake gateway records calls for tests only. No production tool adapter or autonomous loop is included. Phase 1D reuses the existing schema.

## Project layout

- `app/api` — HTTP routes
- `app/core` — settings
- `app/db` — SQLAlchemy base and sessions
- `app/models` — persistence models
- `app/llm`, `app/privacy` — provider-neutral reasoning and outbound sanitization
- `app/agents` — bounded in-process logical agents and capability contracts
- `app/skills` — safe, on-demand loading of one skill
- `app/tools`, `app/policies` — typed fake tools and deterministic approval gates
- `alembic` — database migrations
- `tests` — offline unit and integration tests

## First working organic growth review (Phase 1D)

Run from the repository root. This API has **no authentication**: bind to localhost
and do not expose it publicly. Use a development database, not production.

1. Install with `python -m pip install -e '.[test]'` and copy `.env.example` to `.env`.
2. Start PostgreSQL with `docker compose up -d postgres`. Configure `DATABASE_URL`
   in `.env` for that database.
3. Set `LLM_PROVIDER=anthropic`, `LLM_MODEL` to a model supported by your provider,
   and `ANTHROPIC_API_KEY` in your private environment or `.env` (never commit it).
   `ANTHROPIC_BASE_URL=https://api.anthropic.com` is the default; an explicitly
   trusted compatible endpoint such as `https://api.claudehub.fun` is supported.
   That endpoint receives sanitized product context; choose it deliberately.
4. Run `alembic upgrade head` from your activated virtual environment, then `python -m app.bootstrap`.
   Bootstrap prints the ServGo product UUID. It is idempotent, creates one organic
   goal, and invents no production metrics or experiments.
5. Start `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.
6. Trigger and inspect (replace the UUID below):

```bash
PRODUCT_ID=replace-with-bootstrap-uuid
curl -X POST "http://127.0.0.1:8000/products/$PRODUCT_ID/daily-growth-review"
curl "http://127.0.0.1:8000/products/$PRODUCT_ID/decisions"
curl "http://127.0.0.1:8000/products/$PRODUCT_ID/tasks"
curl "http://127.0.0.1:8000/products/$PRODUCT_ID/artifacts"
curl "http://127.0.0.1:8000/products/$PRODUCT_ID/approvals"
```

Interactive typed API documentation is at `/docs`. Products support create/list/get;
product goals support create/list; experiments, tasks, artifacts, decisions and
approvals support product-scoped lists with bounded `limit`/`offset` pagination.
Review results include `status`, root `task_id`, CMO `run_id`, created record IDs,
and a fixed safe error code on failure. An HTTP 200 can contain `status=failed`;
inspect that field. Missing runtime configuration returns HTTP 503.

### What runs automatically

Each trigger performs one deterministic analytics snapshot and one CMO request
(timeout 90 seconds, output 6,000 tokens). It loads up to 20 records per context
kind, truncates text to 1,500 characters, and includes canonical product context
from validated `PRODUCTS_ROOT/<slug>/company-profile.json` when present. Only
when JSON is absent, the legacy `.claude/product-marketing-context.md` is read
(up to 12,000 characters). Arbitrary database `context_path` values are not opened. Context
truncation is disclosed to the CMO; analytics reports observations rather than
inventing totals, attribution or conversion rates.

The typed strategy allows at most five decisions and five specialist tasks per
decision. Evidence must refer to the exact product-scoped rows supplied to the
model. Missing evidence stays empty. All strategy writes are transactional;
invalid output leaves failed audit runs and no partial strategy. Usage is recorded
when provided, including invalid-output calls; monetary cost is explicitly unknown
(`details.cost_known=false`, not a claim of free inference).

Set `SKILLS_ROOT` to a trusted local checkout of
<https://github.com/alirezarezvani/claude-skills>. Optionally set `REVIEW_SKILL` to
one relative skill directory: only that skill is loaded. No skills are downloaded
or bulk-loaded. Missing requested skills fail safely. Task skill names are stored
in brief artifacts for future specialist execution, not loaded during this review.

### Explicit limits

Phase 1D queues specialist tasks; Phase 2A1 executes article production explicitly
through the endpoint below. No rendering, MP4 generation, publication, email,
website mutation or advertising occurs. Paid launch/budget operations are rejected
in code. YELLOW/RED proposals become pending approvals despite model approval
claims; there is no approve-and-execute endpoint. The funnel is traffic → leads →
demo/registration/booking → customers, not content volume. Channel adapters,
video production and real analytics ingestion belong to later phases.

There is no scheduler, retry loop, deduplication or concurrent-review locking;
each explicit POST creates a new review. Process termination/database outage can
leave running audit rows requiring operator reconciliation. PrivacyGateway uses
pattern-based redaction and configurable PII hooks, not universal secret/PII
recognition; never put credentials or raw customer data into product content.
Evidence IDs are validated, but factual truth still requires human review.

Run the complete offline suite with `python -m pytest`; fake providers require no
credentials or network. Phase 1D adds no schema migration.

## Article production (Phase 2A1)

Use the same local database and provider configuration above. Trigger one bounded
four-stage pipeline: content strategist → SEO editor → article writer → conversion
editor. All four requests pass through PrivacyGateway. No tool adapter runs.

```bash
curl -X POST "http://127.0.0.1:8000/products/$PRODUCT_ID/articles" \
  -H 'Content-Type: application/json' \
  -d '{"channel":"own_site","topic":"How to assess booking friction","audience":"Service business owners","problem":"Manual scheduling","request_publication":false}'
```

`topic` is required. `audience` and `problem` are optional; `channel` defaults to
`own_site` and also supports `dzen`, `tenchat`, `vc_ru`, and `telegram`. Optional
`task_id` must identify a queued task belonging to this product; optional
`decision_id` must also belong to the product. Unknown or foreign references return
404; an unavailable task returns 409; invalid input returns 422. Missing provider
configuration returns 503. Runtime failures return HTTP 200 with `status=failed`
and a safe error code, so always inspect the response status.

Successful responses include `task_id`, four `run_ids`, `main_artifact_id`, seven
`derivative_artifact_ids`, all `artifact_ids`, and any `approval_ids`. Inspect JSON
artifact content with `GET /products/{product_id}/artifacts/{artifact_id}` or the
existing product-scoped artifact list. Main content contains the full article,
strategy, SEO plan, proof-needed notes, CTA, proposed landing/internal links, and
content → traffic → lead → demo/registration/booking → customer measurement plan.
Each derivative includes a distinct channel-specific hook and framing, outline,
CTA, proposed landing page, and its own measurement plan. Derivatives cover Dzen,
TenChat, VC.ru, Telegram, FAQ, founder post, and a short-video **brief only**.

Facts must preserve verbatim source quotes from canonical product context under
`## Product` or `## Verified facts`; other article sections are labeled advice or
hypotheses. This is structural grounding, not universal factual verification:
human review must still catch unsupported claims in prose, titles, and CTAs.
Links and funnel events are proposals, not verified pages or observed metrics.
Invalid output or persistence failure leaves failed audit records and no partial
final article package. Token usage is retained; monetary cost remains unknown.

`request_publication=true` only creates a pending YELLOW approval and marks the
main artifact awaiting approval. Nothing is published, rendered, emailed, or sent
to an advertising platform. There is no approval execution endpoint. Requests
without a task ID create a new task each time; no automatic retry or scheduler is
introduced. This phase reuses the existing database schema.

Run article acceptance tests offline with `python -m pytest tests/test_articles.py`.

## Company Profile Contract v1

**Company profile = stable business truth; DB = dynamic operating truth.**
Keep company identity, offer, customers, commercial terms, approved proof, brand,
channels, outreach rules, measurement definitions and constraints in versioned
`products/<slug>/company-profile.json` with `schema_version: "1.0"`.
Keep goals, experiments, approvals, metrics and prospects in PostgreSQL.
Never place credentials or customer PII in profiles.

The typed contract preserves hypotheses and unknowns separately. Incomplete
profiles are valid. Legacy contract fields remain parseable for migration but do
not silently satisfy canonical readiness requirements. Only approved proof is
rendered in the factual quote section consumed by article grounding.
Rendering and the read API reuse PrivacyGateway; this is pattern-based redaction,
not a guarantee that arbitrary personal data will be detected.

`evaluate_readiness` returns `strategy`, `content`, `outreach` and
`measurement`, each with `ready`, `missing_fields` and `warnings`.
Omitted constraints, prohibited claims and do-not-contact rules differ from
explicitly supplied empty lists. Unknown data is never filled automatically.
`proof.no_proof_yet=true` explicitly allows content without evidence and warns
against inventing it. Profile readiness does not authorize external actions.

`GET /products/{product_id}/company-profile` returns sanitized structured
`profile`, `source`, `readiness` and safe `validation_errors`; unknown products
return 404. Writes require a separate explicit intake review confirmation. Legacy-only or missing profiles do not
establish readiness; raw legacy text is not returned by this endpoint.
Invalid JSON never falls back to markdown. Slugs are ASCII alphanumeric/hyphen
only, symlink components are rejected, UTF-8 file reads are bounded at 256 KiB.
The trusted product directory must be writable only by the application/operator; sources cannot choose paths.

Workflows prefer validated JSON and disclose readiness without globally blocking
research or strategy. Invalid canonical JSON fails context preparation safely.
`OutreachPackageService.prepare` requires `outreach.ready=true` before any
package/approval mutation; `CompanyProfileNotReady` reports the missing fields.
It selects only profile-allowed direct channels and uses profile sender, product
description and conversion action in drafts. Existing send approvals remain
required; preparation never sends messages. Bootstrap skips unready preparation
without changing an experiment's running state.

The migrated ServGo profile contains only facts already in the canonical
markdown. Candidate segments remain hypotheses. Geography/languages, approved
segments, CTA, brand tone, outreach permissions and measurement definitions still
need owner verification. Its gates therefore remain unready; this is an explicit
data gap, not inferred readiness. Company Intake Agent collects and confirms those inputs.


## Company Intake Agent

In the owner-authorized Telegram chat, open **🏢 Компания**, choose an existing
company or create one, then send several ordinary messages, public website links,
a company name/INN, or documents. Sources are retained separately with references
and read/unavailable/unsupported/truncated status. TXT, Markdown, JSON, CSV and
DOCX are supported (2 MiB upload; bounded extraction). PDF, XLSX, images, audio,
and macro documents are explicitly unsupported; supply text or DOCX instead.
There is no registry adapter configured by default: an INN/name alone is retained
as an identifier and cannot establish registry facts.

Choose **Собрать профиль** to produce a review. Extracted candidates carry exact
source quotes; unsupported model claims fail validation. Hypotheses and unknowns
stay separate, conflicts show alternatives, and at most three blocker questions
are asked at once. **Не знаю**, **Изменить**, **Добавить источник**, and **Отмена**
are supported. Review changes old → new and confirm the exact current review to
write the canonical profile. Adding materials invalidates the previous approval.
Canceling or drafting never changes the canonical file. Company approval does not
authorize outreach, publishing, payments or other external actions.

The local trusted API exposes product-scoped `POST /products/{id}/intake`,
`GET /products/{id}/intake/{session}`, and `sources`, `draft`, `unknown`,
`approve`, `cancel` POST suffixes. See `/docs` for typed bodies. Approval requires
`{"review_id":"<current review UUID>","confirmed":true}` exactly. This API has no
user authentication and must remain on loopback or behind operator authentication.
Telegram checks the configured owner chat before processing company input.

Existing Task/Artifact/AgentRun/CostRecord tables hold sessions, source versions,
review/draft/history and usage; no schema migration is added. The prior canonical
bytes and an apply intent are committed before atomic replacement, under a shared
filesystem lock. Approval rejects changed base files or stale reviews. If a process
or database fails during apply, operators must reconcile the saved apply intent,
canonical digest and prior-file artifact before retrying; DB and filesystem are
not one atomic transaction.

Compose shares a durable `company_products` volume between API, Telegram and
worker. The one-shot `profile-init` seeds packaged products, preserves every
existing canonical profile and refreshes ancillary repository files at deployment.
Do not remove this volume when recreating containers. Outside Compose, provide a
persistent writable `PRODUCTS_ROOT` explicitly. Telegram uses the worker's existing
ClaudeHub configuration (`TEAM_LLM_MODEL`, default `kimi-2.7`); API uses its configured
provider/model. Sources are untrusted data; websites forbid private/reserved IPs,
credentials, redirects and nonstandard ports, and pin public DNS results for fetch.
Pattern redaction does not detect all possible secrets: do not upload credentials.

Outreach packages and approval-to-artifact bindings are immutable. A replacement
package receives a new approval ID and supersedes only pending approvals for the
same experiment. Telegram decision buttons carry a digest of the displayed review;
stale or legacy outreach approval buttons cannot authorize changed content. Open
the current Decision Inbox to review and decide again. The deployment enrichment
importer returns a blocked reason for incomplete company profiles while preserving
enrichment and the running experiment; it does not bypass the outreach gate.
Canonical JSON must supply schema_version explicitly, and verified facts must have
nonblank statements.

## Issue #8: owner-operated content factory

Telegram **📝 Контент** selects a product, queues a default topic or accepts a custom topic,
shows job states and opens completed packages. The persistent worker prioritizes these jobs
independently of outreach experiments. The existing article workflow is extended only when
`produce_ready_assets=true`: SEO article + six complete channel adaptations
(Dzen, TenChat, vc.ru, Telegram, FAQ, proposed founder post) + three distinct 30-second
video scripts + a distribution/measurement plan. Packages are drafts requiring human review;
no automatic publishing or claim of SEO performance. ZIP download contains actual texts,
scripts and measurement metadata. The first ServGo package is queued idempotently by
`python -m app.content.bootstrap` during deployment.

After reviewing scripts, **Одобрить рендер трёх MP4** queues local ffmpeg production.
The first renderer produces 720×1280 H.264 typography videos, 25 fps, exactly 30 seconds,
with a cover, SRT subtitles and narration script. These are silent animated text-card videos:
voice/music, filmed footage and paid generative video are not connected. Rendered media,
profiles and integrations persist in the shared `company_products` Docker volume.
Publication is manual until a channel-specific adapter is connected. The bot accepts manual
cumulative views/clicks/leads/activation/customer snapshots linked to the article, channel and
publication URL. These are owner-reported observations, not verified platform analytics.
Missing metrics remain unknown; snapshots must not be summed as increments.

Issue #8 remains open: connected platform publishing/analytics, voice/music, research-backed
iterative topic selection and content improvement are subsequent work, not claimed complete.

## Contact cards and mail

**📇 Контакты** provides paginated business cards, public evidence URLs and validation dates.
Selecting Telegram/WhatsApp displays an exact draft and an official direct-chat link with
prefilled text. The user sends from their own messenger account. Opening the link is not
counted as contact; **Я отправил сообщение** explicitly records the owner's report.
Drafts/actions require a ready outreach profile, allowed channel, verified matching evidence,
a running experiment, a recently checked contact and no previous contact or exclusion.

**📧 Почта** stores SMTP settings per product: public SMTP hostname, TLS 465 or STARTTLS
587, login, sender address/name and an application password. The secret message must be deleted
successfully before persistence. Telegram bot chats are not end-to-end encrypted; use only a
revocable application password. Configuration is encrypted with a local Fernet key; both are
private files in the persistent server volume, never LLM context, artifacts, database or logs.
Host resolution rejects all private/reserved destinations and SMTP sockets pin the validated
public IP while TLS verifies the configured hostname. Connection test only authenticates/NOOP;
it does not send email.

Each email card previews From/To/subject/body. **Отправить это письмо** authorizes that exact
individual message, not a batch. Immutable drafts and review tokens invalidate changed profile,
contact or mailbox settings. A durable locked prospect reservation precedes SMTP; double taps
and cross-channel repeats are blocked. SMTP acceptance is recorded separately from inbox
receipt/read status. Network failures/crashes are left `delivery_unknown`/`sending` without
automatic retry, because delivery cannot safely be inferred. Check the mailbox before any
operator-led recovery. No credentials are supplied or emails sent as part of deployment.

### Файлы в разделе «Компания»

Бот читает TXT, MD, JSON, CSV, DOCX, PDF, XLSX, XLS и изображения JPG/JPEG, PNG, WEBP, TIFF, BMP. Максимальный размер файла — 20 МБ. Фото можно отправить обычным сообщением; подпись также сохраняется. Текстовые файлы поддерживают UTF-8, UTF-16 с BOM и Windows-1251; CSV — запятую, точку с запятой, табуляцию.

PDF со сканами и изображения распознаются локально Tesseract на русском и английском. Проверьте распознанные названия, числа и цены перед подтверждением профиля. Из многостраничных изображений читается только первый кадр. PDF ограничен 40 страницами; на один материал сохраняются первые 12 000 символов, на сессию — 20 источников / 60 000 символов. Частичное чтение явно обозначается; нужный раздел можно прислать отдельно. Excel читается по листам, формулы не вычисляются: используются сохранённые результаты; отсутствующий результат обозначается и не превращается в выдуманную цену.

Пустые, повреждённые, зашифрованные файлы и неизвестные форматы получают явный статус ошибки. DOC, XLSM, ZIP, аудио и видео не распознаются. Разбор документов выполняется в отдельном процессе с ограничением времени, памяти и CPU и не блокирует интерфейс бота/API.

### Редактор-планировщик и календарь подготовки

Роль `editorial_planner` отвечает за выбор площадок, частоты, форматов, тем, цели каждого материала, времени размещения и срока подготовки. Она использует профиль компании, сохранённые исследования/источники, требования владельца и последние снимки результатов публикаций. Снимки одной публикации не складываются; разные URL учитываются отдельно. Проверенные общие рекомендации YouTube и Google SEO имеют ссылки и дату проверки; их нельзя выдавать за исследование аудитории конкретного бизнеса или переносить на другую площадку. Нет подключённой почасовой аналитики аудитории: время размещения остаётся гипотезой либо явно указанным условием владельца.

В боте: **Контент → компания → Контент-план**. «Мои площадки и ресурсы» принимает обычный текст: существующие аккаунты, аудитория, доступное время, желаемый период, частота, даты и бюджет. «Составить / пересмотреть» создаёт отдельную задачу редактору. По умолчанию редактору предлагается тест на 14 дней и пересмотр через 7 дней; это начальная гипотеза, период может изменяться по требованиям владельца. Наличие аккаунтов, ресурсов, исследований и показателей не предполагается без источника.

Просмотр плана сам по себе ничего не запускает. **Подтвердить и готовить** включает подготовку по календарю; при изменении исходных данных или устаревании дат требуется свежий план. Одновременно действует один подтверждённый план компании. Будущие задачи можно остановить через «Пауза»; уже поставленные задачи продолжаются. Черновик пересмотра не меняет действующее расписание, пока владелец его не подтвердит.

Worker запускает подготовку заранее к срокам календаря. Одна тема на нескольких площадках использует общий пакет: SEO-статья, шесть текстовых адаптаций и три сценария роликов по 30 секунд. Это существующая единица производства; календарь явно показывает число таких пакетов. Запланированные публикации используют нужные материалы из пакета; вне календаря могут оставаться дополнительные адаптации. Видео — сценарии и отдельно одобренный рендер тихих текстовых карточек, без съёмки/озвучки/музыки. Повтор одного и того же материала на одной площадке не разрешается. Пропущенная дата без подготовленного задания обозначается как пропущенная, а не превращается в позднюю публикацию.

Календарь показывает состояния подготовки и ссылки на готовые пакеты. Публикация и сбор статистики остаются ручными. URL, введённый владельцем через «Записать результаты публикации», фиксируется как сообщение владельца, а не как проверенный внешней системой факт размещения. При нескольких вариантах видео общий URL статьи не приписывается одному из них автоматически. В дату пересмотра редактор получает задачу на новый черновик с актуальными наблюдениями; он снова требует подтверждения. Автоматического поиска новых внешних маркетинговых исследований в этой роли пока нет: используются сохранённые исследования и явно обозначенные пробелы.
