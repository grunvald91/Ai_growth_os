# Release 2026-10-16: production state

Server: `/opt/servgo-releases/20261016/`. The scripts and the ServGo patch are in `scripts/releases/20261016/`.

## AI Growth OS (growth.servgo.ru)

- Image `servgo-growth-release:20261016`, built from branch `fix/navigation-modules-20261008`
  (contains `release/unified-20261015`).
- Content package assembly no longer fails when a ready video brief lacks on-screen text.
  The stage falls back instead, and assembly errors are recorded on the last run.
- Growth Studio navigation links to the ServGo mail console (`https://servgo.ru/mail`).
- The content critic (article production, quick posts, creative briefs) checks readability, hook,
  persuasion, a single CTA and channel fit. Unsupported claims stay critical.
- Flags and limits are set in the compose overlay written by `deploy_growth_20261016.py up true true`:
  `AGENT_CRITIC_ENABLED=true`, `MODEL_ROUTER_ENABLED=true`, `AGENT_CRITIC_MAX_CONTEXT_CHARS=200000`,
  `AGENT_CRITIC_REVIEW_TIMEOUT_SECONDS=240`, `AGENT_CRITIC_REVISION_TIMEOUT_SECONDS=300`.
- Router config revision 2 (`set_content_critic.py`):
  - Content roles use the critic order `kimi-k3` → `claude-sonnet-5-5` → `gpt-6-luna`.
  - Main routes of all dashboard roles are pinned to the previous routes.
  - Known gap: roles outside the dashboard list (quick post, rewrite, creative strategist,
    editorial planner) are auto-routed. Without model prices, the automatic order breaks ties by
    model name, so they now start with `claude-sonnet-5-5`. Fix pending the owner's decision:
    enter ClaudeHub prices or pin those roles.
- Rollback:
  - Image: `python3 deploy_growth_20261016.py rollback` (back to the 20261015 set).
  - Router config: restore `.model-router-config.before-critic.json`.

## ServGo (servgo.ru, mail.servgo.ru)

Applied directly on the server: `/opt/booking_bot` is not a git checkout, and the Codespace token has
no access to `grunvald91/booking_bot`. Patch: `servgo-booking-mail-entry-20261016.patch`.

- The «Почта» button is shown in the admin panel only. It opens `/mail`, which exchanges the ServGo
  login for a one-time mail.servgo.ru ticket. Access is still checked on the server.
- On mail.servgo.ru without a session, the user gets a «Войти через ServGo» page instead of a bare 401.
- Backups: `/opt/servgo-releases/20261016/mail-entry/backup`, plus `web/frontend/dist.bak_20261016`.
