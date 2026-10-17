# ServGo — состояние выкладки 14.10.2026

## Проверенный исходный набор
- AI Growth OS: ветка `release/combined-20261014`, кодовый коммит `ccd1cf7347f4d933dc97d3a33980531a9599b148`.
- Совмещены критик `feat/agent-critic-advisory-20261013` и роутер `feature/ai-model-router`.
- Сохранены исходники работающего whiteboard/Animator и исправление StudioOriginError.
- Добавлены совместная маршрутизация критика по исходной роли, учёт оценок стоимости, роль comment_sales, вход в панель команды через существующую операторскую Basic-авторизацию.
- Позднее уточнён настоящий последний frontend-кандидат: `wip/studio-unified-workspace-20261013`, см. его docs/STUDIO_UNIFIED_RELEASE_20261013.md. Он имеет четыре основных раздела и marker 20261013-unified10. Его UI-файлы и тесты выбраны в отдельном рабочем дереве, но их финальные тесты и выкладка НЕ подтверждены.

## Фактически выполненные проверки
- Совместный релиз до добавления unified UI: 633 passed на Python 3.14.
- Тот же релиз: 633 passed, 1 warning на Python 3.12, как на сервере.
- Почта и блог: 19 passed, включая одноразовую ссылку, срок действия, Secure/HttpOnly/SameSite cookie, запрет чужому пользователю и cross-origin POST.
- React build из исходников, чей прежний dist/index.html совпал с production: успешно.
- Исправлена реальная ошибка mail_campaign_dispatch.py: удалён несуществующий smtplib.SMTPRecipientRefused. Тест SMTP timeout после исправления прошёл.
- Совмещение Booking API сохраняет уже работающие email_auth, telegram_link и payments; готовая почтовая ветка сама их не содержала.
- Зафиксированная API-проверка Actions для release/combined-20261014 вернула total_count=0.

## Наблюдённые операции сервера — не путать с завершённой приёмкой
1. Через существующий Codespace и SERVER_SSH_KEY прочитаны исходники и конфигурация.
2. На сервер передан пакет /opt/servgo-releases/20261014/payload.tgz.
3. Growth rollout подготовил новый образ и Compose overlay. В журнале видны Recreated и Started для api, worker и telegram.
4. Итог GROWTH_RELEASE_OK и exit code не прочитаны: управление браузером перестало отвечать.
5. Критик и роутер намеренно оставлены AGENT_CRITIC_ENABLED=false и MODEL_ROUTER_ENABLED=false.
6. Запрошен запуск booking rollout с резервными копиями/откатом. Вызов управления браузером завершился тайм-аутом; его фактический итог неизвестен. Не объявлять почту/блог опубликованными.
7. SMENA не публиковалась. Мини-апп размещён на Telegram Serverless, а smena.servgo.ru — отдельный QR-экран. Целевая ветка fix/mobile-miniapp-overflow-20261014, PR grunvald91/SMENA#10.

## Независимая HTTP-проверка после потери терминала
- https://servgo.ru/api/health → 200, {"status":"ok"}.
- https://servgo.ru/blog → 200, но title ServGo, 1014 символов SPA-shell, НЕ серверный блог.
- https://servgo.ru/api/owner-mail/permission → 404.
- https://mail.servgo.ru/ → 502 Bad Gateway.
- https://growth.servgo.ru/studio/team → 401 от Nginx. Это подтверждает внешний барьер авторизации, но не успешную работу нового backend.
Нельзя считать одну только HTTP 200 успешным релизом блога.

## Блокировки
- Browser/CUA перестал отвечать: подряд истекли 300-секундные тайм-ауты pressKey, getAXState и js_reset. Повторно серверные изменения не запускались вслепую.
- Автоматическая проверка отклонила запрос каталога ClaudeHub серверным XAI_API_KEY, назвав его неразрешённой передачей ключа внешнему провайдеру. Отклонённый запрос не обходили. Для каталога/контрольной генерации и включения флагов требуется явное согласие владельца.
- В существующей конфигурации найдены имена SMTP_FROM, SMTP_FROM_EMAIL и CLAUDEHUB_API_KEY; IMAP-пароль не обнаружен. Входящие письма не объявлять подключёнными.
- Codespace SMENA открыт, но находится в Restricted Mode; доверие к этой отдельной папке не подтверждено.

## Точки продолжения
Рабочий Codespace: silver-space-winner-r467jjq6q695cxr77.
Вся подготовка: /workspaces/release-20261014.
- growth/, growth-venv/, growth-live/
- booking-live/, booking-live.tgz, release-input/
- payload.tgz, payload/
- growth-tests.log, growth-312-tests.log, growth-312-tests.exit
- growth-ui-tests.log, growth-ui-tests.exit — результат прочитать первым
- booking-final-tests.log, booking-build.log
- growth-deploy.log, growth-deploy.exit
- booking-deploy.log, booking-deploy.exit
- remote_rollout.py, codespace_rollout_prep.py, test_owner_mail_access.py
- server_key — временная копия существующего ключа с mode 0600; не печатать, не коммитить, удалить после окончания работ.

Сервер:
- /opt/servgo-releases/20261014/backup — scoped backups, если booking rollout дошёл до записи.
- /opt/servgo-releases/20261014/growth-overlay.json и growth-rollback.json.
- /opt/servgo-releases/20261014/payload/remote_rollout.py.
Сначала проверить журналы и действующие сервисы. Затем исправить причину неуспешной публикации mail/blog, проверить владельческий вход без отправки реальных писем. Доработать/проверить unified UI, проверить отсутствие running whiteboard job перед перезапуском worker. После отдельного разрешения проверить ClaudeHub и включить критика/роутер. Для SMENA — авторизованная публикация именно frontend в Telegram Serverless, не замена VPS QR-экрана.
Не запускать GitHub Actions. Не пересылать реальные рекламные письма в рамках проверки.
