"""Owner workspace, protected by the same authenticated edge as Growth Studio."""
import json
from uuid import UUID
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.content_studio import esc, studio_page_shell
from app.api.comments_design import COMMENTS_CSS, COMMENTS_JS
from app.comments.contracts import AgentSettings, CommentInput, ConnectInput, ReplyEdit
from app.comments.models import BusinessComment, CommentChannel
from app.comments.platforms import PLATFORMS, reply_limit
from app.comments.service import CommentService
from app.core.config import get_settings
from app.core.composition import build_llm_provider
from app.db.session import get_db
from app.llm.service import LLMService
from app.models.domain import Artifact
from app.privacy import PrivacyGateway

router = APIRouter()
STATUS = {"new": "Новый", "draft": "Черновик", "review": "Нужна проверка", "sent": "Отправлен",
          "sending": "Отправляется", "delivery_unknown": "Проверьте доставку", "skipped": "Пропущен", "handled": "Передан человеку"}


def service(db=Depends(get_db), settings=Depends(get_settings)):
    return CommentService(db, settings)


def error(exc):
    return HTTPException(404 if isinstance(exc, LookupError) else 409, str(exc))


async def body(request, schema):
    # Avoid FastAPI validation responses echoing credentials back to the client.
    if int(request.headers.get("content-length", "0")) > 100_000:
        raise HTTPException(413, "Слишком большой запрос")
    try:
        raw = await request.body()
        if len(raw) > 100_000:
            raise HTTPException(413, "Слишком большой запрос")
        return schema.model_validate_json(raw)
    except (ValidationError, ValueError):
        raise HTTPException(422, "Проверьте заполнение полей") from None


async def same_origin(request: Request):
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.netloc != request.url.netloc or parsed.scheme not in {"http", "https"}:
            raise HTTPException(403, "Откройте настройки на сайте AI Growth OS")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Откройте настройки на сайте AI Growth OS")


def public_channel(row):
    return {"platform": row.platform, "account_name": row.account_name,
            "account_id": row.account_id, "status": row.status}


@router.get("/studio/assets/comments.css")
def css():
    return Response(COMMENTS_CSS, media_type="text/css")


@router.get("/studio/assets/comments.js")
def js():
    return Response(COMMENTS_JS, media_type="application/javascript")


