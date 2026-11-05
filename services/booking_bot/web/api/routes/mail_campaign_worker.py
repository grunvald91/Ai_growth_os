"""Single-instance ServGo Mail worker, safe across interruptions."""
from __future__ import annotations

import asyncio
import fcntl
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .mail_campaign_analytics import scan_replies
from .mail_campaign_dispatch import dispatch_one
from .mail_campaign_research import prepare_one
from .mail_campaign_store import (
    HEARTBEAT_KEY, audit, automation_enabled, initialize, put_setting,
)
from .owner_mail import db_open


def recover_stalled() -> int:
    """A previously reserved SMTP message is never retried automatically."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with db_open() as db:
        initialize(db)
        stuck = db.execute("""
            SELECT id,campaign_id,status FROM campaign_recipient
            WHERE status IN ('sending','researching') AND reserved_at<?
        """, (cutoff,)).fetchall()
        for row in stuck:
            next_status = "unknown" if row["status"] == "sending" else "draft_error"
            db.execute("UPDATE campaign_recipient SET status=? WHERE id=?",
                       (next_status, row["id"]))
            audit(db, row["campaign_id"], "recovered_" + next_status, row["id"])
        return len(stuck)


def heartbeat() -> bool:
    """Record that the worker is alive; return the owner's sending switch."""
    with db_open() as db:
        initialize(db)
        put_setting(db, HEARTBEAT_KEY, "alive")
        return automation_enabled(db)


async def tick() -> bool:
    """One worker pass: prepare a draft, otherwise send one message if switched on."""
    sending_on = heartbeat()
    if await prepare_one():
        return True
    # Drafts are still prepared for review; nothing is sent while switched off.
    return await dispatch_one() if sending_on else False


async def loop() -> None:
    root = Path(os.getenv("SERVGO_MAIL_DATA_DIR", "/var/lib/servgo-owner-mail"))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = root / "campaign-worker.lock"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("ServGo Mail worker already running")
        recover_stalled()
        last_check = 0.0
        while True:
            did_work = False
            try:
                did_work = await tick()
            except Exception:
                # Keep the worker alive; never dump mailbox data or credentials.
                await asyncio.sleep(15)

            instant = asyncio.get_running_loop().time()
            if instant - last_check >= 600:
                last_check = instant
                try:
                    await asyncio.to_thread(scan_replies)
                except Exception:
                    pass
            if not did_work:
                await asyncio.sleep(8)


if __name__ == "__main__":
    asyncio.run(loop())
