"""The VK publication screen belongs to the authenticated ServGo Studio workspace."""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.content_studio import _owner_product, _studio_form, studio_page_shell
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.comments.service import CommentService
from app.comments.contracts import ConnectInput
from app.core.config import get_settings
from app.db.session import get_db
from app.models.domain import Artifact, Publication
from app.vk_service import platform_service_token, group_app_verified
from app.social_vk import (
    OAUTH_COOKIE, OAUTH_PATH, VKStore, approved_posts, begin_oauth,
    cancel, connected, connect_community, connect_photo_user_key,
    photo_upload_connection, finish_oauth, read_oauth_cookie, schedule,
    community_reference, resolve_community, resolve_publisher_community, verify_community_key, VKKeyVerificationError, client_id, redirect_uri,
    GROUP_OAUTH_COOKIE, GROUP_OAUTH_PATH, community_oauth_config,
    begin_community_oauth, read_community_oauth_cookie, finish_community_oauth,
)

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])
MOSCOW = ZoneInfo("Europe/Moscow")


VK_TOKEN_DEEPLINK_SCRIPT = r"""<script>
(() => {
  const input = document.getElementById("sg-vk-community-link");
  const mainInput = document.getElementById("sg-vk-oauth-community-link");
  const link = document.getElementById("sg-vk-token-link");
  const groupLink = document.getElementById("sg-vk-group-link");
  const mainGroupLink = document.getElementById("sg-vk-oauth-group-link");
  const copy = document.getElementById("sg-vk-token-copy");
  const hint = document.getElementById("sg-vk-token-help");
  if (!input || !link || !hint || !copy) return;
  let manualChange = false;

  function communityName(value) {
    const raw = value.trim();
    if (!raw) return null;
    let name = raw;
    if (/^(?:https?:\/\/)?(?:(?:www|m)\.)?vk\.(?:ru|com)\//i.test(raw)) {
      try {
        const url = new URL(/^https?:\/\//i.test(raw) ? raw : "https://" + raw);
        if (url.protocol !== "https:" || url.username || url.password ||
            !["vk.com", "www.vk.com", "m.vk.com", "vk.ru", "www.vk.ru", "m.vk.ru"].includes(url.hostname) ||
            (url.port && url.port !== "443")) return null;
        name = decodeURIComponent(url.pathname.replace(/^\/|\/$/g, ""));
      } catch (_) { return null; }
    } else if (raw.includes("/") || raw.includes(":") || raw.includes("?") || raw.includes("#")) {
      return null;
    }
    if (/^[1-9]\d{0,14}$/.test(name)) name = "club" + name;
    if (/^(?:id|app)\d+$/i.test(name)) return null;
    if (/^(?:club|public|event)\d+$/i.test(name) &&
        !/^(?:club|public|event)[1-9]\d{0,14}$/i.test(name)) return null;
    return /^(?:club|public|event)[1-9]\d{0,14}$/i.test(name) ||
           /^[A-Za-z][A-Za-z0-9_.]{2,99}$/.test(name) ? name : null;
  }

  function updateGroupLink(anchor, value) {
    if (!anchor) return;
    const name = communityName(value);
    if (!name) { anchor.removeAttribute("href"); anchor.hidden = true; }
    else { anchor.href = "https://vk.ru/" + encodeURIComponent(name); anchor.hidden = false; }
  }

  function update() {
    updateGroupLink(groupLink, input.value);
    updateGroupLink(mainGroupLink, mainInput ? mainInput.value : "");
    const name = communityName(input.value);
    // No href at all until we know which community the user is managing.
    // The old /groups fallback silently took the user to the wrong page.
    if (!name) {
      link.removeAttribute("href");
      link.hidden = true;
      copy.hidden = true;
      hint.textContent = input.value.trim()
        ? "Проверьте адрес: нужна ссылка на сообщество vk.com/название."
        : "Вставьте ссылку на своё сообщество выше — появится переход прямо к ключам API.";
      return;
    }
    link.href = "https://vk.ru/" + encodeURIComponent(name) + "?act=tokens";
    link.hidden = false;
    copy.hidden = false;
    hint.textContent = "Если ВКонтакте открыл приложение вместо настроек, скопируйте ссылку и вставьте её в адресную строку браузера.";
  }

  input.addEventListener("input", () => { manualChange = true; update(); });
  // Reuse the group address already entered in the one-click connection form.
  if (mainInput) {
    if (!input.value.trim() && mainInput.value.trim()) input.value = mainInput.value;
    mainInput.addEventListener("input", () => {
      if (!manualChange) { input.value = mainInput.value; update(); }
    });
  }
  copy.addEventListener("click", async () => {
    if (!link.href || link.hidden) return;
    try {
      await navigator.clipboard.writeText(link.href);
      hint.textContent = "Ссылка на настройки скопирована. Вставьте её в адресную строку браузера.";
    } catch (_) {
      hint.textContent = "Не удалось скопировать автоматически. Откройте ссылку на настройки выше.";
    }
  });
  update();
})();
</script>"""


