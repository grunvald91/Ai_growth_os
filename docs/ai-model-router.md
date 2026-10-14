# ServGo AI Model Router

## Status

The deterministic LLM router is integrated behind an **opt-in** feature flag. It does not require new paid search services or call an LLM to pick another LLM. The current deployment and default task behavior are untouched until the flag is enabled.

It supports the 27 registered agent roles and four extra content-production stage names. It also supports routing a future one-pass critic via trusted `routing_phase=critic` metadata, but **does not implement the critic workflow itself**.

## Configuration

- `MODEL_ROUTER_ENABLED=false`: legacy behavior by default.
- `MODEL_ROUTER_PROFILES`: JSON array. Fields: `model` (exact upstream model ID), `tier` (`economy`, `balanced`, `premium`), `enabled` (boolean), optional `input_usd_per_million`, `output_usd_per_million`, `web_search_verified`. Prices must be entered from the actual provider's billing information. Unknown prices stay unknown. K3, Sonnet, and Opus are **disabled in the starter catalog** until their exact gateway IDs/availability are verified.
- `MODEL_ROUTER_OVERRIDES`: JSON object mapping role name to a list of up to three enabled model IDs, e.g. `{"company_intake":["kimi-k3"],"cmo":["claude-opus-5-5"]}`. Separate critic configuration: `"article_writer:critic":["claude-sonnet-5-5"]`.
- `MODEL_ROUTER_MAX_CALL_USD`: optional numeric ceiling, e.g. `0.10`. A model must have both token prices configured and fit a conservative pre-call text-byte/token-output estimate; otherwise the router rejects it *before any provider call*. This is not a hard billing ceiling or durable per-tenant/monthly quota.

If a task requires premium reasoning (CMO or Research Lead), routing fails closed when no enabled premium model exists rather than silently lowering quality.

## Policy

- Premium: CMO, Research Lead; their critic stage also premium.
- Balanced: Company Intake, marketing strategy, SEO, editors, CRO, content researcher and complex director roles.
- Economy: mass drafting, text adaptation, routine extraction/metrics, community and distribution planning.
- Critic stage: balanced minimum, except premium for leadership roles; exactly one review cycle belongs to the workflow, not the router.
- Unknown roles: balanced minimum.
- Auto-routing prioritizes the minimum suitable tier, then lowest known configured price. An operator may manually fix a role to ordered models.
- 429, 5xx, network errors and timeouts permit bounded fallback. 400/401/403, schema/validation errors and other nontransient issues do not.

The existing `PrivacyGateway` still sanitizes all outgoing requests. Tool/action approval is unchanged.

## Live web-search caveat

**No active search-tool adapter exists in the current OpenAI-compatible XAIProvider.** Merely selecting Kimi K3 via a text-completions API does **not** provide real-time search. A trusted call with `requires_web_search=true` is rejected with `WebSearchUnavailable` before spending tokens. Do not mark a model searchable just because browsing works in a consumer chat UI.

A future end-to-end search adapter can enable it only when provider tool calls, real source URLs, Russian-site access and billing have all been validated. This module does not add Yandex/2GIS/search subscriptions.

Existing Company Intake continues to extract facts from provided URLs/documents; it is not autonomous web discovery.

## Offline verification and activation

Run `python -m pytest -q tests/test_model_router.py` without API keys, network access or server. To activate, verify model IDs, enter actual prices if enforcing a ceiling, enable a premium model for CMO, optionally assign Company Intake to K3, and switch `MODEL_ROUTER_ENABLED=true` in a controlled environment. Inspect resulting `AgentRun.model` and token usage records.

The monetary cost currently persisted by several existing workflows remains `unknown` even when tokens are recorded. Accurate per-tenant cost and durable budget enforcement need follow-up work. No server deployment is included here.


## Phase 2: model availability and cost observability

The operator can use `python -m app.llm.model_catalog --remote` **locally** after configuring `XAI_API_KEY`. It makes exactly one read-only `GET /models` request to the trusted HTTPS gateway and lists exact model IDs. No chat/completion generation and no external search subscription are started. A provider may reject this endpoint: this is a capability limitation, not proof that models are unavailable. Do not put API keys in logs or CLI arguments.

Availability via `GET /models` is **not** proof of web search, Russian-language website access, source quality, region availability or billed price. Kimi K3 remains disabled until tested with operator authorization; no paid model or search probe is executed automatically. The existing provider still rejects `requires_web_search` because it has no verified search-tool protocol.

