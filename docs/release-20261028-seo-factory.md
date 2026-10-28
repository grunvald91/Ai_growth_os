# SEO factory — release notes 2026-10-28

Deploy with the guarded manual workflow `deploy-growth.yml` (ships `app/`, `alembic/versions/`, `pyproject.toml`, `products/`).
No migrations. Restart the app and the team worker.

Order of rollout:
1. Server nginx for growth.servgo.ru: `deploy/ops/20261028/nginx_public_blog.py` (opens only the read-only `/public/blog/`).
   Check: `curl https://growth.servgo.ru/public/blog/servgo/articles` returns JSON.
2. Make `servgo.ru/blog` reach the BookingBot blog (not the SPA shell).
3. Deploy the BookingBot blog branch `seo-blog`. It redirects promo.servgo.ru/blog to servgo.ru/blog, so step 2 comes first.

Settings: `SEO_ARTICLES_PER_DAY` (default 3, per company, Moscow day). Critic must stay on (`AGENT_CRITIC_ENABLED=true`):
only articles with a clean critic review publish on their own; the rest wait for the owner and go live once approved.

Rollback: redeploy the previous app set; the factory only adds tasks and artifacts, published rows stay valid.
