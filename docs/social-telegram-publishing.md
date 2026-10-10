# Growth Studio · Социальные сети: Telegram + OAuth

Development branch: `feature/telegram-multichannel-oauth-20261010`.

## User experience

- From **Growth Studio → Социальные сети → Каналы и публикации**, choose **Добавить бота в канал**. No BotFather token is requested from the customer.
- Telegram opens the official channel selection dialog; give the shared publishing bot the `post_messages` administrator right.
- Enter public `@channel` or private channel numeric ID (`-100…`) in ServGo.
- In the pending connection card, sign in using the official Telegram Login Widget as **an administrator of that channel**. ServGo verifies the signed login data with HMAC and Telegram getChatMember for both the operator and the bot. It does not rely on a public description code.
- Repeat for as many channels as needed. Each company has its own channels and scheduled jobs; chat IDs cannot be claimed by another company.
- Select several connected channels, enter text and optionally upload JPG/PNG/MP4 (up to 10 files, maximum 20 MiB each), select a local date/time and schedule. Statuses and Telegram message IDs are tracked **per channel**.
- Turn on **Автопубликация по утверждённому контент-плану** for each channel independently. The worker creates text posts only for future Telegram slots in owner-approved active plans, once the content-factory Telegram adaptation is ready. The job is created once per plan-slot/channel; the unique constraint prevents duplicates. Photos/videos still require explicit scheduling with actual uploaded media. This does not pretend that a script/storyboard is a video.
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
5. In a private test channel, test admin-linking and rejection of a signed non-admin Telegram account; schedule text, JPG, MP4 and album and verify distinct message IDs per channel.
6. Turn on auto-plan only for a dedicated test company, approve a plan with one future Telegram slot and ensure exactly one scheduled post appears for each opted-in channel. Test a simulated timeout: mark the delivery as unknown and do not repost.
7. Test Google/Meta auth in provider test mode, token refresh and expired/denied access; verify that browser responses and logs contain no tokens.
8. If smoke checks fail, restore the prior containers/files/database snapshot. Do not downgrade the production database without a verified backup because dropping revision 0006 loses scheduling records.

Current branch is **code only**, not tested against real Telegram, Meta or Google accounts and not published to production.
