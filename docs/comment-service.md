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

Every real adapter request (authorization checks, reading and sending) goes
through `COMMENT_PLATFORM_PROXY_URL`, an HTTP(S) forward proxy on the owner's
foreign server. If unset in development, `TELEGRAM_PROXY_URL` is reused. Production
Compose defaults to the foreign proxy already used by this project's Telegram
service. An unavailable proxy fails the operation; **there is no direct fallback**.
Redirects are disabled and HTTPS certificate verification remains enabled.
The proxy is supplied server-side and is never part of the LLM context or web UI.
If proxy authentication is needed, configure the URL as a protected runtime value;
do not commit credentials. The UI reports configured routing separately from a
successful real platform check. Platform account access must still be authorized.

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
Approval, ToolCall and Publication. Publication uses the configured foreign proxy.
Changing business knowledge, rules or the connection invalidates older drafts.
The gateway binds replies to the source account before sending.

A durable `sending` claim is committed before the external request. Repeated
clicks and subsequent requests cannot send it twice. Unconfirmed delivery becomes
`delivery_unknown`, with retries blocked. If the process stops during HTTP,
`sending` remains blocked too. Check the platform manually before operator
reconciliation; this version deliberately has no automatic retry or unverified
"mark as sent" button. A successful response stores the native reply ID. Other
platform errors are secret-safe and distinguish access and rate-limit failures.

Refreshes currently happen through **Загрузить комментарии / Обновить**; continuous
polling, inbound webhooks, auto-replies, built-in OAuth applications and automatic
token refresh are not implemented. A real production verification needs authorized
platform accounts; automated tests use fake providers and HTTP transport mocks.
