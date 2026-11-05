-- Apply before starting the new API and expiry worker. Safe to repeat.
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS prepayment_requested_at TIMESTAMP NULL;
