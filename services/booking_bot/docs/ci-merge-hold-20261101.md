# 2026-11-01 — PR #109 source merge / CI consolidation

The existing `servgo-integrated-pwa-release-gates.md` still governs real
production release. **Merging the candidate into `main` does not certify
production readiness and does not apply SQL migrations or enable gateway flags.**

## GitHub Actions budget

- One `pwa-ci.yml` pull-request workflow now builds the frontend once, runs
  the frontend/SEO/backend checks, then uses the same `dist` for isolated
  Chromium PWA/offline/SSO preselection smoke.
- The redundant `servgo-offline-browser.yml` workflow is removed.
- Per-PR concurrency cancels older `pwa-ci` runs on the same pull request.
  Explicit `workflow_dispatch` runs are not cancelled by new PR commits.
- Backend-release workflow retains its read-only branch preflight and
  `cancel-in-progress: false`; production deployment must never be
  cancelled mid-flight by a PR CI run.
- All changes to scripts and workflow YAML are reviewed as one commit to
  avoid extra CI invocations.

## One-time main merge hold

The production deploy workflow `deploy.yml` normally runs on any `main`
push touching `web/frontend/**`. The **merge commit of PR #109 only** is
named with `[defer-servgo-frontend-release]`, and the deploy job skips
that marked push. Regular unmarked `main` pushes and manual
`workflow_dispatch` still work. Never reuse the marker for unrelated
production updates.

The merged source must be shipped separately in the right order: backup,
BookingBot SEO attribution SQL migration, Growth Alembic 0011, matching
backend, Growth public blog and nginx config, then verified frontend build.
Run real HTTPS smoke; keep `growth.servgo.ru` and `mail.servgo.ru` intact.
Do not enable same-origin gateway flags without the stage E2E and operator
verification documented in the release gates.
