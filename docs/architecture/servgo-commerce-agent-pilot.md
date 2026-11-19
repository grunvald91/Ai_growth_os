# ServGo Commerce Agents: безопасный пилот — 19.11.2026

## Источники
- https://github.com/anthropics/commerce-agents
- https://github.com/anthropics/commerce-agents/blob/main/merchant-agent/skills/performance-insights/SKILL.md
- https://github.com/anthropics/commerce-agents/tree/main/shopping-agent

Использованы идеи: grounded metrics, agent skills, раздельные read/write и
явное подтверждение изменений. Код Anthropic не копируется.
Существующий ClaudeHub/Cloud Hub остаётся без изменений.

## Что уже имеется в ServGo

- web/api/sales/engine.py: model→tools, подготовка, оператор.
- web/api/sales/tools.py: каталог, доступность, цена, карточка и повторная проверка записи.
- web/api/sales/policy.py: права на действия, независимые от ответа модели.
- web/api/routes/ai.py: история в границах компании и пользователя, подтверждение.
- web/api/routes/admin.py: owner-scoped показатели текущего и предыдущего периодов.
- app/ai_chat: чат. Независимый PR #342 — консультанты без вызова BookingBot.

## Реализовано — первый read-only срез Business AI

GET /api/admin/analytics/{business_id}/agent-brief?days=30

/api — внешний root_path приложения; внутренний маршрут /admin/analytics/...

- Тот же auth/tenant guard, что у /admin/analytics/{id}/overview.
- Показатели текущего и предыдущего периодов, ссылки на поля их источника.
- revenue — оценка стоимости записей, не фактически оплаченная выручка и не прибыль.
- Допуслуги включаются в оценку стоимости, но не считаются отдельными визитами.
- Недоступная загрузка и процент изменения от нулевой базы остаются null.
- Нет запуска LLM, оплаты, изменения бронирований или рассылок.
- Топ-5 не достаточен, чтобы определить самого слабого сотрудника.
- Снижение/рост показателей сами по себе не доказывают причину.

## Следующие этапы — отдельными изменениями

1. Инструмент get_business_snapshot от имени серверно авторизованного владельца
   с вызовом только этого endpoint; ID компании не брать без проверки из модели.
2. Навык business-performance-insights: привязка выводов к данным, сравнительные
   периоды, «нет данных — нет вывода».
3. Проект staged changes: передача согласованных предложений в Growth с одобрением
   владельца и проверкой прав непосредственно перед действием.
4. Единый каталог инструментов и тесты для Booking AI. Не создавать вторую систему записи.
5. Внедрить в Studio Chat после согласования с PR #342 и миграцией 0017.

## Проверки и выпуск

- Тесты: services/booking_bot/tests/test_business_ai_brief.py.
- Проверить маршрут с действительной сессией владельца и отказ постороннему.
- Ветка/PR только для проверки; не вливать в main без зелёного CI и ревью.
- Production-deploy на данном этапе не производится.
