-- ServGo: юридические реквизиты бизнеса и доказательства согласий.
-- Safe/re-runnable for PostgreSQL.

ALTER TABLE businesses
    ADD COLUMN IF NOT EXISTS legal_name VARCHAR(500),
    ADD COLUMN IF NOT EXISTS legal_inn VARCHAR(20),
    ADD COLUMN IF NOT EXISTS legal_ogrn VARCHAR(20),
    ADD COLUMN IF NOT EXISTS legal_address VARCHAR(1000),
    ADD COLUMN IF NOT EXISTS legal_email VARCHAR(255),
    ADD COLUMN IF NOT EXISTS legal_documents_enabled BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS personal_data_consent_version VARCHAR(64),
    ADD COLUMN IF NOT EXISTS personal_data_consent_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS booking_terms_version VARCHAR(64),
    ADD COLUMN IF NOT EXISTS booking_terms_accepted_at TIMESTAMP;
