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
