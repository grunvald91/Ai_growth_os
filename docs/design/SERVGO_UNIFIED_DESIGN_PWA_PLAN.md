# ServGo — единый дизайн ServGo Air и одно установленное PWA
**План реализации / расширение аудита Growth OS v4 от 31.10.2026.**

Статус: **проектное решение и план**, не готовая интеграция и не разрешение на деплой.
Основания: три пользовательских скриншота клиентского пути ServGo в чате (главная каталога, страница заведения, выбор услуги); [аудит v4](GROWTH_V4_REAUDIT_20261031.md); [матрица 61 функции](SERVGO_FUNCTION_MATRIX.md).

## 0. Продуктовое решение: одна визуальная система, одно PWA, разные рабочие области

**Канонический визуальный источник:** стиль действующего клиентского пути BookingBot, реализованный в [web/frontend/src/styles/design-system.css](https://github.com/grunvald91/booking_bot/blob/main/web/frontend/src/styles/design-system.css). Стиль самостоятельного макета Growth v4 НЕ является новым визуальным стандартом.

**Каноническое устанавливаемое приложение:** одно PWA **ServGo** на **https://servgo.ru/**. Клиентская запись, кабинет владельца, Growth и Mail должны открываться как рабочие области **в одном standalone-окне** под тем же origin, с одной иконки приложения. Для этого не нужно объединять базы данных или микросервисы в один процесс. Сервисы могут оставаться отдельными, но пользовательские интерфейсы и переходы должны идти через один внешний origin и согласованные маршруты. Не создавать три конкурирующих PWA с разными manifest и service worker.

**Единая тема:** Light/Dark/System с одним пользовательским выбором, едиными цветами поверхностей, элементов, модальных окон, графиков и системных status/theme colors. Тема должна работать во всех областях и сохраняться при переходе без вспышки неправильной темы.

**Важно:** единый стиль не значит идентичные экраны. Клиенту показывать простой путь записи, владельцу — управление, Growth открывать полупустой командной главной с микрофоном, в Mail показывать почтовый рабочий процесс. Административные действия по-прежнему ограничены серверными правами.

## 1. Что подтверждено кодом (снимки 31.10.2026)

| Факт | Подтверждение |
|---|---|
| BookingBot уже имеет общий дизайн ServGo Air с токенами белых/голубых поверхностей и тёмной темой | [design-system.css](https://github.com/grunvald91/booking_bot/blob/main/web/frontend/src/styles/design-system.css) |
| В BookingBot есть единый manifest с start_url="/" и scope="/" | [manifest.webmanifest](https://github.com/grunvald91/booking_bot/blob/main/web/frontend/public/manifest.webmanifest) |
| BookingBot регистрирует SW по адресу /sw.js | [main.tsx](https://github.com/grunvald91/booking_bot/blob/main/web/frontend/src/main.tsx) |
| Текущий SW кэширует ответы навигации как /index.html без проверки, что это публичный SPA-shell; для новых приватных same-origin страниц это **обязательный пункт безопасности** | [sw.js](https://github.com/grunvald91/booking_bot/blob/main/web/frontend/public/sw.js) |
| UI /mail — **не собственная почта в React**: он получает одноразовую ссылку и отправляет пользователя на mail.servgo.ru/claim | [OwnerMailPage.tsx](https://github.com/grunvald91/booking_bot/blob/main/web/frontend/src/pages/client/OwnerMailPage.tsx) |
| Реальная почта использует отдельный host, сессионную cookie и Origin-проверки; права ограничены mail_owner | [owner_mail.py](https://github.com/grunvald91/booking_bot/blob/main/web/api/routes/owner_mail.py) |
| Growth v4 использует самостоятельный HTML-шаблон/CSS, включая собственную тему и шрифтовую/цветовую систему | [growth_v4.py](https://github.com/grunvald91/Ai_growth_os/blob/main/app/api/growth_v4.py), [growth-live.css](https://github.com/grunvald91/Ai_growth_os/blob/main/app/api/assets/growth-live.css) |
| BookingBot хранит выбор темы в schedTheme, Growth — в servgo-desktop-theme | [useTheme.ts](https://github.com/grunvald91/booking_bot/blob/main/web/frontend/src/hooks/useTheme.ts), [growth-live.js](https://github.com/grunvald91/Ai_growth_os/blob/main/app/api/assets/growth-live.js) |
| Почтовая страница и панель рассылок имеют собственные root-переменные и светлые inline-стили | [owner_mail_ui.html](https://github.com/grunvald91/booking_bot/blob/main/web/api/routes/owner_mail_ui.html), [mail_campaign_dashboard.html](https://github.com/grunvald91/booking_bot/blob/main/web/api/routes/mail_campaign_dashboard.html) |
| Growth проверяет происхождение POST через studio_public_origin, почта — через собственный HOST | [content_studio_auth.py](https://github.com/grunvald91/Ai_growth_os/blob/main/app/api/content_studio_auth.py), [owner_mail.py](https://github.com/grunvald91/booking_bot/blob/main/web/api/routes/owner_mail.py) |

### Почему PWA распадается на браузер

Основной manifest на servgo.ru имеет scope '/', но ссылки на growth.servgo.ru и перенаправление на mail.servgo.ru — переходы на **другие origins**. По обычной модели PWA они выходят из области установленного приложения; в зависимости от браузера появляются браузерные элементы, отдельная вкладка или другое окно. Изменение start_url или одного CSS-файла не решает проблему.

Стандарты: [MDN: scope](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Manifest/Reference/scope), [web.dev: multi-origin PWAs](https://web.dev/articles/multi-origin-pwas), [MDN: service worker / other origins](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Manifest/Reference/scope_extensions).

**Вариант для части браузеров:** experimental **scope_extensions** и подтверждения /.well-known/web-app-origin-association могут сохранить единое окно при переходе на другой origin, но поддержка различается между браузерами; они НЕ дают общей service worker области, localStorage, cookies или гарантированной PWA-навигации на каждой платформе. Не использовать их как основной путь: [MDN](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Manifest/Reference/scope_extensions).

## 2. Визуальный контракт по приложенным скриншотам

Взять готовую базу ServGo Air и привести к ней все рабочие области. Не копировать изображения/контент заведений как фиктивные данные в Growth или Mail.

- **Фирменная шапка:** календарь с ручкой ServGo, спокойный фон, компактные действия справа («Кабинет», пользователь). На мобильном — предсказуемая кнопка назад; не создавать разные логотипы в модулях.
- **Палитра и поверхности:** канонические токены из BookingBot, а не отдельные синеватые темы Growth/Mail. Исходные токены: --s-bg, --s-surface, --s-surface-2, --s-text, --s-muted, --s-line, --s-accent; брендовый логотип не перекрашивать темой.
- **Компоненты:** списки без тяжёлых рамок; мягкие иконки на голубых подложках; карточки и bottom sheets с едиными радиусами; понятные цены и длительность; крупные области нажатия, тонкие разделители; карточки услуги с отдельной кнопкой подробностей.
- **Сетка:** мобильный контент с безопасными отступами, поддержкой клавиатуры и safe-area; нижняя навигация для роли; sticky CTA не закрывает контент; на десктопе слева спокойная навигация, справа — только нужные контекстные данные.
- **Главная Growth:** сохраняется полупустой экран; голосовая/текстовая строка — визуальный центр, несколько неброских подсказок. После команды появляются действительные карточки, подтверждения, аналитика и запуск задач, без выдуманных KPI.
- **Текстовые названия:** во всех пользовательских разделах «Простое видео» с пояснением «Для объяснений, инструкций и простой анимированной рекламы». Внутренние API whiteboard не переименовывать.
- **Доступность:** фон/текст с контрастом, фокус клавиатуры, aria, reduced-motion, 320/360/390/412/768/1024/1440 px; кнопки/иконки доступны пальцем. Удалить лишнее CSS-наслоение и повторение старой/новой оболочки.

**Единый дизайн должен быть не набором разрозненных копий CSS.** Нужен канонический пакет токенов (семантический JSON/CSS) и общие компоненты для двух фронтендов: React BookingBot и HTML/серверный Growth/Mail. Генерировать/синхронизировать стили в CI; падать при расхождении токенов. До переноса сохранить действующие бизнес-формы и серверные обработчики.

## 3. Одно PWA — рекомендуемая архитектура

Публичный канонический origin: https://servgo.ru. Manifest и один управляемый service worker на этом origin с scope '/'. **Это одно установленное приложение ServGo**, а не отдельные установки почты и Growth.

Предлагаемый внешний URL-контракт (зафиксировать после аудита конфликтов):

| Рабочая область | Пример внешнего маршрута одного PWA | Реальный backend |
|---|---|---|
| Клиентский путь | /, /business/:id, /book/:id, /my, /subscriptions, /chat | BookingBot React + API |
| Админка | /admin/* | BookingBot React + API |
| Growth | /workspace/growth/* | Growth OS, через same-origin gateway/reverse proxy |
| Почта | /workspace/mail/* | ServGo Mail UI + API, через same-origin gateway/reverse proxy |
| Переходные существующие ссылки | /mail и ссылки на growth.servgo.ru / mail.servgo.ru | Совместимые redirect/entry точки, **сначала** с проверкой SSO и deep links |

**Критическая техническая деталь:** Growth сейчас использует абсолютные пути /studio/*, /products/*, /studio/assets/* и /api/admin/team, почтовый модуль — /, /api/*, /claim и host-ориентированную cookie/CSRF. Простое nginx location /workspace/growth/ или /workspace/mail/ не исправит эти ссылки и не даст работающий вход. До включения интерфейсов нужно внедрить поддержку URL-prefix/root_path и reverse routing в backend, JS, assets, редиректах и формах; явно разнести конфликтующие API, сохранить глубокие ссылки, заголовки CSP и cookies.

Альтернатива на раннем этапе: монтировать **только интерфейсы** Growth и Mail через единый React app shell, а backend вызывать через отдельные same-origin /api/growth/*, /api/mail/* маршруты с проверенными токенами. Не использовать iframe из чужого origin как «решение» проблемы PWA; пользователь всё равно остаётся в другом security-контексте.

### Авторизация и безопасность

- Единый вход для пользователя по возможности, но полномочия из разных приложений остаются разграниченными: посетитель, клиент, мастер, владелец бизнеса, владелец платформы/mail_owner, оператор Growth. Не делать роль mail_owner общедоступной при объединении URL.
- Для mail (сейчас HOST=mail.servgo.ru) реализовать **новый** same-origin вход с короткоживущим одноразовым ticket, строгим audience/redirect allowlist, проверкой владельца и host-only secure HttpOnly cookie на новом origin. Не переносить через JS старую cookie и не применять cookie Domain=.servgo.ru для расширения прав.
- Для Growth обновить защищённый studio_public_origin и Origin/CSRF-конфигурацию только после развёртывания канонического адреса; проверить обратные прокси и trusted headers, SSO, права на company_id, защищённые POST/PUT.
- CSP должен разрешать только необходимые скрипты/стили; на странице ИИ-команды устранить текущий конфликт со встроенными стилями, НЕ ослабляя CSP.
- Не открывать приватные html/JSON/письма для service worker cache; закрыть утечки через history/back/notification links после logout.

### Service worker и PWA безопасность — блокер перед маршрутизацией

Текущий [BookingBot sw.js](https://github.com/grunvald91/booking_bot/blob/main/web/frontend/public/sw.js) кэширует **любой ответ навигации** в '/index.html'. После same-origin проксирования Growth/Mail это может закэшировать персональную страницу/ответ и подменить публичный SPA-shell. Поэтому:

1. Развести **публичный shell** и **авторизованные navigation**. Для /admin, /workspace/growth, /workspace/mail, /api, /gateway — **network-only/no-store** для HTML и данных, не кэшировать ответы по общему ключу. В offline отдавать отдельный нейтральный offline-документ, не старые данные.
2. Static assets с хэшами можно кэшировать отдельно по версиям; запретить кеширование токенов, персональных HTML, ответов API, аттачментов почты и signed URLs.
3. На обновлении SW сделать контролируемое удаление устаревших кешей и проверку обновлений; не ломать существующие push subscriptions и notificationclick маршруты. Для уведомлений Mail/Growth — типизированные маршруты одного origin, но единая PWA-навигация и проверка сессий.
4. Проверить offline, повторное подключение, logout и очистку приватных данных, обновление вкладки, iOS/Android ограничения без обещаний unsupported функций.
5. Убрать внешние target="_blank" и window.location на другие поддомены для **внутренней** навигации. Настоящие внешние сайты/карты остаются внешними.

## 4. Единая светлая/тёмная тема везде

Канонический API выбора темы: **light | dark | system**.
- На клиентской React-части используется единый provider на корне приложения; цвета только через семантические токены ServGo Air. Не сохранять отдельные темы для страниц /admin, /my и /mail.
- Старый BookingBot ключ schedTheme и Growth ключ servgo-desktop-theme прочитать **однократно при миграции** как fallback; новые версии пишут только 'servgo-theme'. Для авторизованного пользователя предусмотреть server-side preference, чтобы тема синхронизировалась между устройствами; без неё same-origin localStorage обеспечивает лишь локальное устройство.
- При параллельном существовании поддоменов localStorage не общий. Тему можно безопасно передать через endpoint профиля после авторизации, но нельзя считать, что выбор на одном origin автоматически доступен на другом.
- Нативный выбор ОС применять, если пользователь выбрал 'system'; Telegram colorScheme учитывать как окружающий контекст, не перезаписывать ручной выбор.
- Синхронизация открытых вкладок через storage event и/или BroadcastChannel. Класс/data-theme выставлять до первой отрисовки; скрипты/SSR/React используют один источник.
- Единый <meta name="theme-color"> на всех страницах плюс светлый/тёмный вариант с поддерживаемыми media, цвет формы/статус-бара, корректный color-scheme для системных полей. Не перекрашивать логотип или фотографии. Основа: [MDN theme_color](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Manifest/Reference/theme_color).
- В Mail удалить несвязанные inline-палитры и применить общие компоненты; проверить контраст полей/таблиц/статусов/ошибок и запрет контраста при тёмной теме.

**Приёмка:** вручную включить Dark в клиентской области → открыть админку → Growth → Mail → вернуться к записи: на каждом маршруте сразу Dark, без мигания Light. Тот же сценарий повторить для Light/System, после закрытия/открытия PWA и после обновления страницы.

## 5. Реализация этапами и контрольными точками

| Шаг | Область / исполнение | Результат / критерий приёмки |
|---|---|---|
| **0. Инвентаризация** | BookingBot + Growth: существующие 382 маршрута, PWA/SW/SSL/SSO/CSP, все ссылки и host assumptions | Таблица конфликтов URI, login/logout/redirect, список точек выхода из PWA |
| **1. Design system** | BookingBot canonical tokens → экспорт CSS для Growth/Mail, общая типографика/иконки | Все три области соответствуют скриншотам: light/dark, desktop/mobile; нет отдельной «синей темы» Growth |
| **2. Компоненты и роли** | Общая шапка, разделы, списки, поля, модальные окна, bottom navigation + desktop nav | Правильные роли и контексты; главная Growth полупустая; навигация сохраняется без потери истории |
| **3. PWA hardening** | BookingBot manifest, SW, static/cache/push | Приватные страницы НЕ закэшированы; offline/logout не раскрывают старые письма, SEO или записи |
| **4. Same-origin gateway** | Reverse proxy и route-prefix для Growth/Mail, CSP/CSRF, SSO/claim | Из установленного PWA переходы клиент → админ → Growth → Mail → назад **в одном standalone-окне** и на одном origin |
| **5. Migration + theme** | Ссылки, deep links, переподключение push, старые cookies и theme keys | Переходный период не ломает существующие входы; темная тема единая на всех экранах |
| **6. Feature parity** | Реестр 61 функции + аудит 31.10 | Старые формы не потеряны; Video в UI — «Простое видео»; исправлены CSP ИИ-команды и 320px overflow |
| **7. E2E и выпуск** | Playwright desktop/mobile, реальный Android Chrome, iOS Safari, staged release | Роли, auth, действия, deep links, PWA launch, единая тема, возврат, offline, уведомления проверены; ручной go/no-go |

### Обязательные E2E сценарии

- Установка **одного ServGo** через servgo.ru на Android; один значок; переход из клиентского пути в кабинет, расписание, Growth и Mail внутри установленного приложения. Внешний сайт явно уходит наружу.
- Аналогичный сценарий в Chrome desktop и iOS Safari; учитывать платформенные ограничения link capture. **Не обещать**, что ссылка из стороннего мессенджера на всех ОС всегда откроет уже установленное PWA: внешнее перехватывание ссылок зависит от браузера/ОС.
- Прямые URL и reload любой глубокой страницы /book, /admin, /workspace/growth, /workspace/mail (проверить обратный прокси, 404, origin и корректную авторизацию).
- Гость видит только клиентские функции; сотрудник не видит чужую компанию; владелец бизнеса НЕ получает mail_owner и platform-admin через общий origin. 401/403/404/409/422, CSRF, reset password, logout без обхода.
- Все 61 сценарий из матрицы пометить: перенесён/использует старую форму/внешний; для действий с рассылкой, публикацией и оплатой обязательное подтверждение.
- Light/Dark/System, media prefers-color-scheme, переход между модулями, не теряется состояние голосового ввода при клавиатуре, экраны 320–1440 px, уменьшение анимации, push click URL.
- Обновление старого установленного PWA с существующим SW, очистка старого кеша и безопасный fallback; восстановление соединения после offline.
- Mail отдельно: черновик, поиск email, список адресатов, отправка после подтверждения, отписка, лог отправки, проверка отсутствия доступа у бизнес-пользователя.

## 6. Важные ограничения и стратегия релиза

- Работать в отдельных review-ветках обоих репозиториев; делать маленькие PR по этапам, каждый со скриншотами и проверками. **Никаких действий с реальной отправкой писем, списанием средств или массовой SEO-публикацией** в UX-тестах.
- Сначала рабочая совместимость исходных API и адресов, затем менять UI. Не ломать BookingBot бронирования ради эстетики.
- Nginx-конфиг в репозитории может не совпадать с production; фактические virtual hosts, service workers и cookies на сервере нужно проинспектировать перед переключением DNS/пути. Не выполнять произвольную замену location без проверки.
- Сохранить rollback feature-flag: прежние ссылки и /studio на старом origin доступны до завершения миграции, а новое меню переключается обратно без потери данных.
- Для Chrome/Edge можно исследовать experimental scope_extensions как **дополнительный переходный путь**, но приёмка «одно PWA» должна базироваться на same-origin архитектуре, независимой от experimental поддержки.

## 7. Минимальные ближайшие задачи

1. Снять реальную схему production nginx (без секретов), выяснить, где обслуживаются https://servgo.ru/mail, https://mail.servgo.ru/ и https://growth.servgo.ru/, проверить Origin/CSRF и SW.
2. Создать canonical токены ServGo Air и первый сравнительный visual snapshot для клиентского пути, BookingBot admin, Growth, Mail (mobile+desktop, light+dark).
3. Исправить опасную логику кеширования любых navigations в BookingBot SW **до** расширения same-origin PWA.
4. Реализовать безопасную same-origin точку входа для Mail с сохранением mail_owner прав и далее Growth с безопасным route-prefix, тестируя ссылку из PWA.
5. Обновить [аудит v4](GROWTH_V4_REAUDIT_20261031.md) и [матрицу](SERVGO_FUNCTION_MATRIX.md) по факту завершения шагов; не маркировать план как «готово» без реального браузерного теста.

**Приёмка проекта:** владелец устанавливает **один ServGo**, может перейти между клиентской записью, админкой, почтой и Growth без выхода в системный браузер при внутренних переходах; дизайн/тема идентичны по правилам во всех областях, а доступ к данным и функциям ограничен ролями. Отдельные бэкенды продолжают работать, но не ощущаются как отдельные приложения.
