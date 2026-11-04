# Yandex Maps organization discovery for ServGo (draft integration)

## Status

This change adds a server-only *provider adapter* and an internal authenticated lookup
for use by ServGo components. It does not automatically enable a license, deploy a
GitHub Actions secret, send emails, or activate Yandex inside company intake and
competitive intelligence. Those last two consumers still need their own approved
workflow integration.

## Source and data

Official product: https://yandex.ru/maps-api/products/geosearch-api
Request: https://yandex.ru/maps-api/docs/geosearch-api/request.html
Response: https://yandex.ru/maps-api/docs/geosearch-api/response.html
Terms: https://yandex.ru/legal/maps_api/ru

The API responds with organization identifiers/names, categories, addresses,
phone numbers and websites. **Email is not a documented output**. Mail visits
public company websites to find email and keeps source provenance. Do not
assert the site email belongs to the contact until it has been reviewed.

The Yandex product page describes tier limits **per day**, e.g., 1,000/day,
and separate standard and expanded paid licenses. This DOES NOT establish
the actual key's subscribed product, quota, terms, billing, commercial
authorization or database-storage rights. Never infer them from presence of
an API key.

## Enablement (operator-controlled)

1. Verify in Yandex Developer Cabinet that the key is for **API Поиска по организациям**
   (not a different Yandex API), and verify the actual quota and paid/
   testing/commercial rights.
2. Confirm whether the contract explicitly permits storing and modifying
   organization records in persistent Mail lead databases. The standard
   license may not authorize this; seek the expanded data-use license if needed.
3. Inject `API_YANDEX` into the **running Growth backend environment** via
   the protected deployment secret mechanism. A secret saved in GitHub Actions
   does not automatically enter the production container. Do not print its value.
4. Only after verifying authorization, set
   `SERVGO_YANDEX_PPO_LICENSE_CONFIRMED=true` in Growth.
5. Only if the license additionally permits saving results, set
   `SERVGO_YANDEX_PPO_STORAGE_ALLOWED=true`. With this flag off, Mail
   **cannot** use Yandex for stored lead discovery.
6. Optional `SERVGO_YANDEX_PPO_DAILY_BUDGET=50` (default). Conservative
   limit **within our integration**, not the provider's quota.
   `SERVGO_YANDEX_MAIL_MAX_PAGES=4` (default: 4 x 25 company records).
7. Deploy and validate first through a single small test query, then
   review results, matching, website/email extraction, licensing,
   and old 2GIS/Exa source paths. Do not bulk-import until reviewed.

## Architecture

Growth:
- `app.integrations.yandex_organizations.search()`: single outbound
  Yandex request; configuration-gated, validated, bounded, and quota-accounted.
- `GET /studio/internal/yandex-organizations/capabilities`
- `GET /studio/internal/yandex-organizations/search?query=...&city=...&skip=...`
- Both routes use existing `X-Studio-Key` / `SERVGO_INTERNAL_KEY`
  and neither exposes `API_YANDEX`.
- The Growh backend keeps only quota counters in
  `products/_integrations/yandex_ppo_usage.sqlite` (under configured
  `PRODUCTS_ROOT`). The operator must ensure this path is persistent and
  writable, and use only one shared quota file if multiple replicas run.

Mail (the separate `booking_bot` repository):
- Existing 2GIS and Exa integrations remain untouched and available.
- Adds optional `provider=yandex`. It is offered only if Growth says the
  license AND data-storage permissions are enabled.
- Search data is persisted only in that opt-in case; no auto-email send.
- Email comes independently from public websites, with separate attribution.
- Search results include a direct “Открыть в Картах” link to Yandex Maps
  when a numeric Yandex organization ID is available.
- Outbound mail still requires existing per-company campaign rules and
  recipient/approval safeguards.

## Broader uses — follow-up implementations

Business intelligence (`app/competitive_intelligence/service.py`) currently
operates on frozen `ResearchSourceBundle` evidence. A dedicated Yandex
discovery action could create auditable, owner-reviewed source candidates,
but must not accidentally change the frozen bundle of an already queued run.

Company onboarding (`app/intake/service.py`) currently uses reviewed
evidence and explicit profile application. A future optional “Найти в Яндексе”
step could suggest candidate organization matches; never silently overwrite
an owner's canonical profile or treat a provider match as owner-verified fact.

SEO local competitor discovery can re-use the same provider lookup only after
the product/usage rights and per-tenant limits have been specified. It must not
use organization-search results as search-query frequencies.

## Operational caveats

- Never send the key to frontend JavaScript.
- Requests are charged against the local budget as soon as reserved,
  including failures. HTTP 401/403/429 are surfaced without key URLs.
- Budget accounting is currently in **UTC days**. The provider's actual
  renewal timezone may differ; a lower conservative local budget minimizes
  surprises.
- API search is by query text and city words, not a hard geofence. Search
  results must not be treated as proof of operating in that city.
- No automatic emailing of contacts without the existing permission checks.
- The license and attribution rules are not substituted by this README;
  confirm the actual contract and required notices before public rollout.

## Test commands

```bash
pytest -q tests/test_yandex_organizations.py
```

Separate Mail repository:

```bash
pytest -q tests/test_yandex_mail_source.py tests/test_mail_company_search.py
```
