"""Owner review and lifecycle actions for ServGo Mail campaigns."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .mail_campaign_api import campaign
from .mail_campaign_dispatch import sender_for_campaign
from .mail_campaign_store import audit, initialize, utcnow
from .owner_mail import db_open, mail_owner

router = APIRouter(prefix="/owner-mail/api/campaigns", tags=["mail-reviews"])


class ReviewSelection(BaseModel):
    recipient_ids: list[str] = Field(min_length=1, max_length=1000)


class EditedMessage(BaseModel):
    subject: str = Field(min_length=3, max_length=180)
    body: str = Field(min_length=30, max_length=12000)


class CampaignLaunch(BaseModel):
    start_at: datetime | None = None
    legal_basis_confirmed: bool = False


class ReplyTag(BaseModel):
    label: Literal["interested", "neutral", "not_interested"]


@router.post("/{campaign_id}/prepare")
def prepare(campaign_id: str, _=Depends(mail_owner)):
    with db_open() as db:
        initialize(db)
        row = campaign(db, campaign_id)
        if row["status"] not in ("draft", "paused"):
            raise HTTPException(409, "Приостановите кампанию для подготовки")
        updated = db.execute(
            "UPDATE campaign_recipient SET status='research_queued' "
            "WHERE campaign_id=? AND status IN ('new','draft_error')",
            (campaign_id,),
        )
        audit(db, campaign_id, "research_queued", info=str(updated.rowcount))
        return {"queued": updated.rowcount}


@router.patch("/{campaign_id}/recipients/{recipient_id}")
def edit_message(campaign_id: str, recipient_id: str, data: EditedMessage,
                 _=Depends(mail_owner)):
    if any(ord(c) < 32 for c in data.subject):
        raise HTTPException(422, "Недопустимая тема")
    with db_open() as db:
        initialize(db)
        row = campaign(db, campaign_id)
        if row["status"] not in ("draft", "paused"):
            raise HTTPException(409, "Приостановите кампанию для редактирования")
        updated = db.execute(
            "UPDATE campaign_recipient SET subject=?,body=?,status='review' "
            "WHERE campaign_id=? AND id=? AND status IN ('review','approved')",
            (data.subject.strip(), data.body.strip(), campaign_id, recipient_id),
        )
        if updated.rowcount != 1:
            raise HTTPException(409, "Черновик ещё не готов")
        audit(db, campaign_id, "edited", recipient_id)
    return {"status": "review"}


@router.post("/{campaign_id}/approve")
def approve(campaign_id: str, selection: ReviewSelection, _=Depends(mail_owner)):
    unique = list(dict.fromkeys(selection.recipient_ids))
    placeholders = ",".join("?" for _ in unique)
    with db_open() as db:
        initialize(db)
        c = campaign(db, campaign_id)
        if c["status"] not in ("draft", "paused"):
            raise HTTPException(409, "Приостановите кампанию для согласования")
        rows = db.execute(
            "SELECT id FROM campaign_recipient WHERE campaign_id=? "
            "AND status='review' AND subject!='' AND body!='' "
            "AND id IN (" + placeholders + ")",
            (campaign_id, *unique),
        ).fetchall()
        if {row["id"] for row in rows} != set(unique):
            raise HTTPException(409, "Некоторые письма не готовы к согласованию")
        for rid in unique:
            db.execute(
                "UPDATE campaign_recipient SET status='approved' WHERE id=?",
                (rid,),
            )
            audit(db, campaign_id, "approved", rid)
    return {"approved": len(unique)}


@router.post("/{campaign_id}/launch")
def launch(campaign_id: str, details: CampaignLaunch, _=Depends(mail_owner)):
    when = details.start_at
    if when and when.tzinfo is None:
        raise HTTPException(422, "Время должно включать часовой пояс")
    start_at = when.astimezone(timezone.utc).isoformat() if when else ""
    if not details.legal_basis_confirmed:
        raise HTTPException(422, "Подтвердите законное основание связи с выбранными адресатами")
    with db_open() as db:
        initialize(db)
        c = campaign(db, campaign_id)
        if c["status"] not in ("draft", "paused"):
            raise HTTPException(409, "Кампания уже запущена или завершена")
        if c["company_id"] and not sender_for_campaign(c["company_id"])[0]:
            raise HTTPException(409, "Для этой организации не подтверждён адрес отправителя. "
                                "Настройте SERVGO_MAIL_COMPANY_SENDERS на сервере")
        approved = db.execute(
            "SELECT count(*) FROM campaign_recipient "
            "WHERE campaign_id=? AND status='approved'", (campaign_id,),
        ).fetchone()[0]
        if not approved:
            raise HTTPException(409, "Нет согласованных писем")
        db.execute(
            "UPDATE campaign SET status='running',not_before=?,updated_at=? WHERE id=?",
            (start_at, utcnow(), campaign_id),
        )
        audit(db, campaign_id, "legal_basis_confirmed_by_owner")
        audit(db, campaign_id, "launched", info=start_at)
    return {"status": "running", "approved": approved}


@router.post("/{campaign_id}/pause")
def pause(campaign_id: str, _=Depends(mail_owner)):
    with db_open() as db:
        initialize(db)
        c = campaign(db, campaign_id)
        if c["status"] != "running":
            raise HTTPException(409, "Кампания не запущена")
        db.execute("UPDATE campaign SET status='paused',updated_at=? WHERE id=?",
                   (utcnow(), campaign_id))
        audit(db, campaign_id, "paused")
    return {"status": "paused"}


@router.post("/{campaign_id}/finish")
def finish(campaign_id: str, _=Depends(mail_owner)):
    with db_open() as db:
        initialize(db)
        c = campaign(db, campaign_id)
        if c["status"] not in ("draft", "running", "paused"):
            raise HTTPException(409, "Кампания уже закрыта")
        db.execute("UPDATE campaign SET status='finished',updated_at=? WHERE id=?",
                   (utcnow(), campaign_id))
        audit(db, campaign_id, "finished")
    return {"status": "finished"}


@router.post("/{campaign_id}/recipients/{recipient_id}/exclude")
def exclude(campaign_id: str, recipient_id: str, _=Depends(mail_owner)):
    with db_open() as db:
        initialize(db)
        item = db.execute(
            "SELECT email FROM campaign_recipient WHERE campaign_id=? AND id=?",
            (campaign_id, recipient_id),
        ).fetchone()
        if not item:
            raise HTTPException(404, "Адресат не найден")
        db.execute("INSERT OR REPLACE INTO campaign_exclusions VALUES (?,?,?)",
                   (item["email"], "owner", utcnow()))
        db.execute(
            "UPDATE campaign_recipient SET status='excluded' "
            "WHERE email=? AND status IN "
            "('new','research_queued','draft_error','review','approved')",
            (item["email"],),
        )
        audit(db, campaign_id, "excluded", recipient_id)
    return {"excluded": True}
