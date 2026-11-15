# Comment sales assistant

Open **Growth Studio → Комментарии** at
`/studio/products/{product_id}/comments`. This extends the existing Studio shell,
design tokens and desktop/mobile navigation. The existing owner-authenticated
web edge must protect this page and `/products/*/comments/*`, just like the other
owner APIs. Compose continues binding the API to loopback only.

Run `alembic upgrade head` before starting the updated API. Migration 0005 adds
business-scoped channels and unique inbound comments; credentials remain outside
PostgreSQL in the existing encrypted integration store on the persistent
`company_products` volume. Back up that volume including `_integrations/mail.key`.
Losing the encryption key requires reconnecting integrations.

## Business knowledge and behavior

`comment_sales` is a logical draft-only agent. It uses the application's configured
LLMProvider through LLMService and PrivacyGateway. No new LLM account is required.
Before every generation it reloads the selected canonical company profile.
Only identity, offer, customers, proof, brand and constraints enter the agent;
speculative hypotheses, unknowns and outreach/internal notes are excluded.
Owner-approved supplemental FAQ/facts, tone, support contact, prohibited claims
and response examples are versioned as `comment_agent_settings` artifacts.
This is knowledge/instruction configuration, not model-weight fine-tuning.

The agent answers the actual question first, asks at most one relevant needs
question, recommends a suitable next step without pressure and does not invent
prices, guarantees, urgency or discounts. It must not request private customer
data publicly or promise to initiate private messages. Complaints and missing
information require human review. Every model result is a validated draft, with
an audited Task, AgentRun and Artifact. The UI provides trial replies, editing,
copying, skipping and handoff to a human. No background auto-publishing is enabled.

## Foreign server egress

**VK and VK Video use direct HTTPS requests to api.vk.com**, including connection
checks, reads and publishing. Their HTTP client disables environment proxy lookup
(`trust_env=false`) and uses no proxy even if the foreign proxy is configured or
unavailable.

YouTube, Telegram, Instagram and Threads use only `COMMENT_PLATFORM_PROXY_URL`, an
HTTP(S) forward proxy. The owner confirmed the existing route: entrypoint
`188.120.254.223:8891`, onward via their foreign server beginning with `89…`.
Production Compose defaults to this confirmed entrypoint; the exact downstream IP
is not needed by the application. There is no implicit reuse of
`TELEGRAM_PROXY_URL`: that setting belongs to the existing controller. An unset or
unavailable comment proxy fails these platform operations with **no direct fallback**.
Redirects are disabled and HTTPS certificate verification remains enabled.
The proxy is configured server-side, outside the LLM context and web UI. Configure
credentials as protected runtime values, never committed files.

### What runs on the foreign server

The comment agent, business knowledge, encrypted platform credentials and adapters
run inside AI Growth OS. The foreign server only needs a working forward proxy
with HTTPS CONNECT support; no separate agent service or platform adapter code
is required there. The owner confirmed an existing proxy chain through 188 → 89.
Check the existing proxy's CONNECT access before making any server changes; the
full downstream IP is still needed to administer that server. Its running state
and platform connectivity have not been verified from this development workspace.

On that server, configure proxy access from the AI Growth OS server only (IP ACL
and/or authenticated access), and allow HTTPS CONNECT to port 443 for
`www.googleapis.com`, `api.telegram.org`, `graph.instagram.com` and
`graph.threads.net`. Configure firewall access to the proxy port accordingly.
A Squid, Tinyproxy or equivalent forward proxy can provide this; a normal reverse
proxy alone cannot replace it. No inbound platform webhooks are required in the
current refresh-on-demand implementation.

On the AI Growth OS server, set `COMMENT_PLATFORM_PROXY_URL` in the protected
runtime environment to the confirmed endpoint (for example,
`http://CONFIRMED_FOREIGN_HOST:PROXY_PORT`), apply migration 0005, then recreate
API and worker containers. Verify each account through its connection card and
perform an owner-approved trial reply. Actual connectivity has to be tested from
the application server; a saved URL alone does not prove the server is reachable.

## Platform-specific setup