@router.get("/studio/products/{product_id}/comments", response_class=HTMLResponse)
def page(product_id: UUID, tab: str = "inbox", platform: str = "all", status: str = "all", svc=Depends(service)):
    try:
        product = svc.product(product_id)
        config, settings_record = svc.config(product_id)
    except (ValueError, LookupError) as exc:
        raise error(exc)
    base = f"/studio/products/{product_id}/comments"
    tabs = ''.join(f'<a class="btn {"primary" if tab == key else "ghost"}" href="{base}?tab={key}" aria-current="{str(tab == key).lower()}">{name}</a>'
        for key, name in [("inbox", "Входящие"), ("agent", "Бизнес и агент"), ("platforms", "Подключения"), ("test", "Проверка ответов")])
    channels = list(svc.db.scalars(select(CommentChannel).where(CommentChannel.product_id == product_id)))
    live = {row.platform: row for row in channels if row.status == "connected"}
    options = ''.join(f'<option value="{key}" {"selected" if platform == key else ""}>{esc(value["name"])}</option>' for key, value in PLATFORMS.items())
    route = "VK и VK Видео подключаются напрямую. " + (
        "Для YouTube, Telegram, Instagram и Threads настроен доступ через зарубежный сервер. Проверка выполняется при подключении."
        if svc.settings.comment_platform_proxy_url else
        "Для YouTube, Telegram, Instagram и Threads укажите адрес прокси на зарубежном сервере в настройках сервиса.")
    notice = '<div class="c-notice">Ответы публикуются после вашей проверки. Жалобы, неизвестные условия и спорные вопросы передаются человеку.<br>' + route + '</div>'
    if tab == "platforms":
        cards = []
        for key, item in PLATFORMS.items():
            channel = live.get(key)
            badge = "Подключено · " + channel.account_name if channel else ("Можно подключить" if item["live"] else "Ручные черновики")
            steps = ''
            for index, step in enumerate(item["steps"]):
                links = ''.join(f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(label)} ↗</a>'
                    for label, url in item.get("links", {}).get(index, []))
                steps += f'<li>{esc(step)}<div class="c-step-links">{links}</div></li>' if links else f'<li>{esc(step)}</li>' 
            docs = f'<a class="c-docs" href="{esc(item["docs"])}" target="_blank" rel="noopener noreferrer">Официальная документация ↗</a>' if item["docs"] else ''
            help_text = esc(item.get("help", ""))
            if item["live"]:
                controls = (f'<form class="c-connect" data-platform="{key}"><label>{esc(item["account_label"])}<input name="account_id" required value="{esc(channel.account_id if channel else "")}" autocomplete="off"></label>'
                    '<label>Токен доступа<input name="token" type="password" required autocomplete="new-password" placeholder="Хранится зашифрованным на сервере"></label>'
                    f'<span class="c-help">{help_text}</span><button class="btn primary" type="submit">Проверить и подключить</button></form>')
                if channel:
                    controls += f'<div class="c-actions"><button class="btn ghost" data-sync="{key}">Загрузить комментарии</button><button class="btn ghost" data-disconnect="{key}">Отключить</button></div>'
            else:
                controls = f'<a class="btn ghost" href="{base}?tab=test&platform={key}">Подготовить ответ</a>'
            cards.append(f'<section class="panel c-platform"><div class="c-card-head"><h2>{esc(item["name"])}</h2><span class="chip {"blue" if channel else ""}">{esc(badge)}</span></div><p class="c-meta">{"Доступ напрямую" if key in {"vk", "vk_video"} else "Через зарубежный сервер" if item["live"] else "Ручной режим"}</p><details class="c-guide"><summary>Как получить ID и токен · {len(item["steps"])} шага</summary><ol>{steps}</ol>{docs}</details><p>{esc(item["limits"])}</p>{controls}</section>')
        content = ('<section class="panel c-platform-intro"><h2>Подключите аккаунты бизнеса</h2>'
            '<p>Для каждой площадки нужны два значения: ID аккаунта и токен доступа — ключ, который площадка выдаёт для работы с комментариями. Это не пароль от аккаунта.</p>'
            '<p>Откройте инструкцию ниже, получите данные по ссылкам, заполните форму и нажмите «Проверить и подключить». Затем загрузите комментарии. В этой версии токены вводятся вручную; при истечении срока действия их нужно заменить.</p>'
            '<p>Для Google и Meta потребуется приложение в кабинете разработчика. Если его ещё нет, подготовку доступа выполняет владелец аккаунта или разработчик интеграции. Для ручной работы подключение не требуется: откройте «Проверка ответов».</p></section>'
            '<div class="c-platform-grid">' + ''.join(cards) + '</div>')
    elif tab == "agent":
        try:
            facts, _, _, _ = svc.knowledge(product_id)
            business_facts = ''.join(f'<li><b>{esc(f["id"].replace("profile.", ""))}</b><pre>{esc(f["text"])}</pre></li>' for f in facts if f["id"].startswith("profile."))
            readiness = '<span class="chip blue">Профиль бизнеса доступен</span>'
        except ValueError:
            business_facts, readiness = '<li>Заполните и подтвердите профиль через раздел «Компания» в боте управления.</li>', '<span class="chip">Нужны данные бизнеса</span>'
        tone_options = ''.join(f'<option value="{key}" {"selected" if config.tone == key else ""}>{name}</option>' for key, name in [("friendly", "Дружелюбный"), ("professional", "Деловой"), ("warm", "Заботливый")])
        owner_facts = '\n'.join(f.text for f in config.facts)
        content = f'''<div class="c-agent-grid"><section class="panel c-card"><h2>Поведение агента</h2><p>Сначала помочь, затем уточнить потребность и предложить подходящий шаг.</p>
        <form id="c-settings"><label>Тон общения<select name="tone">{tone_options}</select></label>
        <div class="c-fields"><label>Максимум символов<input name="max_length" type="number" min="100" max="1500" value="{config.max_length}" required></label><label class="c-check"><input name="emoji" type="checkbox" {"checked" if config.emoji else ""}> Использовать эмодзи</label></div>
        <label>Контакт поддержки для сложных случаев<input name="support_contact" value="{esc(config.support_contact)}" maxlength="500" placeholder="Например: @support_business"></label>
        <label>Следующий шаг для заинтересованного клиента<input name="next_step" value="{esc(config.next_step)}" maxlength="500" placeholder="Например: предложить подбор подходящего тарифа"></label>
        <label>Подтверждённые факты и FAQ<textarea name="facts" rows="6" placeholder="Один факт на строку: цены, условия, ответы на частые вопросы">{esc(owner_facts)}</textarea></label>
        <label>Примеры хороших ответов<textarea name="examples" rows="4" maxlength="6000">{esc(config.examples)}</textarea></label>
        <label>Что нельзя обещать<textarea name="forbidden_claims" rows="3" maxlength="3000">{esc(config.forbidden_claims)}</textarea></label>
        <label class="c-check"><input name="enabled" type="checkbox" {"checked" if config.enabled else ""}> Агент включён</label>
        <button class="btn primary" type="submit">Сохранить настройки</button></form></section>
        <aside class="panel c-card"><h2>Знания о {esc(product.name)}</h2>{readiness}<p>Профиль бизнеса читается перед каждым ответом. Дополнительные факты вводите только после проверки.</p>
        <details><summary>Данные из профиля</summary><ul class="c-knowledge">{business_facts}</ul></details>
        <h3>Правила ответа</h3><ul><li>Отвечать по существу, без давления.</li><li>Задавать один уместный вопрос о потребности.</li><li>Не выдумывать цены, скидки и гарантии.</li><li>Не собирать личные данные публично.</li><li>Жалобы и неизвестные условия — на проверку человеку.</li></ul>
        <p>Версия настроек: {settings_record.version if settings_record else "По умолчанию"}. Настройка через знания и примеры; переобучение модели не требуется.</p></aside></div>'''
    elif tab == "test":
        content = f'''<section class="panel c-card c-test"><h2>Проверьте, как агент отвечает</h2><p>Пробный ответ не публикуется. Используются текущий профиль бизнеса и сохранённые правила.</p>
        <form id="c-test"><label>Площадка<select name="platform">{options}</select></label>
        <label>Контекст публикации<textarea name="post_text" rows="3" maxlength="8000" placeholder="О чём пост и что уже обсудили"></textarea></label>
        <label>Комментарий<textarea name="text" rows="3" maxlength="8000" required placeholder="Например: Подойдёт ли ваш сервис для небольшой команды?"></textarea></label>
        <button class="btn primary" type="submit">Подготовить пробный ответ</button></form><div id="c-test-result" aria-live="polite"></div></section>'''
    else:
        query = select(BusinessComment).where(BusinessComment.product_id == product_id, BusinessComment.source != "test")
        if platform in PLATFORMS:
            query = query.where(BusinessComment.platform == platform)
        if status in STATUS:
            query = query.where(BusinessComment.status == status)
        rows = list(svc.db.scalars(query.order_by(BusinessComment.created_at.desc()).limit(100)))
        statuses = ''.join(f'<option value="{key}" {"selected" if status == key else ""}>{name}</option>' for key, name in STATUS.items())
        sync = ''.join(f'<button class="btn ghost" data-sync="{key}">Обновить {esc(PLATFORMS[key]["name"])}</button>' for key in live)
        cards = []
        for comment in rows:
            record = svc.db.get(Artifact, comment.reply_id) if comment.reply_id else None
            reply = json.loads(record.content) if record else {}
            terminal = comment.status in {"sending", "sent", "delivery_unknown", "skipped", "handled"}
            controls = ''
            if not terminal:
                controls = f'<button class="btn ghost" data-draft="{comment.id}">{"Подготовить заново" if record else "Подготовить ответ"}</button>'
                controls += f'<button class="btn ghost" data-close-comment="{comment.id}" data-state="handled">Передать человеку</button><button class="btn ghost" data-close-comment="{comment.id}" data-state="skipped">Пропустить</button>'
            reply_html = ''
            if record:
                copy_id = f'c-reply-{comment.id}'
                reply_html = f'<label>Ответ агента<textarea id="{copy_id}" rows="4" maxlength="{reply_limit(comment.platform, config.max_length)}" {"readonly" if terminal else ""}>{esc(reply.get("reply", ""))}</textarea></label><p class="c-reason">{esc(reply.get("reason", ""))}</p>'
                missing = reply.get("missing_information", [])
                if missing:
                    reply_html += f'<p class="c-warning">Нужно уточнить: {esc("; ".join(missing))}</p>'
                if reply.get("used_fact_ids"):
                    reply_html += f'<p class="c-meta">Источники: {esc(", ".join(reply["used_fact_ids"]))}</p>'
                reply_html += f'<button class="btn ghost" data-copy-reply="{copy_id}">Копировать ответ</button>'
                if not terminal and comment.source == "platform" and comment.platform in live and reply.get("action") != "skip":
                    reply_html += f'<button class="btn primary" data-send="{comment.id}" data-text-id="{copy_id}">Одобрить и отправить</button>'
            if comment.status == "delivery_unknown":
                reply_html += '<p class="c-warning">Проверьте комментарий на площадке. Повторная отправка заблокирована, чтобы не продублировать ответ.</p>'
            post = f'<details><summary>Контекст публикации</summary><p class="c-comment-text">{esc(comment.post_text)}</p></details>' if comment.post_text else ''
            sent = f'<p>Ответ подтверждён площадкой: {esc(comment.external_reply_id)}</p>' if comment.external_reply_id else ''
            cards.append(f'<article class="panel c-card"><div class="c-card-head"><b>{esc(PLATFORMS[comment.platform]["name"])}</b><span class="chip">{esc(STATUS.get(comment.status, comment.status))}</span></div>{post}<p class="c-comment-text">{esc(comment.text)}</p>{reply_html}{sent}<div class="c-actions">{controls}</div></article>')
        empty = '<section class="panel c-empty"><h2>Комментариев пока нет</h2><p>Подключите площадку и загрузите комментарии или добавьте один вручную.</p><a class="btn primary" href="?tab=platforms">Подключить площадку</a></section>'
        content = f'''<form method="get" class="c-filters"><input type="hidden" name="tab" value="inbox"><label>Площадка<select name="platform"><option value="all">Все площадки</option>{options}</select></label><label>Статус<select name="status"><option value="all">Все статусы</option>{statuses}</select></label><button class="btn ghost" type="submit">Показать</button></form><div class="c-actions">{sync}</div>
        <div class="c-inbox-grid"><div class="c-list">{''.join(cards) or empty}</div><aside class="panel c-card"><h2>Добавить комментарий</h2><p>Для ручного ответа и площадок без подключения.</p><form id="c-manual"><label>Площадка<select name="platform">{options}</select></label><label>Контекст поста<textarea name="post_text" rows="3" maxlength="8000"></textarea></label><label>Комментарий<textarea name="text" rows="4" maxlength="8000" required></textarea></label><button class="btn primary" type="submit">Добавить</button></form><p>Ручные ответы размещаются копированием.</p></aside></div>'''
    body_html = f'<link rel="stylesheet" href="/studio/assets/comments.css?v=2"><div id="comments-app" data-api="/products/{product_id}/comments"><nav class="c-tabs">{tabs}</nav><p id="c-feedback" role="status" aria-live="polite"></p>{notice}{content}</div><script src="/studio/assets/comments.js" defer></script>'
    return HTMLResponse(studio_page_shell(product, active="comments", title="Комментарии", subtitle="AI-консультант вашего бизнеса", body=body_html), headers={"Cache-Control": "no-store"})


