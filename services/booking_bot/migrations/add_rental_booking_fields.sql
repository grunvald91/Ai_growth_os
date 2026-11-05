-- ServGo venue rental booking fields.
-- Idempotent additive migration required by web/api/rental.py.

ALTER TABLE bookings
  ADD COLUMN IF NOT EXISTS duration_minutes INTEGER NULL,
  ADD COLUMN IF NOT EXISTS estimated_price INTEGER NULL,
  ADD COLUMN IF NOT EXISTS price_requires_confirmation BOOLEAN NOT NULL DEFAULT FALSE;