- **YouTube:** Google OAuth access token with `youtube.force-ssl` and the owned
  channel ID. The connector validates channel ownership, imports comment threads,
  and replies with `comments.insert`. Pagination is persisted between refreshes.
  Each reply currently costs 50 quota units. Reconnect when the token expires;
  an OAuth browser login/refresh-token flow is not included in this version.
  [Official reply documentation](https://developers.google.com/youtube/v3/docs/comments/insert).
- **Telegram:** a dedicated BotFather bot, administrator in the channel's linked
  discussion supergroup. The controller bot cannot be reused. No existing webhook
  or second polling consumer may own this bot. Imports new replies from that
  supergroup only, ignores bots/automatic forwards/off-topic group chatter, and
  replies using `reply_parameters` with `allow_sending_without_reply=false`.
  The Bot API cannot retrieve old group history. [Bot API](https://core.telegram.org/bots/api).
- **Instagram:** professional account and Meta application using **Instagram Login**;
  access token with `instagram_business_basic` and
  `instagram_business_manage_comments`. Account ownership and comment-read access
  are checked before saving. Imports recent media comments with pagination, then
  replies under the source comment. This route does not require a Facebook Page.
  Third-party accounts require the relevant Meta access/App Review. DMs are a
  separate API capability and are not sent.
  [Meta's official collection](https://www.postman.com/meta/instagram/folder/6raa77c/instagram-api-with-instagram-login).
- **Threads:** a separate Meta Threads application and owner access token with
  `threads_basic`, `threads_content_publish`, `threads_read_replies` and
  `threads_manage_replies`. Instagram credentials cannot replace this connection.
  Imports the flat conversation of owned posts, including nested replies, with
  persistent pagination and excludes the owner's responses. Replies are limited
  to 500 characters in generation, editing and sending. The adapter rechecks the
  token owner, native reply's root and root post owner, creates a TEXT reply
  container, then publishes it after explicit owner approval. Third-party access
  may require Meta App Review. Reconnect when the token expires.
  [Meta's official collection](https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api).
- **VK:** community wall comments, using the positive numeric community ID and a
  **user** OAuth token with `wall` and `groups` permissions. The user must be a
  community editor or administrator. Community tokens cannot replace the user
  token for the read methods used here. Imports own wall posts and all comment
  threads in bounded batches; replies use `wall.createComment`, numeric community
  `from_group` and the approved comment's stable `guid`.
- **VK Видео:** a separate connection for community-owned videos with a user token
  with `video` and `groups` permissions. Saved videos owned by other accounts are
  excluded. Imports video comment threads; replies use `video.createComment`,
  boolean `from_group=1` and a stable `guid`. Personal videos and VK Clips are not
  covered. Both VK adapters verify manager role, object ownership and native
  comment before publishing, and namespace inbound IDs by owner/object/comment.
  API version 5.199; OAuth availability and publishing depend on the app's granted
  access and the community's comment settings.
  [Wall reply](https://dev.vk.com/ru/method/wall.createComment),
  [video reply](https://dev.vk.com/ru/method/video.createComment),
  [official method schemas](https://github.com/VKCOM/vk-api-schema).
- **Facebook:** shows setup guidance and documentation; the live adapter is not
  enabled. Manually paste comments and copy the approved reply.
- **TikTok:** Display API and Login Kit do not provide comment-reply publishing.
  Do not claim an automatic connection through them. Separate approved Business
  API access would need an additional adapter. Current mode is manual drafts.
  [Display API scope](https://developers.tiktok.com/docs/en/display-api-overview).
- **Дзен / vc.ru / TenChat / own site:** manual drafts, with platform-specific
  guidance. Own-site automation requires a connector for its actual CMS.

Connection uses server-side verification of user-supplied platform access tokens.
Tokens are encrypted at rest, removed on disconnect, never returned in API responses,
never written into audit artifacts and never passed to the model. No platform
password, scraping session or browser-cookie integration is used.

## Approval and delivery

Only comments imported through a verified adapter can be published. Manually
entered and trial comments can only be copied. The explicit **Одобрить и отправить**
action records the exact edited text in a separate approved Artifact, a YELLOW
Approval, ToolCall and Publication. VK publication uses a direct connection;
other live platforms use the configured foreign proxy.
Changing business knowledge, rules or the connection invalidates older drafts.
The gateway binds replies to the source account before sending.

A durable `sending` claim is committed before the external request. Repeated
clicks and subsequent requests cannot send it twice. Unconfirmed delivery becomes
`delivery_unknown`, with retries blocked. If the process stops during HTTP,
`sending` remains blocked too. Check the platform manually before operator
reconciliation; this version deliberately has no automatic retry or unverified
"mark as sent" button. A successful response stores the native reply ID. Other
platform errors are secret-safe and distinguish access and rate-limit failures.

Refreshes can happen manually through **Загрузить комментарии / Обновить**.
The opt-in **background import** is implemented for YouTube and Instagram only:

1. Set `COMMENT_BACKGROUND_POLL_ENABLED=true` in the API and worker runtime environment;
   keep `COMMENT_PLATFORM_PROXY_URL` configured for outbound access. The default is
   **false** and changing Studio settings alone cannot activate polling.
2. Each business enables **Автоматически загружать комментарии** on its Studio agent tab.
   Pausing the agent or disconnecting the channel prevents further imports.
3. The existing worker polls at most one eligible channel per 15-second tick.
   Each channel has a separate next-due timestamp in its existing JSON cursor.
   The default minimum interval is `COMMENT_BACKGROUND_POLL_SECONDS=600` (range 60–86400).
   On failure, attempts back off for 60, 120, 240… up to 3600 seconds.
4. Imported comments stay **new**; no LLM requests, approval operations, external
   reply publications or background auto-replies occur. Operators can use the
   existing manual **Подготовить ответ** and **Одобрить и отправить** workflow.
5. The cursor is persisted in the same transaction as imported comments. The
   product/channel lock and unique inbound key prevent duplicates during parallel
   workers and manual refresh. The persisted schedule survives worker restarts.
   An unsuccessful external request records only a generic error code, never a
   raw token/provider message.

The import loop is off in the default production Compose configuration. Do not
enable it until operators check real proxy connectivity, YouTube API quota and
Instagram account credentials.

## Background AI reply drafts (separate opt-in)

Automatic **draft generation** is available for newly imported YouTube and Instagram
comments, but **never publishes any reply**. Existing Studio manual approval is
still required for every outbound message.

- The API and worker must both receive `COMMENT_BACKGROUND_DRAFT_ENABLED=true`.
  This operator switch defaults to **false**.
- Each company must separately enable **Автоматически создавать ИИ-черновики**
  in the `Бизнес и агент` tab. `auto_draft=false` by default. It can be enabled
  for manually refreshed comments even when background import is disabled.
- `COMMENT_BACKGROUND_DRAFT_DAILY_LIMIT` defaults to **20** claim attempts per
  company per UTC day. It counts attempted calls, including failed generations;
  only one new comment per worker tick is prepared. This is an operator cap,
  not a claim about the LLM provider's own billing or limits.
- `COMMENT_BACKGROUND_DRAFT_MAX_AGE_DAYS=30`: auto-draft only imported comments
  containing a valid authoritative `published_at` from the platform no older
  than 30 days. Unknown-age and older comments remain available for manual review.
- `COMMENT_BACKGROUND_DRAFT_DAILY_TOKEN_LIMIT=100000`: separate per-company
  limit on *observed* input+output tokens for automatic drafts in UTC day.
  An automatic reply provider is configured without model fallback fan-out.
  When provider usage is absent, stop subsequent automatic calls for the day
  until inspected. Monetary cost remains **unknown**, not USD 0: the database
  records a 0 placeholder together with `cost_known=false`. These gates are
  not a guaranteed monetary ceiling; unsuccessful upstream requests may have
  costs that were not reported.
- Before the model is called, the agent locks the business/comment, creates
  `Task` and `AgentRun`, sets the comment status to `drafting`, and commits.
  Other worker instances and manual actions cannot start a second simultaneous
  generation. Successful calls produce a standard, auditable `draft` or
  `review` artifact. Failed calls become `draft_failed` and **are never
  retried automatically**. A person may explicitly use the existing draft button.
  Crash-interrupted `drafting` claims remain locked until manual investigation;
  do not auto-unlock them because a model request may already have been billed.
  A business owner can explicitly choose **Сбросить зависшую генерацию** in Studio
  after 15 minutes; the prior run is marked failed, and a late model response
  cannot overwrite the recovered state. This reset does not call the model.
- A disconnected channel, manual comment, test comment, already drafted or
  handled comment cannot be automatically regenerated. A complaint or unknown
  fact still goes to human review under the existing safety policy.
- There is no background call to `send`, `Publication` or external reply API.

The feature is opt-in and requires an authorized LLM provider already configured
in the worker. Monitor usage after controlled rollout. Webhooks, **auto-replies
without human approval**, built-in OAuth applications, automatic token refresh
and live account verification are still **not implemented**.
Real end-to-end testing needs authorized platform accounts; offline tests use fake
providers and HTTP transport mocks.

## Review hardening (2026-11-15)

- Background poll and paid draft loops run as independent asyncio tasks; slow
  model completion cannot block intake. Neither loop publishes.
- Candidate selection uses bounded chronological SQL pages instead of checking just the
  earliest 500 records, so opted-in tenants after a long disabled queue are found.
- Instagram queries recent media on every tick, while walking historic media and
  comment pages in a separate bounded batch (up to five pages/tick).
- YouTube imports top-level comments and inline replies, then persists a
  `reply_queue` and pages through `comments.list(parentId)` to retrieve
  reply threads that were truncated in the initial API response.
- Publish approval records the authenticated Studio login rather than a constant.
- GitHub Actions includes a dedicated disposable PostgreSQL workflow to test
  real comment claim and daily-budget row locks; ordinary SQLite tests do not
  establish multi-process PostgreSQL guarantees.
- Live API auth/quota and production delivery are NOT verified by offline tests.


## Instagram Unified Login + signed Webhook integration (2026-11)

New Meta apps may require the Unified Login flow via **Facebook Login for Business
configuration ID** instead of the old Instagram App ID + scope form. For this
flow, ServGo uses the Meta App ID, App Secret and `config_id`; after consent it
selects a granted professional account from
`GET /me/assigned_instagram_accounts` and uses `graph.facebook.com`.
Existing manual Instagram Login connections remain supported with
`graph.instagram.com`. Never reuse credentials between these two modes.

Required SERVER environment (no real values in Git):
- `COMMENT_META_APP_ID` — Meta application ID.
- `COMMENT_META_APP_SECRET` — secret for code exchange, OAuth HMAC and webhooks.
- `COMMENT_META_LOGIN_CONFIG_ID` — system-user token configuration under
  Facebook Login for Business. Permissions must include Instagram comment
  management, and business assets must be granted during consent.
- `COMMENT_META_PUBLIC_BASE=https://servgo.ru` — the exact external host
  reachable by both Studio and Meta. Register the **single** redirect URI
  `https://servgo.ru/studio/integrations/instagram/oauth/callback` in Meta.
- `COMMENT_META_WEBHOOK_VERIFY_TOKEN` — long random verification token entered
  in the Meta Dashboard (not the app secret).
- `COMMENT_META_GRAPH_VERSION=v27.0` — explicit Graph version; align with the
  current Meta app permissions, API availability and reviewed API methods.
- `COMMENT_PLATFORM_PROXY_URL` — required reachable foreign HTTP(S) proxy;
  API fails closed without it, never uses environment proxy fallback.

**Owner UX:** Growth Studio → Комментарии → Подключения → Instagram →
**Подключить через Meta**. Browser authorization uses short-lived signed HMAC
state bound to current Studio login, product ID and an HttpOnly, SameSite=Lax
nonce cookie. The callback is globally fixed; the selected business comes only
from a verified state and its active Studio permissions. Credentials are stored
in the encrypted persistent integration store. Before account selection, a
short-lived encrypted pending token is stored outside the SQL database; its
pending Artifact has a 10-minute expiry and never contains the token. Selecting
an authorized account reconfirms its Meta access and binds its native ID to just
one product. Reconnect or revoke access if permissions change.

**Webhook setup:** configure Instagram `comments` field in the Meta Dashboard
and set:
- Callback `https://servgo.ru/api/integrations/instagram/webhook` (public).
- Verify token exactly `COMMENT_META_WEBHOOK_VERIFY_TOKEN`.

GET performs Meta's verification handshake. POST enforces a 256 KiB body cap,
checks `X-Hub-Signature-256` against the **original raw bytes**, validates
Instagram comment fields, binds events to an active Instagram account and one
specific product, then stores the comment under the existing unique
(product, platform, comment ID) key. Multiple webhook deliveries do not create
duplicate comments. No LLM or outbound Meta publish call runs in a webhook.
If the app lacks the required Live mode / Advanced Access / App Review or Meta
does not send events, the existing opt-in polling remains an independent
reconciliation path.

**Important operating limits:**
- Official Meta approvals, application Live mode, user/business grants, network
  proxy and real callback delivery are **not verified** by mock unit tests.
- Some Webhook events do not carry a source comment timestamp or media caption.
  They are imported and visible in Studio; auto-drafts require an authoritative
  `published_at`, so such events may need polling enrichment or manual drafting.
- Unknown or revoked tokens do not trigger blind retries of public replies.
  Existing durable `sending`/`delivery_unknown` protections still apply.
- Automated publishing is **not enabled** by OAuth/Webhooks, nor by polling or
  draft opt-in. Each public response still requires human approval.
- Jev is not activated for small comment volumes; a future adaptive bulk triage
  must be enabled only after a real threshold/cost/quality comparison. It is
  not required for the initial Instagram E2E.

References: [Meta comments webhooks](https://developers.facebook.com/docs/graph-api/webhooks/reference/instagram),
[Meta assigned Instagram assets](https://developers.facebook.com/docs/graph-api/reference/user/assigned_instagram_accounts/),
[Postiz Unified Login compatibility reports](https://github.com/gitroomhq/postiz-app/issues/2350).
