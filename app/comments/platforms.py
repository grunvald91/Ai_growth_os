"""Platform requirements are explicit; unsupported writes never masquerade as connected."""
PLATFORMS = {
    "youtube": {
        "name": "YouTube", "live": True, "account_label": "ID вашего канала (UC…)",
        "steps": ["В Google Cloud включите YouTube Data API v3 и настройте OAuth приложения.",
                  "Авторизуйте владельца канала с правом youtube.force-ssl и получите access token.",
                  "Укажите ID канала и токен ниже. Сервис проверит, что канал принадлежит авторизованному аккаунту."],
        "limits": "Комментарии загружаются при обновлении. Один ответ расходует 50 единиц квоты API. При истечении токена подключите аккаунт заново.",
        "docs": "https://developers.google.com/youtube/v3/docs/comments/insert",
    },
    "telegram": {
        "name": "Telegram", "live": True, "account_label": "ID группы обсуждения (−100…)",
        "steps": ["Создайте отдельного бота через @BotFather. Не используйте бота управления AI Growth OS.",
                  "Свяжите канал с группой обсуждения, добавьте бота в эту группу администратором с правом отправлять сообщения.",
                  "Укажите ID группы и токен бота. Бот должен работать только с этим сервисом, без другого webhook или получателя обновлений."],
        "limits": "Ответы отправляются от имени бота в обсуждении. Загружаются только новые сообщения связанной группы; прежняя история Bot API недоступна.",
        "docs": "https://core.telegram.org/bots/api",
    },
    "instagram": {
        "name": "Instagram", "live": True, "account_label": "ID профессионального аккаунта",
        "steps": ["Используйте профессиональный аккаунт Business или Creator и приложение Meta с Instagram Login.",
                  "Получите разрешения instagram_business_basic и instagram_business_manage_comments. Для сторонних аккаунтов может потребоваться App Review.",
                  "Укажите ID Instagram-аккаунта и токен этого приложения. Этот способ не требует привязки Facebook Page."],
        "limits": "Комментарии загружаются с последних публикаций при обновлении. Ответы только под своими публикациями; личные сообщения требуют отдельного разрешения и здесь не отправляются.",
        "docs": "https://www.postman.com/meta/instagram/folder/6raa77c/instagram-api-with-instagram-login",
    },
    "facebook": {"name": "Facebook", "live": False,
        "steps": ["Для подключения нужен доступ к Facebook Page, Page access token и разрешения приложения Meta для чтения и управления взаимодействиями.", "В этой версии используйте ручной ввод комментария и копирование ответа. Адаптер Facebook пока не включён."],
        "limits": "Личный профиль и бизнес-страница имеют разные возможности API.",
        "docs": "https://developers.facebook.com/docs/pages-api/"},
    "threads": {"name": "Threads", "live": True, "account_label": "Числовой ID Threads-аккаунта", "max_length": 500,
        "steps": ["Создайте приложение Meta с Threads API и авторизуйте владельца Threads-аккаунта.",
                  "Получите access token с threads_basic, threads_content_publish, threads_read_replies и threads_manage_replies. Для сторонних аккаунтов потребуется соответствующий доступ Meta/App Review.",
                  "Укажите ID Threads и токен. Токен Instagram не заменяет подключение Threads; разрешения выдаются отдельно."],
        "limits": "Ответ до 500 символов. Загружаются обсуждения своих публикаций, включая вложенные ответы. Публикация требует одобрения; при истечении токена подключите аккаунт заново.",
        "docs": "https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api"},
    "vk": {"name": "VK", "live": True, "account_label": "Числовой ID сообщества без club/public",
        "steps": ["Используйте сообщество, где вы редактор или администратор. Разрешите комментарии к записям.",
                  "Авторизуйтесь через поддерживаемое VK OAuth приложение и получите пользовательский access token с правами wall и groups. Токен сообщества не подходит для используемых методов чтения.",
                  "Укажите положительный ID сообщества и токен. Сервис проверит роль и доступ к записям; ответы публикуются от имени сообщества."],
        "limits": "Комментарии к записям и вложенные ответы загружаются при обновлении. Для видео используйте отдельное подключение VK Видео. Возможность публикации зависит от прав токена и настроек комментариев.",
        "docs": "https://dev.vk.com/ru/method/wall.createComment"},
    "vk_video": {"name": "VK Видео", "live": True, "account_label": "Числовой ID сообщества-владельца видео",
        "steps": ["Выберите сообщество, где вы редактор или администратор и которому принадлежат видео. Разрешите комментарии к видео.",
                  "Получите пользовательский VK access token с правами video и groups через поддерживаемое VK OAuth приложение. Токен сообщества не поддерживает video.getComments / video.createComment.",
                  "Укажите ID сообщества и токен отдельно от карточки VK. Ответы публикуются от имени сообщества через video.createComment."],
        "limits": "Загружаются комментарии и вложенные ответы только к собственным видео сообщества; сохранённые чужие видео исключаются. VK Клипы и личные видеоколлекции требуют отдельного адаптера.",
        "docs": "https://dev.vk.com/ru/method/video.createComment"},
    "tiktok": {"name": "TikTok", "live": False,
        "steps": ["Display API и Login Kit не предоставляют публикацию ответов на комментарии.", "Для автоматической работы нужен отдельно подтверждённый доступ к подходящему API for Business. Пока используйте ручные черновики."],
        "limits": "Research API нельзя считать подключением для коммерческого продажника.",
        "docs": "https://developers.tiktok.com/docs/en/display-api-overview"},
    "dzen": {"name": "Дзен", "live": False, "steps": ["Дзен принадлежит VK с 2022 года. Яндекс ID OAuth сам по себе не даёт доступ к публикациям и комментариям Дзена.", "Для публикаций подключите публичный Telegram-канал к @zen_sync_bot — Синхроботу Дзена.", "ServGo публикует в ваш Telegram-канал, дальнейшую синхронизацию выполняет сам Дзен.", "Ответы на комментарии Дзена готовьте в ServGo и переносите вручную: открытый API комментариев не подтверждён."], "limits": "Трансляция Telegram → Дзен настраивается за пределами ServGo. Сервис не может подтвердить размещение в Дзене по факту отправки в Telegram.", "docs": "https://t.me/zen_sync_bot"},
    "vc_ru": {"name": "vc.ru", "live": False, "steps": ["Для личного или профессионального Telegram-канала проверьте условия официального бота vc.ru: https://vc.ru/about-tg-bot.", "Отправьте адрес Telegram-канала и блога vc.ru на telegram@vc.ru и дождитесь модерации. Каналы брендов площадка не подключает.", "После одобрения vc.ru может автоматически переносить посты из подключённого Telegram-канала. Это внешняя интеграция: ServGo не подтверждает конечную публикацию в vc.ru.", "Для остальных аккаунтов используйте копирование материалов в редактор. Ответы на комментарии vc.ru — вручную."], "limits": "У vc.ru есть официальный Telegram-репостинг, но он доступен после модерации и не предназначен для каналов брендов. Публичный API публикаций и ответов на комментарии не подтверждён.", "docs": "https://vc.ru/about-tg-bot"},
    "tenchat": {"name": "TenChat", "live": False, "steps": ["Вставьте комментарий. Проверьте профессиональный тон и скопируйте ответ в приложение."], "limits": "Автоматическая отправка не подключена.", "docs": "https://tenchat.ru/"},
    "own_site": {"name": "Сайт бизнеса", "live": False, "steps": ["Добавьте вопрос вручную. Для автоматического подключения необходимо определить CMS и её API."], "limits": "Адаптер зависит от платформы вашего сайта.", "docs": None},
}



