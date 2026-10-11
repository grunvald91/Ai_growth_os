# Growth Studio · Социальные сети: Telegram + OAuth

Development branch: `feature/telegram-multichannel-oauth-20261010`.

## User experience

- From **Growth Studio → Социальные сети → Каналы и публикации**, choose **Добавить бота в канал**. No BotFather token is requested from the customer.
- Telegram opens the official channel selection dialog; give the shared publishing bot the `post_messages` administrator right.
- Enter public `@channel` or private channel numeric ID (`-100…`) in ServGo.
- In the pending connection card, sign in using the official Telegram Login Widget as **an administrator of that channel**. ServGo verifies the signed login data with HMAC and Telegram getChatMember for both the operator and the bot. It does not rely on a public description code.
- Repeat for as many channels as needed. Each company has its own channels and scheduled jobs; chat IDs cannot be claimed by another company.
- Select several connected channels, enter text and optionally upload JPG/PNG/MP4 (up to 10 files, maximum 20 MiB each), select a local date/time and schedule. Statuses and Telegram message IDs are tracked **per channel**.
- Turn on **Автопубликация по утверждённому контент-плану** for each channel independently. The worker creates text posts only for future Telegram slots in owner-approved active plans, once the content-factory Telegram adaptation is ready. The job is created once per plan-slot/channel; the unique constraint prevents duplicates. Photos/videos can be attached to a selected future Telegram slot of an active approved editorial plan; the background worker automatically includes those real uploaded assets for each opted-in channel. Photos/videos can also be scheduled manually. This does not pretend that a script/storyboard is a video.
- Scheduled jobs are claimed and committed **before** the HTTP request. If a timeout or crash makes delivery ambiguous, the job remains sending or delivery_unknown for **manual reconciliation**. Do not blindly retry, or posts might duplicate.
- Disconnecting a channel disables its future jobs, without deleting past delivery records.

## Production configuration

