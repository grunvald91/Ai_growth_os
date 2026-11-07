-- VK ID unified login for ServGo / Studio. Additive, no existing identity is changed.
BEGIN;
CREATE TABLE IF NOT EXISTS vk_identities (
    vk_user_id VARCHAR(40) PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    display_name VARCHAR(255),
    created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE TABLE IF NOT EXISTS vk_login_tickets (
    id VARCHAR(64) PRIMARY KEY,
    payload TEXT NOT NULL,
    link_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMP NOT NULL,
    consumed BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_vk_login_tickets_expires_at ON vk_login_tickets(expires_at);
COMMIT;