def esc(value):
    return html.escape(str(value or ""), quote=True)


def feedback(key):
    return {"connected": "Сообщество VK подключено. Проверьте первую публикацию.",
            "scheduled": "Публикация поставлена в очередь.",
            "photo_checked": "VK разрешил запрос сервера загрузки фото. Полная публикация с фотографией подтверждается только после отправки тестового поста.",
            "photo_connected": "Пользовательский доступ к фотографиям сохранён и проверен. Теперь можно отправить один утверждённый пост с изображением.",
            "cancelled": "Запланированная публикация отменена.",
            "checked": "Результат ручной проверки сохранён.",
            "disconnected": "VK отключён, будущие публикации отменены.",
            "connected_key": "Подключены текстовые публикации и чтение комментариев. Отправку тестового поста и ответа нужно проверить отдельно.",
            "connected_via_vk": "Разрешение ВКонтакте сохранено. Реальная отправка ещё не подтверждена: опубликуйте один утверждённый тестовый пост.",
            "connected_post_only": "Ключ сообщества сохранён для текстовых публикаций. Чтение комментариев пока недоступно: сервисный ключ не настроен или проверка чтения не прошла.",
            }.get(key, "")


def _can_begin_community_oauth(settings, request: Request) -> bool:
    try:
        community_oauth_config(settings)
    except ValueError:
        return False
    if group_app_verified(settings):
        return True
    # Until the first real success, only an operator may try legacy OAuth.
    # Use exactly the same allowlist and organization scope as /studio/tune.
    return _is_tune_owner(settings, request)


def _is_tune_owner(settings, request: Request) -> bool:
    """Mirror /studio/tune's operator allowlist and company-scope rule."""
    login = getattr(request.state, "studio_login", None)
    allowed = {name.strip() for name in
               (settings.studio_tune_usernames or "").split(",") if name.strip()}
    return (bool(login) and login in allowed
            and getattr(request.state, "studio_product_ids", None) is None)


def _vk_owner_settings_link(settings, request: Request, product_id: UUID) -> str:
    """Only link to the operator-only screen if this account can access it."""
    if not _is_tune_owner(settings, request):
        return ""
    # Internal relative route; no external redirect, token or company data.
    return (' <a href="/studio/tune/vk" class="sg-footnote"'
            ' aria-label="Настройки подключения ВКонтакте в ServGo">'
            'Настроить подключение VK</a>')


def connection_error(key):
    return {
        "bad_link": "Не удалось распознать ссылку. Скопируйте адрес страницы сообщества из ВКонтакте.",
        "bad_key": "Ключ не указан либо содержит недопустимые символы. Вставьте ключ доступа из «Работа с API» вашего сообщества.",
        "group_lookup_failed": "Адрес принят, но ВКонтакте не подтвердил сообщество по короткому имени. Попробуйте ссылку вида vk.ru/club123456; ключ и разрешения проверяются отдельно.",
        "key_rights_failed": "Адрес сообщества принят, но VK не завершил проверку ключа. Это не обязательно означает недостаток разрешений; проверьте повторно после обновления страницы.",
        "key_permissions_unverified": "VK не смог подтвердить действительность ключа и его разрешения через API. Ключ не сохранён. Повторите попытку; дополнительные разрешения могут не требоваться.",
        "key_wall_missing": "VK ответил, но разрешение «Стена» в правах этого ключа не найдено. Проверьте выбранный ключ в разделе «Работа с API».",
        "key_group_unverified": "Право на стену подтверждено, но VK не подтвердил, что ключ принадлежит именно указанному сообществу. Ключ не сохранён. Если адрес и ключ верны, причина может быть в доступности метода VK API.",
        "save_failed": "Не удалось сохранить подключение. Проверьте настройки и повторите.",
        "already_connected": "Сообщество уже подключено через VK ID. Ограниченный ключ не заменит действующее подключение.",
        "vk_not_configured": "VK ID пока не настроен на сервере ServGo. Нужны ID приложения VK и разрешённый HTTPS-адрес возврата.",
        "vk_id_input": "Проверьте ссылку на сообщество и настройки VK ID. Адрес вида vk.com/название подходит.",
        "comments_connect_failed": "Не удалось проверить чтение комментариев VK. Проверьте сервисный ключ, доступность стены и права сообщества.",
        "photo_check_failed": "Подключение к VK активно, но право загрузки фото не подтверждено. Ключ сообщества может разрешать публикацию текста, но не фото. Для фото может понадобиться отдельный пользовательский доступ с правами photos и wall.",
        "photo_connect_failed": "Не удалось подтвердить пользовательский доступ к фото. Нужен ключ вашего собственного приложения VK API с правами photos и wall, принадлежащий администратору этого сообщества. Токен VK ID и ключ сообщества для этого не подходят.",
        "publish_not_confirmed": "Подтвердите отправку поста в подключённое сообщество ВКонтакте.",
        "publish_not_ready": "Публикация пока недоступна: проверьте подключение, состояние картинки и согласование поста.",
        "publish_duplicate": "Этот материал уже поставлен в очередь либо опубликован. Проверьте историю VK.",
        "publish_bad_time": "Укажите корректное время публикации в будущем.",
        "publish_disabled": "Автопубликация ВКонтакте отключена на сервере. Проверьте настройки работника.",

        "group_oauth_input": "Не удалось начать авторизацию сообщества VK. Проверьте ссылку или воспользуйтесь подключением по ключу.",
        "group_oauth_not_configured": "Авторизация через VK сейчас недоступна. Пока можно подключить ключ сообщества вручную.",
    }.get(key, "")


