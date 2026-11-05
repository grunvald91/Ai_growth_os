-- ServGo: business setting "several services in one booking" (admin → Услуги).
-- Safe additive schema-only migration; apply separately to production after review.

-- lock_timeout: не держать очередь запросов к businesses, если таблицу держит долгая транзакция.
SET lock_timeout = '3s';

ALTER TABLE businesses
  ADD COLUMN IF NOT EXISTS allow_multiple_services BOOLEAN NOT NULL DEFAULT FALSE;
