-- ServGo: separate upcoming reminders for owners/masters.
ALTER TABLE bookings
  ADD COLUMN IF NOT EXISTS staff_reminder_24h_sent BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS staff_reminder_2h_sent BOOLEAN NOT NULL DEFAULT FALSE;
