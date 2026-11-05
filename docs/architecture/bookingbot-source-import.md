# BookingBot source import — staging only

Source: https://github.com/grunvald91/booking_bot
Source ref: main at `f1529d1bf4960b5e0ad282f05eb9f651faf1cb36`.
Target: `services/booking_bot/` on the integration branch.

This imports 599 exact Git-tracked files via a history-preserving subtree merge, plus one separately reviewed production-only entrypoint. It is not a production deploy or database migration.
The original BookingBot repository and deployed service remain authoritative for production.
No existing runtime path, workflow, DNS, database, or SSH configuration is switched by this import.

Read-only production parity audit: https://github.com/grunvald91/booking_bot/actions/runs/42034462917
Classification: https://github.com/grunvald91/booking_bot/actions/runs/42037871076

Of the six differences between server source and main, five are exact earlier historical Git revisions.
`web/api/routes/studio_sso.py` differs by moved functions and comments; a direct AST comparison confirmed semantic equivalence (True). Keep the current GitHub main version.
The server-only `webapp/promo_standalone.py` was confirmed active (one running process and two systemd references). It was captured by a pinned-host, secret-scanned read-only SSH audit into `booking_bot:audit/server-promo-staging-20261105`, then added **only to this integration branch** at `services/booking_bot/webapp/promo_standalone.py`. Server SHA-256: `991feeab3de80d29ef88a8e39e3026b8cc199eaecb65aea2a822fe57a34d0eb0`. It is absent from the source repository main and must be reconciled before changing production deployment ownership.

Before merging to Growth main, check CI impact and root-level import scope, ensure deployment paths remain separate, preserve the live promo process, validate database permissions and update the deployment scripts as a separate explicitly authorized change.
Do not change booking_bot main or production in this source-import task. No service, database, domain or live configuration has been modified.

Production-only source capture: https://github.com/grunvald91/booking_bot/actions/runs/42039217502

AST and live-service check: https://github.com/grunvald91/booking_bot/actions/runs/42037871076

## CI/CD boundary after the source import

- The root `.github/workflows/ci.yml` remains the required GitHub status `CI`. Its monorepo scope check skips expensive Growth tests when a change affects only `services/booking_bot/` or the explicitly listed import/CI bookkeeping files. Growth changes still run the original complete test suite.
- `.github/workflows/bookingbot-module-ci.yml` runs BookingBot Python syntax/regression checks and the React/Vite frontend build for booking code changes. The first imported source tree deliberately preserves historical whitespace; later edits are whitespace-checked against the existing BookingBot tree.
- `.github/workflows/deploy-growth.yml` has an independent impact job **outside** the production environment. A Booking-only or import-only commit does not start the Growth deployment job. If a newer Booking-only commit supersedes the latest tested Growth commit, the freshness gate still allows the older Growth code to deploy, preventing a missed release.
- The original `booking_bot` repository remains **the BookingBot production deployment source** until an explicit, separately tested cutover. GitHub workflow files inside `services/booking_bot/.github/workflows/` are preserved source history, not active monorepo workflows. **Do not assume a code change in this directory deploys BookingBot automatically yet.** No BookingBot production deploy keys, databases or servers are switched in this merge.
- An operator must verify the server’s SSH credentials/target path, reconcile the existing six source differences and the active promo service, and stage an actual BookingBot release from the monorepo before enabling an automatic booking deployment. There must not be two independent automatic deployment writers to `/opt/booking_bot`.
- Tenant isolation between BookingBot `business_id` and Growth `product_id` remains enforced by separate existing services and the canonical SaaS ADR. Sharing a Git repository does not merge databases or weaken access controls.

### Verification

- Main-vs-production source audit: https://github.com/grunvald91/booking_bot/actions/runs/42034462917
- Classification / semantic equivalence: https://github.com/grunvald91/booking_bot/actions/runs/42037871076
- Verified subtree import: https://github.com/grunvald91/Ai_growth_os/actions/runs/42038408510
- BookingBot scoped tests: https://github.com/grunvald91/Ai_growth_os/actions/runs/42057981272
- CI and release routing: `scripts/ci/monorepo_scope.py` (tested source classifier).