The router now accepts trusted `routing_complexity=low|normal|high` metadata. High complexity upgrades quality to premium; it never downgrades an expensive/high-risk role. All overrides must satisfy that minimum.

When enabled and when a configured model returns actual input and output token counts, the router attaches `estimated_cost_usd` calculated from the configured per-model tariff. This value is **not** a verified ClaudeHub charge. The downstream CostRecord keeps `amount=0` and `cost_known=false`, and adds `details.estimated_amount_usd` with its price basis; zero must never be treated as a free call. Unknown tariff, unknown model alias, or missing usage => unknown estimate.

`app.llm.usage_report.report_product_usage(db, product_id)` exposes read-only, product-scoped token and estimated-price aggregation for future **authenticated** dashboards. No public endpoint was added because this API currently lacks a complete owner/tenant authorization boundary.

To enable this in production Docker Compose, explicitly supply `MODEL_ROUTER_ENABLED=true` **and** `MODEL_ROUTER_PROFILES` JSON with a premium model for CMO. Optional: `MODEL_ROUTER_OVERRIDES` JSON and `MODEL_ROUTER_MAX_CALL_USD`. The configuration is forwarded to api, worker and Telegram only when set. Keep defaults off until verified provider IDs and prices are entered.

Known gaps: no direct provider billing reconciliation, no hard cumulative per-company spending quota, no live search tool integration, no web UI/authenticated management API, no actual critic invocation. All remain pending.


## Owner-only team dashboard (Phase 3)

The new `/studio/team` page is reachable from Growth Studio's sidebar and mobile navigation. It uses a **separate mandatory owner bearer token**, not the platform's unprotected development APIs. The HTML/CSS/JS shell contains no secrets and makes no provider calls on initial load.

1. Set a fresh random `MODEL_ROUTER_ADMIN_TOKEN` of **at least 32 characters** in the deployment environment for the **API container only**. Keep the token private; never commit it.
2. Open `/studio/team` behind HTTPS/your existing web-edge authentication and enter the token. The token is retained only in the tab's JavaScript memory, not saved to localStorage, cookies or a database.
3. Edit enabled models, operator-researched USD per million token prices, main/fallback routing and critic model per role. The dashboard includes the 27 registered roles plus four additional content-pipeline stages.
4. Click **Проверить настройки** to preview deterministic route selection without generating tokens. The dashboard refuses to enable a policy with missing main or critic model coverage.
5. Save. It writes a private `PRODUCTS_ROOT/.model-router-config.json` with optimistic revision protection, a filesystem lock, atomic file replacement and mode `0600`. The shared production `company_products` volume is already mounted into API, worker and Telegram. Fresh `build_llm_provider(settings)` calls read this file and pick up the latest policy; no deployment/restart is necessary for such **new** provider instances. An already running request may finish with its old policy.
6. The **Модели** tab can explicitly invoke an authenticated, read-only `GET /models` request to the configured provider. This is not a paid inference test and cannot establish web search.
7. The **Расходы** tab reads per-product, role-grouped stored usage estimates. It is not a provider billing report.

New endpoints:
- `GET /api/admin/team` — owner-scoped configuration, route preview and product names.
- `POST /api/admin/team/preview` — validate *draft*, with no persistence or inference.
- `PUT /api/admin/team` — atomic update with `expected_revision`; stale updates return HTTP 409.
- `GET /api/admin/team/catalog` — optional, explicit read-only provider model listing.
- `GET /api/admin/team/usage?product_id=...` — read-only aggregated usage for one project.

Every data and mutation endpoint requires the long owner bearer token, including reads. No API keys are returned. When the token is unset, the endpoints return HTTP 503 and configuration cannot be edited. Use HTTPS and edge-level access control in production as additional protection.

This is a **platform-owner panel**, not multi-tenant self-service. Tenant-specific model budgets/permissions and an audited role-based identity system need their own phase before exposing this feature to individual client-business owners. The toggle does **not** activate the critic workflow, search adapters, or provider-side spending limits. The cost ceiling is only a pre-call estimate, not a hard billing cap.

Quick offline smoke test: `python -m pytest -q tests/test_team_dashboard.py tests/test_model_router.py tests/test_model_router_composition.py tests/test_model_router_costing.py tests/test_model_catalog.py`. JavaScript uses a static same-origin asset and no external dependencies.
