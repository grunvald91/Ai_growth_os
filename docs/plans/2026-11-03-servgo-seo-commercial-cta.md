# P0 #240 — ServGo SEO article → business inquiry

This work applies **only to the platform's editorial blog** on `servgo.ru/blog`.
It does not redefine tenant booking-page conversions or claim that platform
signups / paid customers occurred.

## Real conversion path

1. A published article's CTA points to `/blog/cta/<published-slug>`.
   Optional `module` is one enum from platform/booking/growth/mail/shifts/seo.
   The slug must currently be in the public article collection.
2. The redirect writes an **anonymous** click event (slug, module, timestamp,
   random 128-bit event id) in `servgo_seo_commercial_clicks`. It stores no
   IP, user agent, name, phone or email. If persistence fails, the redirect
   still works but omits the attribution token; no phantom event is reported.
3. The target is the existing ServGo platform enquiry form:
   `https://servgo.ru/?utm_source=servgo_blog&utm_medium=seo&...`,
   with article slug in `utm_content` and click id in `utm_term`.
   Form opens automatically and offers all five modules as selectable
   interests, all via the **same real contact form**, not imaginary product
   landing pages. The click module and selected interest are not conflated.
4. The visitor explicitly consents to using their contacts to answer the
   enquiry. The public handler rejects SEO submissions without consent.
5. The original `marketing_leads` table receives the enquiry. The server
   verifies the click id, matching article and clicked module against its
   own event table before attributing the lead. A pending request becomes
   `received` **only after** the business request was saved and at least
   one required notification channel succeeded.
6. An owner/admin with authenticated ServGo access can POST to the existing
   `/api/landing/connect-request?seo=report&days=90` endpoint. It returns
   only per-article and per-clicked-module aggregates, without PII:
   `cta_clicks` and `accepted_leads`. This is intentionally a
   business lead count, **not** the customer service-booking count.

## Constraints and limitations

- The operator's current landing request/SMTP/Telegram route is reused,
  rather than creating another lead CRM or a new acquiring service.
- This change cannot establish that a customer was onboarded, paid or became
  an active tenant. A `received` lead is **only** an accepted request.
- Anonymous CTA requests may include bots or repeat clicks; do not label
  this as unique humans. CTA endpoints are marked noindex/nofollow and
  disallowed in robots.txt.
- Only already-published ServGo articles have live CTA URLs. Never publish
  previously rejected or test SEO articles to create conversion statistics.
- Lead status `pending`, `test`, `demo`, `new` or unverified source is
  excluded from attributed accepted-lead totals; old legacy leads are not
  silently backfilled.
- GDPR/Russian data-processing compliance and full account-signup completion
  measurement require a separate legal and product review. The checkbox
  establishes an explicit affirmative action for this enquiry only.
- Check that production nginx routes `/blog/cta/*` to the same webapp, that
  `/api/landing/connect-request` is accessible from the ServGo root,
  that `servgo_seo_commercial_clicks` exists, and that both contact
  notification and the final status transition succeed **before** marking
  #240 fully accepted. Do not create a real customer's enquiry as a test.