@router.get("/studio/products/{product_id}/vk", response_class=HTMLResponse)
def vk_page(product_id: UUID, request: Request, done: str = "", error: str = "",
            post_id: UUID | None = None, db: Session = Depends(get_db),
            settings=Depends(get_settings)):
    product = _owner_product(db, product_id)
    channel = connected(db, product_id)
    posts = approved_posts(db, product_id)
    options = "".join(
        f'<option value="{p.id}"{" selected" if p.id == post_id else ""}>{esc(p.title[:85])}</option>'
        for p in posts
    )
    rows = db.scalars(select(Publication).where(
        Publication.product_id == product_id, Publication.channel == "vk",
    ).order_by(Publication.created_at.desc()).limit(30))
    statuses = {"scheduled": "Запланировано", "sending": "Отправляется",
                "delivery_unknown": "Требует проверки", "published": "Опубликовано",
                "cancelled": "Отменено", "failed_before_send": "Не отправлено"}
    history = ""
    issues = {}
    for issue in db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.kind == "vk_publish_issue",
    ).order_by(Artifact.created_at.desc()).limit(60)):
        try:
            issue_data = json.loads(issue.content)
            issues.setdefault(issue_data.get("publication_id"), issue_data.get("reason", ""))
        except (ValueError, TypeError):
            continue
    for row in rows:
        buttons = ""
        if row.status == "scheduled":
            buttons = (f'<form method="post" action="/studio/products/{product_id}/vk/cancel">'
                       f'<input type="hidden" name="publication_id" value="{row.id}">'
                       '<button class="btn secondary small">Отменить</button></form>')
        elif row.status == "delivery_unknown":
            buttons = (f'<form method="post" action="/studio/products/{product_id}/vk/reconcile" class="sg-fields sg-compact-form">'
                       f'<input type="hidden" name="publication_id" value="{row.id}">'
                       '<label class="sg-field">Результат после проверки стены '
                       '<select name="outcome"><option value="published">Пост опубликован</option>'
                       '<option value="not_sent">Поста нет</option></select></label>'
                       '<label class="sg-field">ID поста (если опубликован) '
                       '<input name="post_id" type="number" min="1"></label>'
                       '<label><input type="checkbox" name="confirmed" value="yes" required> '
                       'Я лично проверил стену сообщества</label>'
                       '<button class="btn secondary small">Сохранить проверку</button></form>')
        stamp = row.scheduled_at.astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M") if row.scheduled_at else "—"
        reference = (f'<small>{esc(row.external_reference)}</small>' if row.external_reference else "")
        issue = issues.get(str(row.id), "") if row.status == "failed_before_send" else ""
        note = f'<small role="status">{esc(issue)}</small>' if issue else ""
        history += (f'<li class="sg-publication-row"><div><b>{esc(statuses.get(row.status, row.status))}</b>'
                    f'<small>{esc(stamp)} МСК</small>{reference}{note}</div>{buttons}</li>')
    alert = f'<p role="status" class="sg-notice">{esc(feedback(done))}</p>' if feedback(done) else ""
    if connection_error(error):
        alert += f'<p role="alert" class="sg-notice">{esc(connection_error(error))}</p>'
    if channel:
        current = (f'<div class="sg-channel-line"><span class="sg-link-dot">✓</span><div>'
                   f'<b>{esc(channel.account_name)}</b><small>Сообщество VK · ID {esc(channel.account_id)}</small></div>' +
                   ('<span class="sg-channel-status">Ключ сообщества · можно проверить текстовую публикацию</span></div>'
                    if channel.is_community_token else
                    '<span class="sg-channel-status">VK ID · права отправки не подтверждены</span></div>') +
                   f'<form method="post" action="/studio/products/{product_id}/vk/disconnect">'
                   '<button type="submit" class="btn secondary small">Отключить VK</button></form>')
        reply_channel = CommentService(db, settings).channel(product_id, "vk")
        replies_ready = bool(reply_channel and reply_channel.status == "connected"
                             and reply_channel.account_id == channel.account_id
                             and (not channel.is_community_token or platform_service_token(settings)))
        current += (
            f'<form method="post" action="/studio/products/{product_id}/vk/check-photo">'
            '<button type="submit" class="btn secondary small">Проверить доступ к фото</button>'
            '<small>Проверка без публикации поста и без загрузки файла.</small></form>'
        )
        photo_connection = photo_upload_connection(db, product_id, channel.account_id)
        current += (
            '<details class="sg-surface">'
            '<summary>Фото: ' + (
                'доступ подключён ✓' if photo_connection else 'настроить доступ к загрузке'
            ) + '</summary>'
            '<p class="sg-footnote">Для изображения VK API требует пользовательский ключ '
            'с разрешениями photos и wall. Ключ сообщества для публикации текста остаётся '
            'подключённым отдельно. Используйте только ключ вашего собственного VK-приложения '
            'и не получайте его через сторонние сервисы.</p>'
            f'<form method="post" action="/studio/products/{product_id}/vk/photo-key" '
            'class="sg-fields sg-compact-form">'
            '<label class="sg-field"><span>Пользовательский ключ VK API для фото</span>'
            '<input name="access_token" type="password" autocomplete="off" '
            'maxlength="8000" required placeholder="Ключ пользователя с photos и wall"></label>'
            '<button class="btn secondary" type="submit">'
            + ('Обновить доступ к фото' if photo_connection else 'Подключить фото')
            + '</button></form>'
            '<p class="sg-footnote">Проверка не публикует пост и не загружает изображение. '
            '<a href="https://dev.vk.com/ru/api/upload/wall-photo" '
            'target="_blank" rel="noopener noreferrer">Официальный порядок загрузки VK</a>.</p>'
            '</details>'
        )
        if channel.is_community_token and channel.expires_at:
            current += '<p class="sg-footnote">Доступ VK ограничен по сроку. Для продления подключите сообщество заново до окончания действия ключа.</p>'
        if replies_ready:
            current += '<p class="sg-footnote">Комментарии: подключены, отправка ответов не проверена.</p>'
        else:
            owner_help = (_vk_owner_settings_link(settings, request, product_id)
                          if channel.is_community_token and not platform_service_token(settings) else "")
            current += ('<p class="sg-footnote">Комментарии: не подключены.'
                        + owner_help + '</p>')
        if channel.is_community_token and platform_service_token(settings) and not replies_ready:
            current += (f'<form method="post" action="/studio/products/{product_id}/vk/connect-comments">'
                        '<button class="btn secondary" type="submit">Подключить чтение комментариев</button>'
                        '</form>')
    else:
        current = '<p class="sg-muted">Сообщество ещё не подключено.</p>'
    group_oauth_ready = _can_begin_community_oauth(settings, request)
    pilot_mode = group_oauth_ready and not group_app_verified(settings)
    settings_link = _vk_owner_settings_link(settings, request, product_id)
    oauth_first = (f"""
 <form method="post" action="/studio/products/{product_id}/vk/group-connect" class="sg-fields sg-compact-form">
  <label class="sg-field"><span>Ссылка на сообщество ВКонтакте <a id="sg-vk-oauth-group-link" target="_blank" rel="noopener noreferrer" hidden>Открыть указанное сообщество</a></span>
   <input id="sg-vk-oauth-community-link" type="text" name="community_link" maxlength="255" autocomplete="url"
     placeholder="https://vk.com/my_community" required></label>
  <button class="btn primary" type="submit">{"Проверить вход ВКонтакте" if pilot_mode else "Подключить ВКонтакте"}</button>
  <p class="sg-footnote">Вы перейдёте во ВКонтакте, чтобы подтвердить доступ ServGo.
  Ключи копировать не требуется. Подключение станет активным только после проверки прав.
  {"Пробный режим: совместимость с вашим VK-приложением ещё не подтверждена." if pilot_mode else ""}{settings_link if pilot_mode else ""}</p>
 </form>""" if group_oauth_ready else
 ('<p class="sg-footnote">Авторизация одним нажатием пока не настроена.'
  + settings_link
  + '</p>' if settings_link else
  '<p class="sg-footnote">Подключение одним нажатием пока недоступно. Используйте ключ сообщества ниже.</p>'))
    enabled = bool(settings.content_vk_autopost_enabled)
    can_schedule = bool(channel and posts and enabled)
    status = ("Отправка по расписанию включена на сервере."
              if enabled else "Отправка на сервере выключена. Планирование сохраняется, но публикации не уходят.")
    body = f"""
<section class="sg-surface">
 <div class="sg-section-head"><div><span class="sg-overline">КАНАЛЫ</span><h2>ВКонтакте</h2>
 <p>Скопируйте ссылку на ваше сообщество из ВКонтакте. Числовой ID искать не нужно.</p></div></div>
 {alert}{current}
 {oauth_first}
 <details {"open" if not group_oauth_ready else ""}><summary>{"Подключить по ключу сообщества (резервный способ)" if group_oauth_ready else "Подключить сообщество сейчас (по ключу)"}</summary>
 <form method="post" action="/studio/products/{product_id}/vk/community-key" class="sg-fields sg-compact-form">
  <label class="sg-field"><span>Ссылка на сообщество <a id="sg-vk-group-link" target="_blank" rel="noopener noreferrer" hidden>Открыть указанное сообщество</a></span>
   <input id="sg-vk-community-link" name="community_link" type="text" maxlength="255" autocomplete="off"
      placeholder="https://vk.com/my_community" required></label>
  <p class="sg-footnote" id="sg-vk-token-help" role="status" aria-live="polite">Вставьте ссылку на своё сообщество выше — появится переход прямо к ключам API.</p>
  <label class="sg-field"><span>Ключ сообщества <a id="sg-vk-token-link" target="_blank" rel="noopener noreferrer" hidden>Открыть ключи API моего сообщества</a></span>
   <input name="access_token" type="password" maxlength="8000" autocomplete="off"
      placeholder="Ключ из настроек сообщества" required></label>
  <button id="sg-vk-token-copy" type="button" class="btn secondary small" hidden>Скопировать ссылку на настройки в браузере</button>
  <button class="btn primary" type="submit">Проверить и подключить публикации</button>
 </form>
 <details open><summary>Как получить ключ сообщества? Пошаговая инструкция</summary>
  <p class="sg-footnote"><strong>Сначала включите двухфакторную аутентификацию (2FA) для VK ID.</strong>
  Это дополнительная защита аккаунта: при входе, кроме пароля, требуется ещё одно подтверждение.
  Если 2FA выключена, ВКонтакте предложит включить её перед созданием ключа.</p>
  <ol class="sg-footnote">
    <li>Откройте <strong>своё сообщество ВКонтакте</strong> (ссылка выше).</li>
    <li>Перейдите в <strong>Управление</strong> сообществом, затем нажмите <strong>«Дополнительно»</strong>.</li>
    <li>Откройте <strong>«Работа с API» → «Ключи доступа»</strong>.</li>
    <li>Нажмите <strong>«Создать ключ»</strong>, предоставьте необходимые права на управление и работу со стеной и подтвердите создание в VK.</li>
    <li>Скопируйте полученный ключ и вставьте его в поле <strong>«Ключ сообщества»</strong> выше.</li>
  </ol>
  <p class="sg-footnote">Если адрес содержит номер, например vk.ru/club123456, ServGo извлечёт ID автоматически — отдельно искать его не нужно.
  Ключ храните в секрете; никому не отправляйте его в сообщениях или чатах.</p>
 </details>
 </details>
 <p class="sg-footnote">Посты без изображения можно отправлять ключом сообщества. Для постов с фото ВКонтакте может потребовать авторизацию пользователя с правами photos и wall: ключ сообщества иногда не допускается к загрузке фото. Если ВК отклонит загрузку, ServGo покажет ошибку и НЕ опубликует один текст без фотографии. Для чтения комментариев может потребоваться отдельный доступ; успешное подключение ещё не доказывает права публикации.</p>
</section>
<section class="sg-surface">
 <div class="sg-section-head"><div><span class="sg-overline">АВТОПУБЛИКАЦИЯ</span>
 <h2>Готовые посты VK</h2><p>{esc(status)}</p></div></div>
 <form method="post" action="/studio/products/{product_id}/vk/schedule" class="sg-fields sg-compact-form">
  <label class="sg-field"><span>Утверждённый материал</span>
   <select name="post_id" required>{options}</select></label>
  <label class="sg-field"><span>Дата и время (Москва)</span>
   <input type="datetime-local" name="when"></label>
  <label><input type="checkbox" name="confirmed" value="yes" required>
   Я проверил материал и разрешаю публикацию в подключённом сообществе</label>
  <button name="mode" value="scheduled" class="btn secondary" type="submit"
   {"disabled" if not can_schedule else ""}>Запланировать</button>
  <button name="mode" value="now" class="btn primary" type="submit"
   {"disabled" if not can_schedule else ""}>Опубликовать сейчас</button>
 </form>
 <p class="sg-footnote">Отправляются утверждённые посты VK — с проверенной картинкой или без неё. Перед отправкой изображение проверяется снова. Если ключ не разрешает загрузку фотографии, текст отдельно не публикуется, а ошибка отображается в истории. Неизвестный результат отправки не повторяется автоматически.</p>
</section>
<section class="sg-surface">
 <h2>История VK</h2><ul class="sg-publication-list">
 {history or '<li class="sg-muted">Публикаций пока нет.</li>'}</ul>
</section>
"""
    body += VK_TOKEN_DEEPLINK_SCRIPT
    # This HTML contains same-origin POST forms protected by require_studio_csrf.
    # no-referrer can turn their Origin into "null" and suppress Referer in
    # browsers, so do not use it on the form page. same-origin keeps the
    # document URL private from third-party VK links while allowing own forms.
    return HTMLResponse(studio_page_shell(product, active="channels", title="Публикации ВКонтакте",
        subtitle="Авторизация, очередь и история отправок", body=body),
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "same-origin"})


