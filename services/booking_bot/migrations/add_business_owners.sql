-- Миграция: Добавление таблицы business_owners и полей version
-- Выполнить на PostgreSQL сервере

-- 1. Создаём таблицу business_owners
CREATE TABLE IF NOT EXISTS business_owners (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(20) DEFAULT 'owner',
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    added_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT uix_business_user UNIQUE (business_id, user_id)
);

-- 2. Создаём индексы
CREATE INDEX IF NOT EXISTS idx_business_owners_business_id ON business_owners(business_id);
CREATE INDEX IF NOT EXISTS idx_business_owners_user_id ON business_owners(user_id);

-- 3. Мигрируем существующих владельцев
INSERT INTO business_owners (business_id, user_id, role, added_at)
SELECT id, owner_id, 'owner', created_at
FROM businesses
WHERE owner_id IS NOT NULL
ON CONFLICT (business_id, user_id) DO NOTHING;

-- 4. Добавляем поле version в таблицы
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1;
ALTER TABLE services ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1;

-- Готово!
SELECT 'Migration completed successfully' AS status;