@router.post("/products/{product_id}/comments/settings", dependencies=[Depends(same_origin)])
async def configure(product_id: UUID, request: Request, svc=Depends(service)):
    value = await body(request, AgentSettings)
    try:
        row = svc.configure(product_id, value)
        return {"version": row.version}
    except (ValueError, LookupError) as exc:
        raise error(exc)


@router.post("/products/{product_id}/comments/connect", dependencies=[Depends(same_origin)])
async def connect(product_id: UUID, request: Request, svc=Depends(service)):
    value = await body(request, ConnectInput)
    try:
        return public_channel(await svc.connect(product_id, value))
    except (ValueError, LookupError) as exc:
        svc.db.rollback()
        raise error(exc)


@router.post("/products/{product_id}/comments/channels/{platform}/{action}", dependencies=[Depends(same_origin)])
async def channel_action(product_id: UUID, platform: str, action: str, svc=Depends(service)):
    try:
        if platform not in PLATFORMS:
            raise LookupError("Площадка не найдена")
        if action == "sync":
            return {"imported": await svc.sync(product_id, platform)}
        if action == "disconnect":
            svc.disconnect(product_id, platform)
            return {"status": "disconnected"}
        raise LookupError("Действие не найдено")
    except (ValueError, LookupError) as exc:
        svc.db.rollback()
        raise error(exc)


