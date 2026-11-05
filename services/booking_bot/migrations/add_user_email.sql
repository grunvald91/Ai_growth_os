-- Optional email for booking notifications.
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS email VARCHAR(320);

CREATE INDEX IF NOT EXISTS ix_users_email
  ON users (email)
  WHERE email IS NOT NULL;
