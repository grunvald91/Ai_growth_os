-- Apply before starting the sales agent API. Safe to repeat.
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS sales_agent_settings JSON NOT NULL DEFAULT '{}';
