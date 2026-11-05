-- Вход через Яндекс ID: связь аккаунтов, одноразовые билеты, дата рождения.
-- Только добавляет; существующие данные не меняются. Откат: DROP TABLE yandex_tickets, yandex_identities; ALTER TABLE users DROP COLUMN birthday.
BEGIN;
ALTER TABLE users ADD COLUMN IF NOT EXISTS birthday VARCHAR(10);
CREATE TABLE IF NOT EXISTS yandex_identities (
    yandex_id VARCHAR(32) PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    login VARCHAR(255),
    email VARCHAR(320),
    phone VARCHAR(20),
    created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE TABLE IF NOT EXISTS yandex_tickets (
    id VARCHAR(64) PRIMARY KEY,
    payload TEXT NOT NULL,
    link_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    code_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    code_hash VARCHAR(64),
    code_sent_at TIMESTAMP,
    sends INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    expires_at TIMESTAMP NOT NULL,
    consumed BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_yandex_identities_email ON yandex_identities(email);
CREATE INDEX IF NOT EXISTS ix_yandex_tickets_expires_at ON yandex_tickets(expires_at);
COMMIT;
