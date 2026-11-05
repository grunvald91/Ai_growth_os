"""Safe unsubscribe handling for ServGo Mail campaign recipients."""
from __future__ import annotations

from html import escape
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse

from .mail_campaign_store import audit, initialize, utcnow
from .owner_mail import ADDRESS, db_open, verify

router = APIRouter(prefix="/owner-mail", tags=["mail-unsubscribe"])


def check_token(token: str) -> dict:
    item = verify(token, "campaign-unsubscribe")
    address = item.get("address", "")
    if not isinstance(address, str) or not ADDRESS.fullmatch(address):
        raise HTTPException(400, "Некорректная ссылка")
    return item


@router.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_page(token: str):
    check_token(token)
    page = (
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<meta name="robots" content="noindex,nofollow">'
        '<title>Отписка от ServGo</title>'
        '<body style="font:16px system-ui;max-width:440px;margin:12vh auto;padding:24px">'
        '<h1>Письма ServGo</h1>'
        '<p>Чтобы больше не получать эти сообщения, подтвердите отписку.</p>'
        '<form method="post"><input type="hidden" name="token" value="'
        + escape(token, quote=True) +
        '"><button type="submit">Отписаться</button></form></body></html>'
    )
    return HTMLResponse(page, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"
    })


@router.post("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(request: Request):
    # RFC 8058 uses a POST with token in query, and form clients submit a token.
    raw = (await request.body())[:8192].decode("utf-8", errors="replace")
    fields = parse_qs(raw, keep_blank_values=True)
    token = fields.get("token", [request.query_params.get("token", "")])[0]
    item = check_token(token)
    address = item["address"].lower()
    with db_open() as db:
        initialize(db)
        db.execute(
            "INSERT OR REPLACE INTO campaign_exclusions VALUES (?,?,?)",
            (address, "unsubscribe", utcnow()),
        )
        db.execute(
            "UPDATE campaign_recipient SET status='excluded' WHERE email=? "
            "AND status IN ('new','research_queued','draft_error','review','approved')",
            (address,),
        )
        audit(db, item.get("campaign_id", ""), "unsubscribed",
              item.get("sub"))
    return HTMLResponse(
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<h1>Вы отписаны от писем ServGo</h1></html>',
        headers={"Cache-Control": "no-store"},
    )
