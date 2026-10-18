# Production vs GitHub — audit 2026-10-18 (AI Growth OS)

Read-only audit of `155.212.162.47` (workflow `server-sync-audit` in `booking_bot`, run 39147676931).
Server files were hashed and compared with `main`, every branch tip and the full history.

## Running code (`/app` in `ai-growth-os-api-1`, image `servgo-growth:55e30d924009`)

- 165 files identical to `main`.
- 8 files differ from `main` and match branch `ccr-76da7f95-ladnb7` exactly (PR #142: whiteboard
  auto-resume, error log, region fallback): `app/api/whiteboard.py`, `app/content/whiteboard/{jobs,layout,
  pipeline,runtime,worker}.py`, `app/core/config.py`, `pyproject.toml`. Merging PR #142 makes `main` equal
  to production.
- Nothing on the server is missing from GitHub.

## Deploy configuration (`/opt/ai_growth_os/deploy/deploy.json`, copied here)

- Base image `servgo-growth-release:20261016`.
- `overlay_env`: `AGENT_CRITIC_ENABLED=true`, `MODEL_ROUTER_ENABLED=true` and critic limits. These flags are
  applied by `deploy_growth.py` on every deploy, so a redeploy keeps the critic and router on.
- No secrets in this file; secrets stay in `/opt/ai_growth_os/.env` on the server.

## Ops added here

- `nginx/growth-servgo.conf`: the enabled site for growth.servgo.ru.
- `deploy/ops/20261015/`: the 2026-10-15 release script and the script that enabled the router (Opus for CMO
  and Research).
- `deploy/ops/20261016/`: the nginx tuning script and the compose overlay with critic and router on.

Not added: `/opt/ai_growth_os/whiteboard-releases/*` and `releases/*` (one-off pilot build/QA scripts and
old release copies), `servgo-company-profile.before.json` (company data backup).