Provision a **dedicated** ServGo publisher bot in [@BotFather](https://t.me/BotFather). It must not be the existing AI Growth OS controller token. In production environment, available to **API** and **worker**, set:

    SOCIAL_TELEGRAM_BOT_TOKEN=...      # secret, never commit
    SOCIAL_TELEGRAM_BOT_USERNAME=...   # username without @
    COMMENT_PLATFORM_PROXY_URL=http://CONFIRMED_PROXY_HOST:PORT
    SOCIAL_OAUTH_REDIRECT_BASE=https://growth.servgo.ru

The existing foreign HTTPS CONNECT proxy must allow api.telegram.org, oauth2.googleapis.com, api.instagram.com, graph.instagram.com, www.googleapis.com. The API/worker make outbound token exchanges. No direct network fallback is permitted.

In @BotFather, register the public HTTPS website with /setdomain for the legacy Telegram Login Widget, or its equivalent Allowed URLs setting if using Telegram's newer Login management interface. The widget uses the official signed login scheme documented at https://core.telegram.org/widgets/login/. The new Telegram OIDC Login SDK is described at https://core.telegram.org/bots/telegram-login; an OIDC migration can follow separately. The website's authenticated reverse proxy must preserve the signed return query parameters and allow normal same-site session cookies.

Register OAuth applications once at the platform level (not separately for every business):

| Platform | Redirect URI to register | Server environment |
| --- | --- | --- |
| YouTube / Google | `https://growth.servgo.ru/social/oauth/youtube/callback` | SOCIAL_GOOGLE_CLIENT_ID, SOCIAL_GOOGLE_CLIENT_SECRET |
| Instagram Login | `https://growth.servgo.ru/social/oauth/instagram/callback` | SOCIAL_INSTAGRAM_CLIENT_ID, SOCIAL_INSTAGRAM_CLIENT_SECRET |

Use the official provider consoles and register these redirect URIs exactly. For Google request youtube.force-ssl and offline access/refresh token; for Instagram request instagram_business_basic and instagram_business_manage_comments and the relevant external-app review. The YouTube OAuth path presently requires exactly one channel returned by mine=true. If the Google account has several channels, the UI will display an error rather than guess; an explicit selector is future work. Threads/VK connections still have the existing advanced manual-token flow.

Sources: https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps , https://www.postman.com/meta/instagram/folder/6raa77c/instagram-api-with-instagram-login , https://core.telegram.org/bots/api , https://core.telegram.org/api/links

## Rollout and testing

**Do not deploy solely because code is merged**. This requires a verified migration and bot/provider configuration.

1. Back up PostgreSQL **and** the persistent company_products Docker volume, especially _integrations/mail.key, OAuth metadata and _social_media uploads.
2. Run static lint / import smoke and `pytest -q tests/test_social_telegram.py tests/test_comments.py tests/test_content_calendar.py`. No GitHub Actions needed; prefer a Codespace or the existing SSH process.
3. Apply migration `alembic upgrade head` (revision 0006) to a staging or restored backup first. Verify rollback/restoration using that backup.
4. Configure publisher bot and domain, verify the proxy CONNECT chain, then update API + worker with the existing safe deployment script and health check.
5. In a private test channel, test admin-linking and rejection of a signed non-admin Telegram account; schedule text, JPG, MP4 and album and verify distinct message IDs per channel. Attach photo and MP4 to a future approved editorial Telegram slot, confirm per-channel auto-plan inclusion and rejection of cross-business attachments.
6. Turn on auto-plan only for a dedicated test company, approve a plan with one future Telegram slot and ensure exactly one scheduled post appears for each opted-in channel. Test a simulated timeout: mark the delivery as unknown and do not repost.
7. Test Google/Meta auth in provider test mode, token refresh and expired/denied access; verify that browser responses and logs contain no tokens.
8. If smoke checks fail, restore the prior containers/files/database snapshot. Do not downgrade the production database without a verified backup because dropping revision 0006 loses scheduling records.

Current branch is **code only**, not tested against real Telegram, Meta or Google accounts and not published to production.

## VK ID, Meta Threads, Dzen, vc.ru

**VK/VK Video:** VK ID OAuth PKCE, account owner specifies a numeric community ID
before redirect; existing VK adapter checks group manager role, permissions to
read comments and write replies. User login alone does not grant these rights.
Configure SOCIAL_VK_CLIENT_ID and these redirects:
- https://growth.servgo.ru/social/oauth/vk/callback
- https://growth.servgo.ru/social/oauth/vk_video/callback

**Threads:** configure SOCIAL_THREADS_CLIENT_ID and
SOCIAL_THREADS_CLIENT_SECRET for a dedicated Meta Threads app.
Redirect: https://growth.servgo.ru/social/oauth/threads/callback
Scope: threads_basic, threads_content_publish, threads_read_replies,
threads_manage_replies. The existing comment adapter checks the obtained token
and account ID after OAuth; long-lived tokens/refresh metadata are encrypted.

**Dzen:** link a public Telegram channel with official @zen_sync_bot:
https://t.me/zen_sync_bot . ServGo schedules posts in that Telegram channel;
the Dzen sync is an external feature controlled by Dzen, and ServGo cannot
assert that publication to Dzen happened based only on a successful Telegram
post. Comments remain manual drafts. Dzen's own author channel explains the bot:
https://t.me/s/zenforcreators?after=1289

**vc.ru:** the Social Networks page displays platform-specific content with
Copy and Open vc.ru actions. Comments and publications remain manually
performed by the owner. No public writing/comment API for general third-party
access was confirmed, and no private browser endpoints or cookies are used.
Official editorial introduction: https://vc.ru/team/28520-how-to-vcru

## Proxy checking

The production Compose config already references the proxy endpoint
188.120.254.223:8891 for overseas platforms, via COMMENT_PLATFORM_PROXY_URL.
This is **configuration**, not a successful live connectivity test.
VK comment methods and VK ID auth are direct. Threads/Instagram/YouTube/Telegram
use overseas proxy routing.

The Social Networks UI has an authenticated 'Проверить подключение' action,
which checks a fixed list of HTTPS API destinations through the proxy from
the **API container** without user tokens, and reports response codes.
It does NOT verify OAuth scopes, approval for third-party accounts, content
publishing rights, or connectivity from the separate worker container.

Before production deploy:
1. Run pytest on test_social_extended_oauth.py and test_social_proxy_check.py
   together with test_social_oauth.py, test_comments.py and test_social_telegram.py.
2. Test VK/Threads OAuth redirect URI, consent grants, token refresh and actual
   comment reads/replies on owner-authorized test accounts.
3. Test proxy from API and worker containers; verify the overseas proxy chain.
4. Configure domains, secrets and provider app approval. Then stage migration
   0006 on a database backup and deploy with health check/rollback.

No GitHub Actions, live proxy test, OAuth-provider test or production
deployment has been performed during this change.

## 2026-10-11: provider ownership and vc.ru limitations — verified

Dzen was acquired by VK from Yandex on **2022-09-12**. The existence of
Yandex ID OAuth means that applications may authenticate Yandex users; it does
**not** supply Dzen content/comment API permissions. Do not show a misleading
"Connect Dzen via Yandex OAuth" button as though Dzen publishing were unlocked.
Current supported Dzen publishing bridge: public Telegram channel connected to
@zen_sync_bot. This bridge is external; ServGo cannot prove Dzen delivery.
Ownership source: https://www.interfax.ru/business/1046274
Yandex OAuth API rights: https://yandex.ru/dev/id/doc/ru/

**Important new finding for vc.ru:** the vc.ru editorial team runs an official
Telegram-to-vc.ru publication service. It can carry media and text; vc.ru may
generate a headline, sends a notification, and supports edit/delete via its own
bot. However participation requires **editorial pre-moderation**, and the
official announcement explicitly says **brand channels are not connected**.
Advertising-labelled posts are not imported. Thus this is not a general-purpose
business/brand integration and must **never** be advertised as such.

For an eligible personal/professional channel, the owner sends the Telegram
channel URL **and** vc.ru blog URL to telegram@vc.ru, then follows the
acceptance instructions from vc.ru. The bot is @vcrepost_bot.
Source: https://vc.ru/about-tg-bot
Bot: https://t.me/vcrepost_bot

The Social Networks UI and comment-platform guide now disclose these
restrictions and link to the official application route. The existing
prepared-content copy/editor workflow remains available for brand businesses,
and replies to vc.ru comments are manual. No private/unofficial publication
endpoints or browser automation should be introduced. No provider
connectivity or production deployment has been verified.
