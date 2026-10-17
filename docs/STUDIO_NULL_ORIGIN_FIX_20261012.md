# Studio form Origin=null: server correction, 2026-10-12

Verified 2026-10-12 18:48:16 UTC (21:48 Moscow).
Reported error IDs: `f4c323c5ef21` and `7bf029881d59`.

## Evidence and cause

Both error IDs were found in the running API's `studio_origin_rejected` logs.
Both were POSTs to the company's whiteboard form and recorded:
- configured origin: `https://growth.servgo.ru`;
- submitted Origin: literal `null`;
- Referer origin: missing;
- Sec-Fetch-Site: `same-origin`;
- rejection reason: `Cross-origin action rejected`.

The live container's STUDIO_PUBLIC_ORIGIN is already correct. The current
application baseline is commit `f1118b392c38d9ec29335939082783bc69c85b71`.

Nginx's Growth Studio configuration had two
`add_header Referrer-Policy no-referrer always;` directives. Public responses
for both the Create page and whiteboard page returned `Referrer-Policy: no-referrer`.

This explains the observed browser form headers: the
[WHATWG Fetch Origin-header algorithm](https://fetch.spec.whatwg.org/#append-a-request-origin-header)
sets the serialized Origin to null for non-CORS, non-GET/HEAD requests under
no-referrer. Under same-origin it does so when the origins differ.
[W3C same-origin policy](https://www.w3.org/TR/referrer-policy/#referrer-policy-same-origin)
also withholds Referer from requests to other origins.

## Applied correction

In `/etc/nginx/sites-available/growth-servgo`, changed exactly two directives:

```diff
-add_header Referrer-Policy no-referrer always;
+add_header Referrer-Policy same-origin always;
```

The original configuration is privately backed up under
`/opt/ai_growth_os/origin-fixes/20261012-null-origin/growth-servgo.before`.
Nginx configuration validation passed, Nginx was reloaded, and subsequent
verification returned:
- Nginx active; nginx -t exit 0.
- Public whiteboard page: HTTP 401 without credentials, Referrer-Policy same-origin.
- Public Create page: HTTP 401 without credentials, Referrer-Policy same-origin.
- API health: `{"status":"ok"}`.

Authentication and the application Origin checks were not changed.
No API/container/env replacement or paid generation was needed.

## Verification and limitation

Eight checks directly against the current container's real production Origin
dependency passed:
1. Exact local Origin accepted.
2. Local Referer fallback, with no Origin header, accepted.
3. Literal null Origin rejected with 403.
4. Foreign Origin rejected with 403.
5. Cross-site request rejected with 403 even with a local Origin.
6. Missing Origin and Referer rejected with 403.
7. Null Origin rejected even with local Referer.
8. Foreign Origin rejected even with local Referer.

These checks do not create jobs or invoke models. Results are saved privately as
`result-final.json` and `csrf-checks.json` in the correction directory.

The owner's actual browser submission after correction has not been observed;
its success cannot yet be confirmed. A page already loaded under no-referrer
must be reloaded or reopened before sending the form again. A normal fresh GET
receives the corrected policy. Do not automatically repeat a paid approval.

GitHub Actions launched for this correction: 0. This report is documentation-only.
