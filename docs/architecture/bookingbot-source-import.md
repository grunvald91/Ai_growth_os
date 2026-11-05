# BookingBot source import — staging only

Source: https://github.com/grunvald91/booking_bot
Source ref: main at `f1529d1bf4960b5e0ad282f05eb9f651faf1cb36`.
Target: `services/booking_bot/` on the integration branch.

This is an exact tracked Git source import, not a production deploy or database migration.
The original BookingBot repository and deployed service remain authoritative for production.
No existing runtime path, workflow, DNS, database, or SSH configuration is switched by this import.

Read-only production parity audit: https://github.com/grunvald91/booking_bot/actions/runs/42034462917
Classification: https://github.com/grunvald91/booking_bot/actions/runs/42037871076

Of the six differences between server source and main, five are exact earlier historical Git revisions.
`web/api/routes/studio_sso.py` on the server has a moved-function layout and comments.
An untracked server-only file `webapp/promo_standalone.py` exists and is not included here.

Before any future merge to Growth main, check CI impact, separate deployment paths, database permissions, and the server-only file.
Do not change booking_bot main or production until the server-only file and current UI/backend differences are reconciled.
