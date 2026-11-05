-- Миграция: добавление таблицы employee_services и разрешение нескольких бизнесов для админа
-- Дата: 2026-01-19
-- Описание: Связь между мастерами и услугами + возможность админу работать в нескольких бизнесах

-- 1. Создание таблицы employee_services
CREATE TABLE IF NOT EXISTS employee_services (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(employee_id, service_id)
);

-- Создание индексов для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_employee_services_employee ON employee_services(employee_id);
CREATE INDEX IF NOT EXISTS idx_employee_services_service ON employee_services(service_id);

-- Комментарии к таблице
COMMENT ON TABLE employee_services IS 'Связь между сотрудниками и услугами (какие услуги может делать мастер)';
COMMENT ON COLUMN employee_services.employee_id IS 'ID сотрудника (мастера)';
COMMENT ON COLUMN employee_services.service_id IS 'ID услуги';

-- 2. Удаление UNIQUE constraint с telegram_id в employees
-- Это позволяет одному человеку быть сотрудником в нескольких бизнесах
ALTER TABLE employees DROP CONSTRAINT IF EXISTS employees_telegram_id_key;
