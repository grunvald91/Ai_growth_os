"""Useful, read-only responses to rejected Studio browser commands."""
from types import SimpleNamespace
from urllib.parse import parse_qs
from uuid import UUID
import re

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.content_studio_auth import StudioOriginError


async def studio_origin_error(request: Request, exc: StudioOriginError):
    error_id = getattr(request.state, "studio_origin_error_id", "")
    message = (
        "Запрос не прошёл проверку адреса страницы. Данные не отправлены. "
        "Попробуйте отправить форму ещё раз. Если ошибка повторится, сообщите код ошибки поддержке."
        if exc.status_code == 403 else
        "Сервис временно не может принять форму. Попробуйте позже или сообщите код ошибки поддержке."
    )
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if not request.url.path.startswith("/studio/") or "text/html" not in request.headers.get("accept", ""):
        return JSONResponse({"detail": exc.detail, "message": message, "error_id": error_id},
                            status_code=exc.status_code, headers=headers)

    # CSRF runs after authentication. No database access and no business writes
    # happen here. Only the caller's submitted fields are reflected, escaped.
    from app.api.content_studio import esc, studio_page_shell
    from app.api.content_studio_ui import create_content_html
    match = re.fullmatch(r"/studio/products/([0-9a-fA-F-]{36})/create", request.url.path)
    product_match = re.match(r"/studio/products/([0-9a-fA-F-]{36})(?:/|$)", request.url.path)
    try:
        product_id = UUID(product_match[1]) if product_match else None
    except ValueError:
        product_id = None
    feedback = message + (f" Код ошибки: {error_id}." if error_id else "")
    if product_id and match:
        fields = {}
        if request.headers.get("content-type", "").split(";")[0].strip().lower() == "application/x-www-form-urlencoded":
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 1_000_000:
                    chunks = []
                    break
                chunks.append(chunk)
            try:
                values = parse_qs(b"".join(chunks).decode("utf-8"), keep_blank_values=True, max_num_fields=80)
                for key, limit in {"format": 40, "channel": 40, "topic": 300,
                                   "audience": 300, "editorial_brief": 3500}.items():
                    if values.get(key):
                        fields[key] = values[key][-1][:limit]
            except (UnicodeError, ValueError):
                pass
        body = create_content_html(product_id, fields=fields, error=feedback)
        response_html = studio_page_shell(SimpleNamespace(id=product_id, name="Компания"),
            active="create", title="Не удалось отправить задание",
            subtitle="Проверьте заполненные поля ниже и повторите отправку.", body=body)
    elif product_id:
        body = (f'<section class="sg-surface"><p role="alert">{esc(feedback)}</p>'
                '<p><button class="btn secondary" type="button" data-error-back>Вернуться к заполненной форме</button></p>'
                '</section><script>document.querySelector("[data-error-back]").onclick=()=>history.back();</script>')
        response_html = studio_page_shell(SimpleNamespace(id=product_id, name="Компания"),
            active="create", title="Не удалось отправить форму", subtitle="Запрос отклонён.", body=body)
    else:
        response_html = ('<!doctype html><html lang="ru"><meta charset="utf-8">'
                         '<meta name="viewport" content="width=device-width,initial-scale=1">'
                         '<title>Не удалось отправить форму · Growth Studio</title>'
                         '<link rel="stylesheet" href="/studio/assets/studio.css">'
                         f'<main class="main"><section class="sg-surface"><h1>Не удалось отправить форму</h1>'
                         f'<p role="alert">{esc(feedback)}</p><a class="btn secondary" href="/studio">Открыть студию</a>'
                         '</section></main></html>')
    return HTMLResponse(response_html, status_code=exc.status_code, headers=headers)
