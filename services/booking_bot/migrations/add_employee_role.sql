-- Миграция: Добавление поля role в таблицу employees
-- Дата: 2026-01-15
-- Описание: Добавляет роль (master/administrator) для сотрудников

-- Добавить колонку role если её нет
ALTER TABLE employees ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'master';

-- Проверить результат
SELECT id, name, role FROM employees;
