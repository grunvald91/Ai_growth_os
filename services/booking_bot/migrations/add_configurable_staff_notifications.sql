-- Configurable owner/master reminders and notification delivery audit.
-- Immediate booking-created/confirmed events remain mandatory and are not
-- controlled by these reminder settings.

ALTER TABLE businesses
  ADD COLUMN IF NOT EXISTS owner_reminders_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS owner_reminder_minutes TEXT NOT NULL DEFAULT '[1440, 120]',
  ADD COLUMN IF NOT EXISTS master_reminders_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS master_reminder_minutes TEXT NOT NULL DEFAULT '[1440, 120]';

CREATE TABLE IF NOT EXISTS notification_deliveries (
  id SERIAL PRIMARY KEY,
  dedupe_key VARCHAR(255) NOT NULL UNIQUE,
  booking_id INTEGER NULL REFERENCES bookings(id) ON DELETE CASCADE,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  audience VARCHAR(32) NOT NULL,
  event_kind VARCHAR(64) NOT NULL,
  offset_minutes INTEGER NULL,
  telegram_sent INTEGER NOT NULL DEFAULT 0,
  webpush_sent INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_notification_deliveries_booking_id
  ON notification_deliveries (booking_id);

CREATE INDEX IF NOT EXISTS ix_notification_deliveries_business_id
  ON notification_deliveries (business_id);

CREATE INDEX IF NOT EXISTS ix_notification_deliveries_dedupe_key
  ON notification_deliveries (dedupe_key);
