"""Owner-authenticated campaign creation and recipient intake."""
from __future__ import annotations

import sqlite3
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .mail_campaign_store import (
    AUTOMATION_KEY, audit, automation_status, initialize, put_setting, summary, utcnow,
)
from .owner_mail import ADDRESS, db_open, mail_owner

router = APIRouter(prefix="/owner-mail/api/campaigns", tags=["mail-campaigns"])


class CampaignInput(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    objective: str = Field(default="Персональное предложение ServGo", max_length=800)
    min_interval_seconds: int = Field(default=90, ge=30, le=86400)


class RecipientInput(BaseModel):
    email: str = Field(min_length=5, max_length=250)
    company: str = Field(default="", max_length=160)
    website: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=2500)


class AutomationInput(BaseModel):
    enabled: bool


class RecipientList(BaseModel):
    recipients: list[RecipientInput] = Field(min_length=1, max_length=1000)


def campaign(db, campaign_id: str):
    found = db.execute("SELECT * FROM campaign WHERE id=?", (campaign_id,)).fetchone()
    if found is None:
        raise HTTPException(404, "Кампания не найдена")
    return found


@router.post("")
def create_campaign(data: CampaignInput, _=Depends(mail_owner)):
    campaign_id = uuid4().hex
    with db_open() as db:
        initialize(db)
        db.execute(
            "INSERT INTO campaign (id,name,objective,status,created_at,updated_at,not_before,min_interval_seconds)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (campaign_id, data.name.strip(), data.objective.strip(),
             "draft", utcnow(), utcnow(), "", data.min_interval_seconds),
        )
        audit(db, campaign_id, "created")
    return {"id": campaign_id}


@router.get("")
def list_campaigns(_=Depends(mail_owner)):
    with db_open() as db:
        initialize(db)
        items = db.execute(
            "SELECT * FROM campaign ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
        return [{"campaign": dict(item), "metrics": summary(db, item["id"])}
                for item in items]


@router.get("/automation")
def get_automation(_=Depends(mail_owner)):
    with db_open() as db:
        initialize(db)
        return automation_status(db)


@router.put("/automation")
def set_automation(data: AutomationInput, _=Depends(mail_owner)):
    with db_open() as db:
        initialize(db)
        put_setting(db, AUTOMATION_KEY, "on" if data.enabled else "off")
        audit(db, "", "automation_on" if data.enabled else "automation_off")
        return automation_status(db)


@router.get("/{campaign_id}")
def campaign_detail(campaign_id: str, _=Depends(mail_owner)):
    with db_open() as db:
        initialize(db)
        item = campaign(db, campaign_id)
        recipients = db.execute(
            "SELECT * FROM campaign_recipient WHERE campaign_id=? "
            "ORDER BY created_at,id LIMIT 10000", (campaign_id,)
        ).fetchall()
        return {
            "campaign": dict(item),
            "metrics": summary(db, campaign_id),
            "recipients": [dict(row) for row in recipients],
        }


@router.post("/{campaign_id}/recipients")
def add_recipients(campaign_id: str, data: RecipientList, _=Depends(mail_owner)):
    outcome = {"added": 0, "duplicates": 0, "invalid": 0, "excluded": 0}
    with db_open() as db:
        initialize(db)
        item = campaign(db, campaign_id)
        if item["status"] not in ("draft", "paused"):
            raise HTTPException(409, "Приостановите кампанию для изменения списка")
        current = db.execute(
            "SELECT count(*) FROM campaign_recipient WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()[0]
        if current + len(data.recipients) > 10000:
            raise HTTPException(422, "Максимум 10 000 адресов в кампании")

        for r in data.recipients:
            address = r.email.strip().lower()
            if not ADDRESS.fullmatch(address):
                outcome["invalid"] += 1
                continue
            if r.website:
                try:
                    site = urlsplit(r.website)
                    if (site.scheme != "https" or not site.hostname or
                            site.username or site.password or site.port not in (None, 443)):
                        outcome["invalid"] += 1
                        continue
                except ValueError:
                    outcome["invalid"] += 1
                    continue
            if db.execute("SELECT 1 FROM campaign_exclusions WHERE email=?",
                          (address,)).fetchone():
                outcome["excluded"] += 1
                continue
            try:
                db.execute(
                    "INSERT INTO campaign_recipient "
                    "(id,campaign_id,email,company,website,notes,created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (uuid4().hex, campaign_id, address, r.company.strip(),
                     r.website.strip(), r.notes.strip(), utcnow()),
                )
                outcome["added"] += 1
            except sqlite3.IntegrityError:
                outcome["duplicates"] += 1
        audit(db, campaign_id, "import",
              info=",".join(str(outcome[k]) for k in ("added","duplicates","invalid","excluded")))
    return outcome