def attach_llm(svc):
    try:
        svc.llm = LLMService(privacy_gateway=PrivacyGateway(), provider=build_llm_provider(svc.settings))
    except ValueError:
        raise HTTPException(503, "AI-провайдер не настроен на сервере") from None


@router.post("/products/{product_id}/comments/{mode}", dependencies=[Depends(same_origin)])
async def create_comment(product_id: UUID, mode: str, request: Request, svc=Depends(service)):
    if mode not in {"manual", "test"}:
        raise HTTPException(404, "Действие не найдено")
    value = await body(request, CommentInput)
    try:
        if mode == "test":
            attach_llm(svc)
        comment = svc.manual(product_id, value, simulation=mode == "test")
        if mode == "test":
            artifact = await svc.draft(product_id, comment.id)
            data = json.loads(artifact.content)
            return {key: data[key] for key in ("reply", "action", "reason", "used_fact_ids", "missing_information")}
        return {"id": str(comment.id)}
    except (ValueError, LookupError) as exc:
        raise error(exc)


@router.post("/products/{product_id}/comments/items/{comment_id}/{action}", dependencies=[Depends(same_origin)])
async def comment_action(product_id: UUID, comment_id: UUID, action: str, request: Request, svc=Depends(service)):
    try:
        if action == "draft":
            attach_llm(svc)
            record = await svc.draft(product_id, comment_id)
            return {"id": str(record.id)}
        if action == "send":
            value = await body(request, ReplyEdit)
            return {"external_reply_id": await svc.send(product_id, comment_id, value.reply)}
        if action in {"skipped", "handled"}:
            svc.close(product_id, comment_id, action)
            return {"status": action}
        raise LookupError("Действие не найдено")
    except (ValueError, LookupError) as exc:
        raise error(exc)

