-- Owner switch: may the venue's administrators open the marketing studio (growth.servgo.ru).
-- Off by default; only owners and co-owners can change it. Safe to run twice.
-- Rollback: ALTER TABLE businesses DROP COLUMN IF EXISTS studio_admin_access;
ALTER TABLE businesses
  ADD COLUMN IF NOT EXISTS studio_admin_access BOOLEAN NOT NULL DEFAULT FALSE;
