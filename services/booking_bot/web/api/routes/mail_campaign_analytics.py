"""Campaign reporting, owner reply classification, and inbox reply correlation."""
from __future__ import annotations

import email
import email.policy
import imaplib
import os
import re
from email.utils import parseaddr
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .mail_campaign_store import audit, initialize, summary, utcnow
from .owner_mail import db_open, mail_owner

router = APIRouter(prefix="/owner-mail/api/campaigns", tags=["mail-analytics"])


class ReplyLabel(BaseModel):
    status: Literal["interested", "neutral", "not_interested"]


@router.get("/{campaign_id}/analytics")
def report(campaign_id: str, _=Depends(mail_owner)):
    with db_open() as db:
        initialize(db)
        if not db.execute("SELECT 1 FROM campaign WHERE id=?",
                          (campaign_id,)).fetchone():
            raise HTTPException(404, "Кампания не найдена")
        daily = db.execute("""
            SELECT substr(occurred_at,1,10) AS date,
                   event,COUNT(*) AS count
            FROM campaign_event
            WHERE campaign_id=?
              AND event IN ('send_attempt','smtp_accepted','smtp_rejected',
                            'smtp_unknown','reply')
            GROUP BY date,event ORDER BY date
        """, (campaign_id,)).fetchall()
        return {"summary": summary(db, campaign_id),
                "daily": [dict(row) for row in daily]}


@router.post("/{campaign_id}/recipients/{recipient_id}/reply")
def label_reply(campaign_id: str, recipient_id: str, label: ReplyLabel,
                _=Depends(mail_owner)):
    with db_open() as db:
        initialize(db)
        item = db.execute(
            "SELECT email FROM campaign_recipient WHERE campaign_id=? AND id=?",
            (campaign_id, recipient_id),
        ).fetchone()
        if not item:
            raise HTTPException(404, "Адресат не найден")
        db.execute("""
            UPDATE campaign_recipient SET reply_at=CASE WHEN reply_at=''
              THEN ? ELSE reply_at END,reply_status=?
            WHERE id=?
        """, (utcnow(), label.status, recipient_id))
        audit(db, campaign_id, "reply_review", recipient_id, label.status)
        if label.status == "not_interested":
            db.execute(
                "INSERT OR IGNORE INTO campaign_exclusions VALUES (?,?,?)",
                (item["email"], "not_interested", utcnow()),
            )
    return {"status": label.status}


def ensure_scan_table(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS campaign_scan_uid(
          id TEXT PRIMARY KEY, processed_at TEXT NOT NULL
        )
    """)


def scan_replies() -> int:
    """Correlate replies by Message-ID, without LLM access to mailbox text."""
    username = os.getenv("SERVGO_MAIL_IMAP_USER", "")
    password = os.getenv("SERVGO_MAIL_IMAP_PASSWORD", "")
    if not username or not password:
        return 0
    matched = 0
    with imaplib.IMAP4_SSL("mail.servgo.ru", 993, timeout=15) as inbox:
        inbox.login(username, password)
        result, _ = inbox.select("INBOX", readonly=True)
        if result != "OK":
            return 0
        result, validity = inbox.response("UIDVALIDITY")
        mailbox_version = str(validity[0] if validity else b"none")
        result, data = inbox.uid("search", None, "ALL")
        if result != "OK" or not data:
            return 0
        for uid in data[0].split()[-300:]:
            marker = mailbox_version + ":" + uid.decode("ascii", errors="ignore")
            with db_open() as db:
                initialize(db)
                ensure_scan_table(db)
                if db.execute(
                    "SELECT 1 FROM campaign_scan_uid WHERE id=?", (marker,)
                ).fetchone():
                    continue
            result, data = inbox.uid(
                "fetch", uid,
                "(BODY.PEEK[HEADER.FIELDS (FROM IN-REPLY-TO REFERENCES)])",
            )
            if result != "OK":
                continue
            raw = next((part[1] for part in data if isinstance(part, tuple)), None)
            if not raw:
                continue
            headers = email.message_from_bytes(raw, policy=email.policy.default)
            sender = parseaddr(str(headers.get("From", "")))[1].lower()
            references = (
                str(headers.get("In-Reply-To", "")) + " " +
                str(headers.get("References", ""))
            )
            referenced_ids = re.findall(
                r"<sgc-([a-f0-9]{32})@servgo\.ru>", references, flags=re.I,
            )
            with db_open() as db:
                initialize(db)
                ensure_scan_table(db)
                for rid in set(referenced_ids):
                    row = db.execute(
                        "SELECT * FROM campaign_recipient WHERE id=? AND email=?",
                        (rid, sender),
                    ).fetchone()
                    if not row or row["reply_at"]:
                        continue
                    db.execute(
                        "UPDATE campaign_recipient SET reply_at=? WHERE id=?",
                        (utcnow(), rid),
                    )
                    audit(db, row["campaign_id"], "reply", rid)
                    matched += 1
                db.execute(
                    "INSERT OR IGNORE INTO campaign_scan_uid VALUES (?,?)",
                    (marker, utcnow()),
                )
    return matched
