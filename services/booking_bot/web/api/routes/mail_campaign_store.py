"""Storage schema and summaries for ServGo Mail campaigns."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS campaign (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            objective TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            not_before TEXT NOT NULL DEFAULT '',
            min_interval_seconds INTEGER NOT NULL DEFAULT 90
        );
        CREATE TABLE IF NOT EXISTS campaign_recipient (
            id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            email TEXT NOT NULL,
            company TEXT NOT NULL DEFAULT '',
            website TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new',
            review_note TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL DEFAULT '',
            reserved_at TEXT NOT NULL DEFAULT '',
            accepted_at TEXT NOT NULL DEFAULT '',
            reply_at TEXT NOT NULL DEFAULT '',
            reply_status TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(campaign_id,email),
            FOREIGN KEY(campaign_id) REFERENCES campaign(id)
        );
        CREATE TABLE IF NOT EXISTS campaign_event (
            id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            recipient_id TEXT,
            event TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            info TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS mail_setting (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS campaign_exclusions (
            email TEXT PRIMARY KEY,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS recipient_status_idx
            ON campaign_recipient(campaign_id,status);
        CREATE INDEX IF NOT EXISTS campaign_event_idx
            ON campaign_event(campaign_id,occurred_at);
    """)
    # The company a campaign works for (a studio company); empty means ServGo's own offer.
    columns = {row[1] for row in db.execute("PRAGMA table_info(campaign)")}
    if "company_id" not in columns:
        db.execute("ALTER TABLE campaign ADD COLUMN company_id TEXT NOT NULL DEFAULT ''")
    if "company_brief" not in columns:
        db.execute("ALTER TABLE campaign ADD COLUMN company_brief TEXT NOT NULL DEFAULT ''")


AUTOMATION_KEY = "automation_enabled"
HEARTBEAT_KEY = "worker_heartbeat"
WORKER_STALE_SECONDS = 120


def setting(db, key: str) -> tuple[str, str] | None:
    row = db.execute("SELECT value,updated_at FROM mail_setting WHERE key=?",
                     (key,)).fetchone()
    return (row["value"], row["updated_at"]) if row else None


def put_setting(db, key: str, value: str) -> None:
    db.execute(
        "INSERT INTO mail_setting VALUES (?,?,?) ON CONFLICT(key) DO UPDATE "
        "SET value=excluded.value,updated_at=excluded.updated_at",
        (key, value, utcnow()),
    )


def automation_enabled(db) -> bool:
    """Automatic sending is off until the owner switches it on."""
    found = setting(db, AUTOMATION_KEY)
    return bool(found and found[0] == "on")


def automation_status(db) -> dict:
    found = setting(db, AUTOMATION_KEY)
    beat = setting(db, HEARTBEAT_KEY)
    alive = False
    if beat:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(beat[1])).total_seconds()
            alive = 0 <= age < WORKER_STALE_SECONDS
        except ValueError:
            alive = False
    return {
        "enabled": bool(found and found[0] == "on"),
        "changed_at": found[1] if found else "",
        "worker_alive": alive,
        "worker_seen_at": beat[1] if beat else "",
    }


def audit(db, campaign_id: str, event: str, recipient_id: str | None = None,
          info: str = "") -> None:
    db.execute(
        "INSERT INTO campaign_event VALUES (?,?,?,?,?,?)",
        (uuid4().hex, campaign_id, recipient_id, event, utcnow(), info[:300]),
    )


def summary(db, campaign_id: str) -> dict:
    rows = db.execute("""
        SELECT status, COUNT(*) AS total
        FROM campaign_recipient WHERE campaign_id=?
        GROUP BY status
    """, (campaign_id,)).fetchall()
    statuses = {row["status"]: row["total"] for row in rows}
    totals = db.execute("""
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN accepted_at != '' THEN 1 ELSE 0 END) AS accepted,
          SUM(CASE WHEN reply_at != '' THEN 1 ELSE 0 END) AS replies,
          SUM(CASE WHEN reply_status = 'interested' THEN 1 ELSE 0 END)
            AS interested
        FROM campaign_recipient WHERE campaign_id=?
    """, (campaign_id,)).fetchone()
    return {
        "total": totals["total"],
        "status_counts": statuses,
        "smtp_accepted": totals["accepted"] or 0,
        "replies": totals["replies"] or 0,
        "interested": totals["interested"] or 0,
        "delivery_unknown": statuses.get("unknown", 0),
    }