@router.post("/studio/products/{product_id}/vk/group-connect")
async def vk_group_connect(product_id: UUID, request: Request,
                           db: Session = Depends(get_db), settings=Depends(get_settings)):
    _owner_product(db, product_id)
    if not _can_begin_community_oauth(settings, request):
        return RedirectResponse(
            f"/studio/products/{product_id}/vk?error=group_oauth_not_configured", status_code=303)
    fields = await _studio_form(request)
    try:
        url, cookie = await begin_community_oauth(
            settings, product_id, fields.get("community_link", ""),
            owner_login=getattr(request.state, "studio_login", None))
    except ValueError:
        return RedirectResponse(
            f"/studio/products/{product_id}/vk?error=group_oauth_input", status_code=303)
    response = RedirectResponse(url, status_code=303,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    response.set_cookie(GROUP_OAUTH_COOKIE, cookie, max_age=600,
                        path=GROUP_OAUTH_PATH,
                        secure=settings.app_env.casefold() in {"prod", "production"},
                        httponly=True, samesite="lax")
    return response


@router.get(GROUP_OAUTH_PATH)
async def vk_group_callback(request: Request, state: str = "", code: str = "",
                            error: str = "", db: Session = Depends(get_db),
                            settings=Depends(get_settings)):
    try:
        payload = read_community_oauth_cookie(
            settings, request.cookies.get(GROUP_OAUTH_COOKIE), state)
        if (payload.get("owner_login") is not None
                and payload["owner_login"] != getattr(request.state, "studio_login", None)):
            raise ValueError("Войдите в ServGo под тем же аккаунтом, с которого начали подключение")
        scope = getattr(request.state, "studio_product_ids", None)
        if scope is not None and payload["product_id"] not in scope:
            raise ValueError("Нет доступа к организации")
        _owner_product(db, UUID(payload["product_id"]))
        if error:
            raise ValueError("Подключение отменено во ВКонтакте")
        row = await finish_community_oauth(db, settings, payload, code)
        result = "connected_via_vk"
        if platform_service_token(settings):
            try:
                token = VKStore(settings.products_root).load_token(row.id)
                await CommentService(db, settings).connect(
                    row.product_id, ConnectInput(platform="vk",
                        account_id=payload["group_id"], token=token))
                result = "connected_key"
            except (ValueError, LookupError):
                db.rollback()  # publication key is already committed
        response = RedirectResponse(
            f"/studio/products/{payload['product_id']}/vk?done={result}", status_code=303)
    except (ValueError, LookupError) as exc:
        db.rollback()
        response = HTMLResponse(
            '<!doctype html><html lang="ru"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Подключение ВКонтакте</title><p>Не удалось подтвердить доступ: '
            + esc(str(exc))
            + '</p><p>Вернитесь в Studio. Можно использовать подключение по ключу.</p></html>',
            status_code=422)
    response.delete_cookie(GROUP_OAUTH_COOKIE, path=GROUP_OAUTH_PATH)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.post("/studio/products/{product_id}/vk/connect")
async def vk_connect(product_id: UUID, request: Request, db: Session = Depends(get_db),
                     settings=Depends(get_settings)):
    _owner_product(db, product_id)
    fields = await _studio_form(request)
    try:
        url, cookie = begin_oauth(settings, product_id, fields.get("community_link", fields.get("group_id", "")),
                                  owner_login=getattr(request.state, "studio_login", None))
    except ValueError as exc:
        key = "vk_not_configured" if ("SOCIAL_VK_CLIENT_ID" in str(exc) or "HTTPS origin VK" in str(exc)) else "vk_id_input"
        return RedirectResponse(f"/studio/products/{product_id}/vk?error={key}", status_code=303)
    response = RedirectResponse(url, status_code=303,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    response.set_cookie(OAUTH_COOKIE, cookie, max_age=600, path=OAUTH_PATH,
                        secure=settings.app_env.casefold() in {"prod", "production"},
                        httponly=True, samesite="lax")
    return response


@router.post("/studio/products/{product_id}/vk/community-key")
async def vk_community_key(product_id: UUID, request: Request,
                           db: Session = Depends(get_db), settings=Depends(get_settings)):
    _owner_product(db, product_id)
    fields = await _studio_form(request)
    token = fields.get("access_token", "")
    reference = fields.get("community_link", fields.get("group_id", ""))
    try:
        community_reference(reference)
    except ValueError:
        return RedirectResponse(f"/studio/products/{product_id}/vk?error=bad_link", status_code=303)
    if not token or len(token) > 8000 or any(ord(ch) < 32 for ch in token):
        return RedirectResponse(f"/studio/products/{product_id}/vk?error=bad_key", status_code=303)
    try:
        account_id, name = await resolve_publisher_community(token, reference)
    except ValueError:
        return RedirectResponse(f"/studio/products/{product_id}/vk?error=group_lookup_failed",
                                status_code=303)
    try:
        await verify_community_key(token, account_id)
    except VKKeyVerificationError as exc:
        reason_to_error = {
            "permissions_unverified": "key_permissions_unverified",
            "wall_missing": "key_wall_missing",
            "group_unverified": "key_group_unverified",
        }
        return RedirectResponse(
            f"/studio/products/{product_id}/vk?error={reason_to_error[exc.reason]}",
            status_code=303)
    except ValueError:
        return RedirectResponse(f"/studio/products/{product_id}/vk?error=key_rights_failed",
                                status_code=303)
    existing = connected(db, product_id)
    if existing is not None and not existing.is_community_token:
        return RedirectResponse(f"/studio/products/{product_id}/vk?error=already_connected",
                                status_code=303)
    try:
        connect_community(db, settings, product_id, account_id, token, name=name)
    except (LookupError, ValueError):
        db.rollback()
        return RedirectResponse(f"/studio/products/{product_id}/vk?error=save_failed", status_code=303)
    # A community key can write text, but reading public posts needs a separate
    # operator-owned VK service key. Never claim comments work without probing.
    if not platform_service_token(settings):
        return RedirectResponse(f"/studio/products/{product_id}/vk?done=connected_post_only",
                                status_code=303)
    try:
        await CommentService(db, settings).connect(
            product_id, ConnectInput(platform="vk", account_id=account_id, token=token))
    except (LookupError, ValueError):
        db.rollback()
        return RedirectResponse(f"/studio/products/{product_id}/vk?done=connected_post_only",
                                status_code=303)
    return RedirectResponse(f"/studio/products/{product_id}/vk?done=connected_key",
                            status_code=303)


@router.post("/studio/products/{product_id}/vk/connect-comments")
async def vk_connect_comments(product_id: UUID, db: Session = Depends(get_db),
                              settings=Depends(get_settings)):
    """Reconnect an already verified community without asking for its key again."""
    _owner_product(db, product_id)
    channel = connected(db, product_id)
    if channel is None or not channel.is_community_token or not platform_service_token(settings):
        return RedirectResponse(f"/studio/products/{product_id}/vk?error=comments_connect_failed",
                                status_code=303)
    try:
        key = VKStore(settings.products_root).load_token(channel.id)
        await CommentService(db, settings).connect(
            product_id, ConnectInput(platform="vk", account_id=channel.account_id, token=key))
    except (ValueError, LookupError):
        db.rollback()
        return RedirectResponse(f"/studio/products/{product_id}/vk?error=comments_connect_failed",
                                status_code=303)
    return RedirectResponse(f"/studio/products/{product_id}/vk?done=connected_key", status_code=303)


@router.get(OAUTH_PATH)
async def vk_callback(request: Request, state: str = "", code: str = "", device_id: str = "",
                      error: str = "", db: Session = Depends(get_db),
                      settings=Depends(get_settings)):
    # Even an admin's OAuth callback must retain the original business scope.
    try:
        data = read_oauth_cookie(settings, request.cookies.get(OAUTH_COOKIE), state)
        if data.get("owner_login") is not None and data["owner_login"] != getattr(request.state, "studio_login", None):
            raise ValueError("Вы вошли под другим аккаунтом ServGo. Начните подключение VK заново")
        scope = getattr(request.state, "studio_product_ids", None)
        if scope is not None and data["product_id"] not in scope:
            raise ValueError("Нет доступа к этой организации")
        if error:
            raise ValueError("Авторизация VK была отменена")
        channel = await finish_oauth(db, settings, data, state, code, device_id)
        response = RedirectResponse(
            f"/studio/products/{channel.product_id}/vk?done=connected", status_code=303)
    except (ValueError, LookupError) as exc:
        db.rollback()
        response = HTMLResponse(
            '<!doctype html><html lang="ru"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Подключение VK</title><p>Не удалось подключить ВКонтакте: '
            + esc(str(exc)) + '</p><p>Вернитесь в ServGo Studio и попробуйте заново.</p></html>',
            status_code=422)
    response.delete_cookie(OAUTH_COOKIE, path=OAUTH_PATH)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.post("/studio/products/{product_id}/vk/photo-key")
async def vk_connect_photo_key(product_id: UUID, request: Request,
                               db: Session = Depends(get_db),
                               settings=Depends(get_settings)):
    _owner_product(db, product_id)
    fields = await _studio_form(request)
    try:
        await connect_photo_user_key(
            db, settings, product_id, fields.get("access_token", ""),
        )
    except (ValueError, LookupError, OSError):
        db.rollback()
        return RedirectResponse(
            f"/studio/products/{product_id}/vk?error=photo_connect_failed",
            status_code=303,
        )
    return RedirectResponse(
        f"/studio/products/{product_id}/vk?done=photo_connected", status_code=303,
    )


@router.post("/studio/products/{product_id}/vk/check-photo")
async def vk_check_photo(product_id: UUID, db: Session = Depends(get_db),
                         settings=Depends(get_settings)):
    from app.social_vk import verify_photo_upload_access
    _owner_product(db, product_id)
    try:
        await verify_photo_upload_access(db, settings, product_id)
    except ValueError:
        db.rollback()
        return RedirectResponse(
            f"/studio/products/{product_id}/vk?error=photo_check_failed", status_code=303,
        )
    return RedirectResponse(
        f"/studio/products/{product_id}/vk?done=photo_checked", status_code=303,
    )


@router.post("/studio/products/{product_id}/vk/schedule")
async def vk_schedule(product_id: UUID, request: Request, db: Session = Depends(get_db),
                      settings=Depends(get_settings)):
    _owner_product(db, product_id)
    fields = await _studio_form(request)
    if fields.get("confirmed") != "yes":
        return RedirectResponse(
            f"/studio/products/{product_id}/vk?error=publish_not_confirmed", status_code=303,
        )
    if not settings.content_vk_autopost_enabled:
        return RedirectResponse(
            f"/studio/products/{product_id}/vk?error=publish_disabled", status_code=303,
        )
    try:
        post_id = UUID(fields.get("post_id", ""))
        if fields.get("mode") == "now":
            when = datetime.now(timezone.utc) + timedelta(seconds=1)
        elif fields.get("mode") == "scheduled":
            when = datetime.fromisoformat(fields.get("when", "")).replace(tzinfo=MOSCOW)
        else:
            raise ValueError("Неизвестный режим публикации")
        schedule(db, product_id, post_id, when, products_root=settings.products_root)
    except (ValueError, LookupError, TypeError) as exc:
        db.rollback()
        message = str(exc).lower()
        code = ("publish_duplicate" if "уже назначена" in message else
                "publish_bad_time" if ("дата" in message or "время" in message or
                                       "isoformat" in message) else
                "publish_not_ready")
        return RedirectResponse(
            f"/studio/products/{product_id}/vk?error={code}", status_code=303,
        )
    return RedirectResponse(f"/studio/products/{product_id}/vk?done=scheduled", status_code=303)


@router.post("/studio/products/{product_id}/vk/cancel")
async def vk_cancel(product_id: UUID, request: Request, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    fields = await _studio_form(request)
    try:
        cancel(db, product_id, UUID(fields.get("publication_id", "")))
    except (LookupError, ValueError) as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    return RedirectResponse(f"/studio/products/{product_id}/vk?done=cancelled", status_code=303)


@router.post("/studio/products/{product_id}/vk/reconcile")
async def vk_reconcile(product_id: UUID, request: Request, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    fields = await _studio_form(request)
    if fields.get("confirmed") != "yes":
        raise HTTPException(422, "Сначала проверьте стену VK")
    try:
        pub = db.scalar(select(Publication).where(
            Publication.product_id == product_id, Publication.channel == "vk",
            Publication.id == UUID(fields.get("publication_id", ""))).with_for_update())
        if pub is None or pub.status != "delivery_unknown":
            raise ValueError("Нет неопределённой отправки для проверки")
        if fields.get("outcome") == "published":
            post_id = int(fields.get("post_id") or "")
            if post_id <= 0:
                raise ValueError("Нужен ID опубликованного поста")
            intent = db.scalar(select(Artifact).where(
                Artifact.product_id == product_id, Artifact.kind == "vk_publish_intent",
                Artifact.title == "VK schedule " + str(pub.id)))
            if intent is None:
                raise ValueError("Не найден первоначальный адрес публикации")
            details = json.loads(intent.content)
            original_group = str(details.get("group_id") or "")
            if not original_group.isdigit() or original_group.startswith("0"):
                raise ValueError("Нет действительного ID сообщества")
            pub.status = "published"
            pub.external_reference = f"vk:-{original_group}_{post_id}"
        elif fields.get("outcome") == "not_sent":
            pub.status = "failed_before_send"
        else:
            raise ValueError("Неизвестный результат проверки")
        db.commit()
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    return RedirectResponse(f"/studio/products/{product_id}/vk?done=checked", status_code=303)


@router.post("/studio/products/{product_id}/vk/disconnect")
async def vk_disconnect(product_id: UUID, db: Session = Depends(get_db),
                        settings=Depends(get_settings)):
    _owner_product(db, product_id)
    sending = db.scalar(select(Publication.id).where(
        Publication.product_id == product_id, Publication.channel == "vk",
        Publication.status == "sending").limit(1))
    if sending:
        raise HTTPException(409, "Публикация ещё отправляется. Проверьте её статус")
    channel = connected(db, product_id)
    if channel:
        service = CommentService(db, settings)
        if service.channel(product_id, "vk"):
            try:
                service.disconnect(product_id, "vk")
            except ValueError as exc:
                db.rollback()
                raise HTTPException(409, str(exc)) from None
        publishers = list(db.scalars(select(Artifact).where(
            Artifact.product_id == product_id,
            Artifact.kind == "vk_publish_connection",
            Artifact.status == "approved")))
        for publisher in publishers:
            publisher.status = "rejected"
            VKStore(settings.products_root).forget(publisher.id)
        photos = list(db.scalars(select(Artifact).where(
            Artifact.product_id == product_id,
            Artifact.kind == "vk_photo_upload_connection",
            Artifact.status == "approved",
        )))
        for photo in photos:
            photo.status = "rejected"
            VKStore(settings.products_root).forget(photo.id)
    for pub in db.scalars(select(Publication).where(
        Publication.product_id == product_id, Publication.channel == "vk",
        Publication.status == "scheduled")):
        pub.status = "cancelled"
    db.commit()
    return RedirectResponse(f"/studio/products/{product_id}/vk?done=disconnected", status_code=303)
