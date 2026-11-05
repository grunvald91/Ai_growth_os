-- ServGo resource-overlap safety.
-- Backfills an immutable resource duration snapshot and adds a PostgreSQL
-- exclusion constraint so the database itself rejects overlapping active
-- bookings for the same employee/resource.

CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE bookings
  ADD COLUMN IF NOT EXISTS resource_duration_minutes INTEGER,
  ADD COLUMN IF NOT EXISTS occupies_resource BOOLEAN NOT NULL DEFAULT TRUE;

UPDATE bookings AS b
SET
  resource_duration_minutes = COALESCE(
    b.resource_duration_minutes,
    b.duration_minutes,
    s.duration_minutes,
    30
  ),
  occupies_resource = NOT COALESCE(s.is_additional, FALSE)
FROM services AS s
WHERE b.service_id = s.id
  AND (
    b.resource_duration_minutes IS NULL
    OR b.occupies_resource IS DISTINCT FROM NOT COALESCE(s.is_additional, FALSE)
  );

UPDATE bookings
SET resource_duration_minutes = COALESCE(resource_duration_minutes, duration_minutes, 30)
WHERE resource_duration_minutes IS NULL;

ALTER TABLE bookings
  ALTER COLUMN resource_duration_minutes SET DEFAULT 30,
  ALTER COLUMN resource_duration_minutes SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'bookings_no_active_resource_overlap'
  ) THEN
    ALTER TABLE bookings
      ADD CONSTRAINT bookings_no_active_resource_overlap
      EXCLUDE USING gist (
        employee_id WITH =,
        tsrange(
          booking_date + booking_time,
          booking_date + booking_time + resource_duration_minutes * interval '1 minute',
          '[)'
        ) WITH &&
      )
      WHERE (
        employee_id IS NOT NULL
        AND occupies_resource
        AND resource_duration_minutes > 0
        AND status IN (
          'pending',
          'confirmed',
          'pending_prepayment',
          'prepayment_sent',
          'prepayment_confirmed'
        )
      )
      DEFERRABLE INITIALLY DEFERRED;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_bookings_resource_day_active
  ON bookings (employee_id, booking_date)
  WHERE employee_id IS NOT NULL
    AND occupies_resource
    AND status IN (
      'pending',
      'confirmed',
      'pending_prepayment',
      'prepayment_sent',
      'prepayment_confirmed'
    );
