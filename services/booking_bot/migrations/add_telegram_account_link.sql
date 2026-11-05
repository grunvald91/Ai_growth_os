BEGIN;
CREATE TABLE IF NOT EXISTS telegram_link_requests (
    id VARCHAR(36) PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE, email_session_version INTEGER NOT NULL,
    telegram_user_id INTEGER REFERENCES users(id), telegram_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at TIMESTAMP NOT NULL, consumed BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_telegram_link_requests_user_id ON telegram_link_requests(user_id);
CREATE TABLE IF NOT EXISTS user_account_merges (
    source_user_id INTEGER PRIMARY KEY REFERENCES users(id), target_user_id INTEGER NOT NULL REFERENCES users(id),
    merged_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    CHECK (source_user_id <> target_user_id)
);
CREATE INDEX IF NOT EXISTS ix_user_account_merges_target_user_id ON user_account_merges(target_user_id);
COMMIT;
