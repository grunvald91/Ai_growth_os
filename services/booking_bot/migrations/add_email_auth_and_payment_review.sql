BEGIN;
CREATE TABLE IF NOT EXISTS email_identities (
    email VARCHAR(320) PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    password_hash VARCHAR(255) NOT NULL, session_version INTEGER NOT NULL DEFAULT 1,
    verified_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE TABLE IF NOT EXISTS email_challenges (
    id VARCHAR(36) PRIMARY KEY, email VARCHAR(320) NOT NULL, purpose VARCHAR(16) NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, full_name VARCHAR(255) NOT NULL DEFAULT '',
    password_hash VARCHAR(255) NOT NULL, code_hash VARCHAR(64) NOT NULL, expires_at TIMESTAMP NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, consumed BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_email_challenges_email ON email_challenges(email);
CREATE TABLE IF NOT EXISTS auth_rate_buckets (
    id VARCHAR(64) PRIMARY KEY, started_at TIMESTAMP NOT NULL, count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS booking_receipts (
    id SERIAL PRIMARY KEY, booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    mime_type VARCHAR(32) NOT NULL, content BYTEA NOT NULL, size INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS ix_booking_receipts_booking_id ON booking_receipts(booking_id);
CREATE TABLE IF NOT EXISTS prepayment_reviews (
    id SERIAL PRIMARY KEY, booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    reviewer_id INTEGER NOT NULL REFERENCES users(id), decision VARCHAR(16) NOT NULL,
    booking_version INTEGER NOT NULL, reviewed_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    CONSTRAINT uq_prepayment_review_version UNIQUE(booking_id, booking_version)
);
CREATE INDEX IF NOT EXISTS ix_prepayment_reviews_booking_id ON prepayment_reviews(booking_id);
COMMIT;
