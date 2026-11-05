-- Migration: Add cancellation policy photo and require receipt fields to businesses
-- Date: 2026-02-20

ALTER TABLE businesses ADD COLUMN cancellation_policy_photo TEXT;
ALTER TABLE businesses ADD COLUMN require_receipt_for_prepayment BOOLEAN NOT NULL DEFAULT 0;
