# Growth OS: Opt-in serving under https://servgo.ru/studio

**31.10.2026. Review-only code, gateway is OFF by default. No nginx/deploy changes.**

This is the companion to [BookingBot PR #104](https://github.com/grunvald91/booking_bot/pull/104), whose server can issue the normal signed one-time SSO ticket directly to `https://servgo.ru/studio/sso`. The staging Nginx include in BookingBot proxies only the explicitly known Growth routes to an on-loopback Growth process. The canonical PWA path alias `/workspace/growth` redirects to `/studio` while keeping the browser on the SAME origin; no route-prefix rewrite of the Growth application is being claimed here.

## Compatibility contract: existing Growth links MUST keep working

The production URL **`https://growth.servgo.ru` remains a supported independent entry point**, including `/studio`, deeper pages, bookmarks and links previously shared with users. The canonical PWA URL `https://servgo.ru/studio` is **additional**, not a replacement. No permanent/temporary redirect from the old URL to the new one.

Do not remove the legacy nginx virtual host, change the existing session-cookie scope, switch `STUDIO_PUBLIC_ORIGIN` away from `https://growth.servgo.ru`, or schedule retirement without an explicit decision from the owner.

Both URLs point to the **same Growth backend and data**, with independently validated host-bound browser sessions. Existing logins, GET/POST/CSRF handling, projects and tasks on the old URL must remain functional after enabling the new gateway. The existing regression [test_old_growth_origin_keeps_working_on_old_host_during_rollout](../tests/test_servgo_same_origin_gateway.py) checks old-origin POST acceptance; authenticated deep links and previously active sessions still require real browser E2E.

## Configuration

Current default in production:
- `STUDIO_PUBLIC_ORIGIN=https://growth.servgo.ru`
- `STUDIO_SERVGO_GATEWAY_ENABLED=false`

During staging, after reverse-proxy integrity checks, set **only** `STUDIO_SERVGO_GATEWAY_ENABLED=true`. Keep existing `STUDIO_PUBLIC_ORIGIN` so in-flight sessions on `growth.servgo.ru` continue to accept writes.

The new validation allows the second origin `https://servgo.ru` only when:
1. the request arrived at `https://servgo.ru` itself (`Host` and trusted forwarded scheme),
2. submitted `Origin` (or `Referer` fallback) matches that exact origin,
3. `Sec-Fetch-Site` is not `cross-site`,
4. the normal Growth auth, SSO, company scope and CSRF are all otherwise valid.

It **does not** allow a POST from `servgo.ru` to `growth.servgo.ru`, or vice versa. No new roles, public APIs or CORS permissions are created. HTTP is explicitly refused for the new origin. An absent/invalid primary origin still fails closed.

## Operator prerequisites

- Review the actual TLS nginx virtual host and verify that the Growth process is reachable exclusively through a trusted local network/loopback, and that only nginx supplies proxy headers. If Uvicorn/ASGI does not trust `X-Forwarded-Proto: https` from nginx, the new same-origin CSRF validation **correctly rejects** requests; fix trusted proxy setup, do not remove the HTTPS check.
- Release BookingBot SW v3 [PR #102](https://github.com/grunvald91/booking_bot/pull/102), whose navigation cache is network-only. The existing SW must never cache Growth HTML, SSO tickets or authenticated documents.
- Pass the full Growth test suite and `tests/test_servgo_same_origin_gateway.py`. In staging test same-origin and cross-origin POST with valid/invalid login, reload, SSO cookie, multiple tenants, role revocation and old `growth.servgo.ru/studio` / product / task deep links. Retest an existing browser session and a fresh login after enabling the gateway; old URL must not redirect to canonical URL.
- Observe the existing [Growth v4 review](https://github.com/grunvald91/Ai_growth_os/pull/181): the strict CSP on the AI-team screen currently blocks some v4 inline styles. This integration **does not** fix that visual issue.

## Rollback

- Turn off the BookingBot gateway issuer first, so NEW tickets return to `growth.servgo.ru`.
- Leave the existing Growth origin configured and working. Disable the secondary-origin flag only once canonical PWA sessions have expired or been drained; otherwise their POSTs will intentionally fail.
- Remove new nginx locations only after operational verification and `nginx -t`.

The Mail module remains a different origin. A future phase must address its `mail_owner` role, absolute /api paths, Origin checks and session cookies before same-origin publishing. Nothing here changes SMTP, database access, or mail permissions.
