-- Apply once to the booking_bot PostgreSQL database before enabling the CRM API.
-- Existing users and bookings remain unchanged; contact rows are created only on edits
-- or when an owner manually adds a contact.
CREATE TABLE IF NOT EXISTS crm_contacts (
    id BIGSERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255),
    phone VARCHAR(32),
    email VARCHAR(320),
    notes TEXT NOT NULL DEFAULT '',
    stage VARCHAR(20) NOT NULL DEFAULT 'new',
    follow_up_on DATE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    CONSTRAINT uq_crm_business_user UNIQUE (business_id, user_id),
    CONSTRAINT ck_crm_stage CHECK (stage IN ('new', 'active', 'vip', 'paused'))
);
CREATE INDEX IF NOT EXISTS ix_crm_contacts_business_id ON crm_contacts (business_id);
CREATE INDEX IF NOT EXISTS ix_crm_contacts_user_id ON crm_contacts (user_id);
CREATE INDEX IF NOT EXISTS ix_crm_contacts_followup ON crm_contacts (business_id, follow_up_on);
