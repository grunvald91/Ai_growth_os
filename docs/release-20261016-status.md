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
- Router config revision 4 (`set_content_critic.py`, then `set_model_priorities.py`):
  - Models are ordered by owner priority inside a tier (1 = first): deepseek-v4.1-flash 1, kimi-2.7 2;
    grok-4.7 1, kimi-k3 2, claude-sonnet-5-5 3, gpt-6-luna 4; claude-opus-5-5 1. Priorities are
    editable on «ИИ-команда».
  - Content roles, quick posts, post rewrites and creative strategy use the critic order
    `kimi-k3` → `claude-sonnet-5-5` → `gpt-6-luna`.
  - First choices are unchanged: grok-4.7 for balanced roles, deepseek for economy roles,
    opus for CMO and research lead.
- `/studio/tune` (login `artur` only, via `STUDIO_TUNE_USERNAMES`): agent prompts (editable,
  overrides in `company_products/.prompt-overrides.json`), the package pipeline map, and per-task progress.
- «Добавить компанию» on the company selector, plus a company profile page (build and confirm the profile).
- Pending, needs the owner's OK (server nginx change was blocked): in `/etc/nginx/sites-enabled/growth-servgo`,
  add `location = /tune { return 302 /studio/tune; }` and raise `proxy_read_timeout` for `/studio` from 120s to 300s
  (building a company profile can take more than 2 minutes).
- Rollback:
  - Router: restore `.model-router-config.before-priorities.json` or `.model-router-config.before-critic.json`.
  - Image: `python3 deploy_growth_20261016.py rollback` (back to the 20261015 set).
  - Router config: restore `.model-router-config.before-critic.json`.

## ServGo (servgo.ru, mail.servgo.ru)

Applied directly on the server: `/opt/booking_bot` is not a git checkout, and the Codespace token has
no access to `grunvald91/booking_bot`. Patch: `servgo-booking-mail-entry-20261016.patch`.

- The «Почта» button is shown in the admin panel only. It opens `/mail`, which exchanges the ServGo
  login for a one-time mail.servgo.ru ticket. Access is still checked on the server.
- On mail.servgo.ru without a session, the user gets a «Войти через ServGo» page instead of a bare 401.
- Backups: `/opt/servgo-releases/20261016/mail-entry/backup`, plus `web/frontend/dist.bak_20261016`.
