# SEO factory — release notes 2026-10-28

Deploy with the guarded manual workflow `deploy-growth.yml` (ships `app/`, `alembic/versions/`, `pyproject.toml`, `products/`).
**Historical note:** this original October 28 release note predates the opt-in guard.
Current branch requires applying `alembic/versions/0011_seo_factory_opt_in.py`
BEFORE deploying the new app and worker, and needs the BookingBot HTML routes/nginx integration.
No live deployment has been performed as part of the October 31 work.

Order of rollout:
1. Server nginx for growth.servgo.ru: `deploy/ops/20261028/nginx_public_blog.py` (opens only the read-only `/public/blog/`).
   Check: `curl https://growth.servgo.ru/public/blog/servgo/articles` returns JSON.
2. Make `servgo.ru/blog` reach the BookingBot blog (not the SPA shell).
3. Deploy the BookingBot blog branch `seo-blog`. It redirects promo.servgo.ru/blog to servgo.ru/blog, so step 2 comes first.

Settings: `SEO_ARTICLES_PER_DAY` (default 3, per company, Moscow day). Critic must stay on (`AGENT_CRITIC_ENABLED=true`):
**Current behavior:** SEO articles with a clean critic verdict are automatically published.
There is no mandatory owner approval and no version/hash mismatch gate.
One factory launch queues no more than three articles. Articles failing critic/static checks remain held for optional owner review.
See `docs/design/SEO_EDITORIAL_PRINCIPLES_20261031.md`.

Rollback: redeploy the previous app set; the factory only adds tasks and artifacts, published rows stay valid.