# Links belong to individual steps so the owner can act without searching the docs.
CONNECTION_GUIDES = {
    "youtube": {
        "steps": [
            "Войдите в аккаунт владельца нужного канала. Откройте расширенные настройки YouTube и скопируйте «Идентификатор канала»: он начинается с UC. Имя @канала сюда не подходит.",
            "В Google Cloud выберите или создайте проект, включите YouTube Data API v3. В Google Auth Platform настройте экран согласия и OAuth-клиент типа Web application. Для тестового приложения добавьте свой Google-аккаунт в тестировщики. В разрешённые redirect URI добавьте https://developers.google.com/oauthplayground (без слеша в конце).",
            "Откройте OAuth Playground: в шестерёнке включите Use your own OAuth credentials и укажите Client ID и Client secret из своего проекта. На шаге 1 введите scope https://www.googleapis.com/auth/youtube.force-ssl, нажмите Authorize APIs и выберите владельца нужного канала. На шаге 2 нажмите Exchange authorization code for tokens и скопируйте Access token.",
            "Вернитесь сюда: вставьте ID канала и Access token, затем нажмите «Проверить и подключить». После статуса «Подключено» нажмите «Загрузить комментарии»."
        ],
        "links": {0: [("Настройки YouTube", "https://www.youtube.com/account_advanced")], 1: [("Включить YouTube API", "https://console.cloud.google.com/apis/library/youtube.googleapis.com"), ("Настроить OAuth", "https://console.cloud.google.com/auth/overview")], 2: [("Открыть OAuth Playground", "https://developers.google.com/oauthplayground/")]},
        "help": "ID начинается с UC; токен — значение Access token, без слова Bearer. API key, Client secret и Refresh token в это поле не подходят."
    },
    "telegram": {
        "steps": [
            "Откройте BotFather, отправьте /newbot, задайте имя и username нового бота. Скопируйте выданный токен. Создайте отдельного бота для комментариев, чтобы он не конфликтовал с другими сервисами.",
            "В настройках своего канала откройте «Обсуждение» и привяжите группу. В группе добавьте нового бота администратором и разрешите отправлять сообщения. Подключается группа обсуждения, а не сам канал.",
            "Найдите ID группы. Для приватной группы: в Telegram Desktop скопируйте ссылку на сообщение вида t.me/c/1234567890/42. Возьмите число после /c/ и добавьте перед ним -100: в примере ID будет -1001234567890. Для группы с публичным username ID можно получить методом getChat Bot API; если такого инструмента нет, этот шаг выполняет администратор интеграции.",
            "Вставьте ID группы и токен BotFather ниже, нажмите «Проверить и подключить». Напишите новый комментарий в обсуждении, затем нажмите «Загрузить комментарии». Ответ будет отправлен от имени бота."
        ],
        "links": {0: [("Открыть BotFather", "https://t.me/BotFather")], 1: [("Права бота в группе", "https://core.telegram.org/bots/faq#what-messages-will-my-bot-get")], 2: [("Получить ID через getChat", "https://core.telegram.org/bots/api#getchat")]},
        "help": "ID группы начинается с -100. Токен берётся из BotFather. Старые сообщения группы не загружаются; бот не должен использовать чужой webhook."
    },
    "instagram": {
        "steps": [
            "В Instagram переключите свой аккаунт на профессиональный: Business или Creator. Обычный личный аккаунт не подходит. Этот способ подключения использует Instagram Login и не требует Facebook Page.",
            "В Meta for Developers создайте или откройте своё приложение и настройте Instagram API with Instagram Login. Для проверки собственного аккаунта добавьте его в тестировщики приложения и примите приглашение в Instagram.",
            "В настройке Instagram Login авторизуйте нужный аккаунт и получите Instagram User access token с разрешениями instagram_business_basic и instagram_business_manage_comments. Получение токена описано в официальной инструкции ниже; для чужих аккаунтов может понадобиться App Review.",
            "Получите числовой ID этого аккаунта из ответа GET /me?fields=user_id,username на graph.instagram.com — нужен user_id. Вставьте его и Instagram User access token в форму, проверьте подключение и загрузите комментарии. @username и токен Facebook Page не подходят."
        ],
        "links": {1: [("Мои приложения Meta", "https://developers.facebook.com/apps/")], 2: [("Настроить Instagram Login", "https://www.postman.com/meta/instagram/folder/6raa77c/instagram-api-with-instagram-login")], 3: [("Официальная коллекция запросов Meta", "https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api")]},
        "help": "Нужны числовой user_id и Instagram User access token одного аккаунта. Получение токена требует настройки приложения Meta."
    },
    "threads": {
        "steps": [
            "Откройте Meta for Developers и создайте или выберите приложение с Threads API. Добавьте владельца своего Threads-аккаунта в тестировщики и примите приглашение в Threads.",
            "В настройке Threads API авторизуйте этот аккаунт и получите Threads access token. Нужны threads_basic, threads_content_publish, threads_read_replies и threads_manage_replies. Используйте инструкцию Authorization в официальной коллекции Meta; токен Instagram не подходит.",
            "Выполните запрос профиля /me?fields=id,username на graph.threads.net с этим токеном. Скопируйте числовое поле id. Ниже вставьте ID и токен, проверьте подключение, затем загрузите комментарии своих публикаций."
        ],
        "links": {0: [("Мои приложения Meta", "https://developers.facebook.com/apps/")], 1: [("Авторизация Threads API", "https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api")], 2: [("Запрос ID Threads", "https://www.postman.com/meta/threads/request/q9os31a/get-threads-user-s-profile-information")]},
        "help": "Нужны числовой ID Threads и отдельный Threads access token. Для сторонних аккаунтов может потребоваться проверка приложения Meta."
    },
    "vk": {
        "steps": [
            "Войдите в VK как редактор или администратор нужного сообщества и включите комментарии к его записям.",
            "Узнайте числовой ID сообщества. Если адрес содержит club12345 или public12345, ID — 12345. Для короткого имени получите ID через groups.getById в документации VK.",
            "Получите пользовательский access token через приложение VK с доступом к wall и groups по официальной инструкции. Нужен токен пользователя с ролью редактора или администратора; токен сообщества не подходит. Если приложения с такими правами нет, потребуется настройка интеграции разработчиком.",
            "Вставьте положительный ID без club/public и токен ниже. Нажмите «Проверить и подключить», затем «Загрузить комментарии». Ответы публикуются от имени сообщества."
        ],
        "links": {1: [("Найти ID: groups.getById", "https://dev.vk.com/ru/method/groups.getById")], 2: [("Токены доступа VK", "https://dev.vk.com/ru/api/access-token/getting-started"), ("Метод чтения комментариев", "https://dev.vk.com/ru/method/wall.getComments")]},
        "help": "Например, для club12345 укажите 12345. Нужен пользовательский токен с wall и groups, не ключ сообщества."
    },
    "vk_video": {
        "steps": [
            "Выберите сообщество, где вы редактор или администратор, и проверьте, что оно владеет нужными видео. Включите комментарии к видео.",
            "Найдите числовой ID сообщества-владельца: число после club/public в адресе или id из groups.getById. Укажите его без минуса и букв.",
            "По инструкции VK получите пользовательский access token с video и groups. Токен сообщества не поддерживает используемые video.getComments / video.createComment. Если подходящего приложения нет, понадобится разработчик интеграции.",
            "Заполните эту карточку отдельно от VK: ID сообщества и токен, затем «Проверить и подключить» и «Загрузить комментарии». Обрабатываются собственные видео сообщества, а не сохранённые чужие видео."
        ],
        "links": {1: [("Найти ID сообщества", "https://dev.vk.com/ru/method/groups.getById")], 2: [("Токены доступа VK", "https://dev.vk.com/ru/api/access-token/getting-started"), ("Комментарии к видео", "https://dev.vk.com/ru/method/video.getComments")]},
        "help": "Положительный ID сообщества и пользовательский токен с video и groups. VK и VK Видео подключаются отдельно."
    }
}
for _platform, _guide in CONNECTION_GUIDES.items():
    PLATFORMS[_platform].update(_guide)

def reply_limit(platform, configured):
    return min(configured, PLATFORMS[platform].get("max_length", configured))

