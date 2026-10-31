# SEO factory — release notes 2026-10-28

Deploy with the guarded manual workflow `deploy-growth.yml` (ships `app/`, `alembic/versions/`, `pyproject.toml`, `products/`).
**Historical note:** this original October 28 release note predates the opt-in guard.\nCurrent branch requires applying `alembic/versions/0011_seo_factory_opt_in.py`\nBEFORE deploying the new app and worker, and needs the BookingBot HTML routes/nginx integration.\nNo live deployment has been performed as part of the October 31 work.

Order of rollout:
1. Server nginx for growth.servgo.ru: `deploy/ops/20261028/nginx_public_blog.py` (opens only the read-only `/public/blog/`).
   Check: `curl https://growth.servgo.ru/public/blog/servgo/articles` returns JSON.
2. Make `servgo.ru/blog` reach the BookingBot blog (not the SPA shell).
3. Deploy the BookingBot blog branch `seo-blog`. It redirects promo.servgo.ru/blog to servgo.ru/blog, so step 2 comes first.

Settings: `SEO_ARTICLES_PER_DAY` (default 3, per company, Moscow day). Critic must stay on (`AGENT_CRITIC_ENABLED=true`):
**Current behavior:** every SEO article requires a clean critic review **and explicit owner approval**.\nOne factory launch queues no more than three articles; no articles auto-publish.\nA failed or stale critic review blocks publication even after a human approval until it is corrected and re-reviewed.\nSee `docs/design/SEO_EDITORIAL_PRINCIPLES_20261031.md`.

Rollback: redeploy the previous app set; the factory only adds tasks and artifacts, published rows stay valid.
