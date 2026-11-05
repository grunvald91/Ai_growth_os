-- Миграция для добавления полей предоплаты
-- Добавляем поля в таблицу businesses

-- Поля для настройки предоплаты в бизнесе
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS prepayment_enabled BOOLEAN DEFAULT FALSE;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS prepayment_percent INTEGER DEFAULT NULL;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS payment_phone VARCHAR(20) DEFAULT NULL;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS payment_details TEXT DEFAULT NULL;

-- Поля для хранения информации о предоплате в бронировании
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS prepayment_amount INTEGER DEFAULT NULL;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS prepayment_confirmed_at TIMESTAMP DEFAULT NULL;

-- Комментарии к новым полям
COMMENT ON COLUMN businesses.prepayment_enabled IS 'Включена ли предоплата для бизнеса';
COMMENT ON COLUMN businesses.prepayment_percent IS 'Процент предоплаты (1-100)';
COMMENT ON COLUMN businesses.payment_phone IS 'Номер телефона для получения оплаты';
COMMENT ON COLUMN businesses.payment_details IS 'Дополнительные реквизиты для оплаты';
COMMENT ON COLUMN bookings.prepayment_amount IS 'Сумма предоплаты в рублях';
COMMENT ON COLUMN bookings.prepayment_confirmed_at IS 'Время подтверждения предоплаты';

-- Статусы бронирования теперь включают:
-- pending - ожидает (старый)
-- confirmed - подтверждено (старый)
-- cancelled - отменено (старый)
-- completed - завершено (старый)
-- pending_prepayment - ожидает предоплату (новый)
-- prepayment_sent - клиент отправил предоплату (новый)
