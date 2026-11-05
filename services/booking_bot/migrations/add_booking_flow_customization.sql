-- ServGo: customizable booking flow and primary resource photo
-- Safe schema-only migration; apply separately to production after review.

ALTER TABLE businesses
  ADD COLUMN IF NOT EXISTS booking_labels TEXT NULL,
  ADD COLUMN IF NOT EXISTS booking_flow_order VARCHAR(32) NOT NULL DEFAULT 'service_first';

ALTER TABLE employees
  ADD COLUMN IF NOT EXISTS primary_photo TEXT NULL;
