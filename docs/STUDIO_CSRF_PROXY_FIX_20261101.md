# Growth Studio: CSRF/HTTPS behind nginx (2026-11-01)

Affected user flow: new whiteboard video, POST from an HTTPS Studio page.
Observed 403 error ID from browser: `402e780f54cf`.

The public Studio responses were checked on 2026-11-01 via one read-only GitHub
Actions run: `growth.servgo.ru` returned `Referrer-Policy: same-origin`.
Therefore the old nginx `no-referrer` problem is *not* demonstrated here.

The existing CSRF check has a reverse-proxy mismatch when the dual-host gateway
flag is on: it requires `request.url.scheme == https` although the TLS
connection terminates at nginx and Docker/Uvicorn can see an internal HTTP hop.
The nginx Growth site configuration explicitly overwrites
`X-Forwarded-Proto: $scheme`. This patch accepts exact `https` in that
header as the public HTTPS scheme, while **also** requiring:

- Submitted Origin/Referer equals the configured legacy origin, or equals
  `https://servgo.ru` on the canonical host when the gateway is enabled.
- Actual Host matches the corresponding host. A cross-host origin remains denied.
- `Sec-Fetch-Site: cross-site` remains denied.
- Literal `Origin: null`, missing Origin/Referer and non-HTTPS proxy claims
  remain denied.

No authentication, scope, spending approval, scientific check or model invocation
is bypassed. No broad cross-origin allowlist is added.

**Evidence limit:** The specific origin error ID has not been matched to private
production API logs, so this is an identified code-level bug, not a confirmed
trace of that individual failed submission.

The immediate verification is a no-spend browser form submission after deploying
this fix. The previous failed 403 request created no paid job.
