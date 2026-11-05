-- Additive migration; run before deploying the sales routes. No existing data changes.
BEGIN;
CREATE TABLE IF NOT EXISTS sales_conversations (
    id VARCHAR(36) PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    principal VARCHAR(80) NOT NULL,
    messages JSON NOT NULL DEFAULT '[]',
    stage VARCHAR(32) NOT NULL DEFAULT 'needs_analysis',
    facts JSON NOT NULL DEFAULT '{}',
    mode VARCHAR(16) NOT NULL DEFAULT 'ai',
    handoff_reason TEXT,
    action_id VARCHAR(36) UNIQUE,
    action JSON,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sales_business_principal UNIQUE (business_id, principal)
);
CREATE INDEX IF NOT EXISTS ix_sales_conversations_business_id ON sales_conversations(business_id);
COMMIT;
