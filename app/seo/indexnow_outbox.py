"""IndexNow pilot: durable, opt-in notifications for published ServGo blog pages.

A successful HTTP submission is NOT an indexing or AI citation confirmation.
No API calls happen unless owner configures INDEXNOW_KEY and publishes its
verification file at https://servgo.ru/<key>.txt. The public key is never logged.
Company blogs are deliberately not submitted until verified public routes and
company mapping exist; never leak draft/tenant pages into ServGo's index.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Artifact, ArtifactStatus, Product, Publication

_LOG = logging.getLogger(__name__)
_KEY = re.compile(r"^[A-Za-z0-9-]{8,128}$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ORIGIN = "https://servgo.ru"
_ENDPOINT = "https://api.indexnow.org/indexnow"
_KIND = "geo_indexnow_outbox"


def enqueue_published_article(db: Session, article: Artifact, slug: str) -> bool:
    """Enqueue in the same transaction as a genuine published page.

    Publication sites have independent authorization. The pilot is *ServGo*
    only, and ignores every other tenant, even if its own_site publication has
    a misleading /blog/ reference. Called under the existing company row lock.
    """
    if not _SLUG.fullmatch(slug):
        raise ValueError("Invalid public slug")
    product = db.get(Product, article.product_id)
    if product is None or product.slug != "servgo":
        return False
    version = article.version or 1
    title = f"indexnow:{article.id}:v{version}"
    already = db.scalar(select(Artifact.id).where(
        Artifact.product_id == article.product_id,
        Artifact.kind == _KIND,
        Artifact.title == title,
    ).limit(1))
    if already:
        return False
    db.add(Artifact(
        product_id=article.product_id,
        task_id=article.task_id,
        kind=_KIND,
        title=title,
        content=json.dumps({
            "url": f"{_ORIGIN}/blog/{slug}",
            "article_id": str(article.id),
            "version": version,
            "state": "pending",
            "attempts": 0,
            "next_attempt_at": None,
            "last_http_status": None,
        }),
    ))
    return True


def _ready(state: dict, now: datetime) -> bool:
    if state.get("state") != "pending":
        return False
    after = state.get("next_attempt_at")
    if not after:
        return True
    try:
        due = datetime.fromisoformat(after)
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        return due <= now
    except (ValueError, TypeError):
        return False


def deliver_one(db: Session, *, key: str | None = None,
                client=None, now: datetime | None = None) -> int:
    """Send at most one due notification; no billing/models/extra publications.

    Return 1 for an attempted notification, 0 for disabled/no due record.
    Always verify public key file before posting. An unsuccessful verification
    does not consume attempts; owner may complete DNS/nginx configuration later.
    """
    if not key:
        return 0
    if not _KEY.fullmatch(key):
        raise ValueError("IndexNow key has invalid format")
    now = now or datetime.now(timezone.utc)
    rows = list(db.scalars(select(Artifact).where(
        Artifact.kind == _KIND,
    ).order_by(Artifact.created_at, Artifact.id).limit(200)))
    row = None
    state = None
    for candidate in rows:
        try:
            payload = json.loads(candidate.content)
        except (ValueError, TypeError):
            continue
        if _ready(payload, now):
            row, state = candidate, payload
            break
    if row is None:
        return 0
    # An additional worker cannot claim the same event concurrently.
    row = db.scalar(select(Artifact).where(Artifact.id == row.id)
                    .with_for_update(skip_locked=True))
    if row is None:
        return 0
    state = json.loads(row.content)
    if not _ready(state, now):
        return 0
    if (not isinstance(state.get("url"), str) or
            not re.fullmatch(r"https://servgo\.ru/blog/[a-z0-9]+(?:-[a-z0-9]+)*", state["url"])):
        state["state"] = "rejected"
        row.content = json.dumps(state)
        db.commit()
        return 1
    # Do not submit a page later hidden/unpublished by the owner.
    try:
        source_id = UUID(state["article_id"])
    except (ValueError, TypeError, KeyError):
        state["state"] = "rejected"
        row.content = json.dumps(state)
        db.commit()
        return 1
    published = db.scalar(select(Publication.id).join(
        Artifact, Publication.artifact_id == Artifact.id,
    ).where(
        Publication.product_id == row.product_id,
        Publication.artifact_id == source_id,
        Artifact.product_id == row.product_id,
        Artifact.kind == "article",
        Artifact.status == ArtifactStatus.PUBLISHED,
        Publication.channel == "own_site",
        Publication.status == "published",
        Publication.external_reference == state["url"].removeprefix(_ORIGIN),
    ).limit(1))
    if not published:
        state["state"] = "skipped_unpublished"
        row.content = json.dumps(state)
        db.commit()
        return 1

    def try_send(c):
        verification = c.get(f"{_ORIGIN}/{key}.txt")
        if verification.status_code != 200 or verification.text.strip() != key:
            return None
        return c.post(_ENDPOINT, json={
            "host": "servgo.ru", "key": key,
            "urlList": [state["url"]],
        }).status_code

    try:
        if client is None:
            with httpx.Client(timeout=7, follow_redirects=False, trust_env=False) as c:
                code = try_send(c)
        else:
            code = try_send(client)
    except httpx.RequestError:
        code = 503

    if code is None:
        # Key not yet served publicly. No attempt was made.
        return 0
    count = int(state.get("attempts") or 0) + 1
    state["attempts"] = count
    state["last_http_status"] = code
    state["last_attempt_at"] = now.isoformat()
    if code in (200, 202):
        state["state"] = "accepted_by_endpoint"
    elif code in (400, 403, 422):
        state["state"] = "rejected"
    elif count >= 5:
        state["state"] = "failed_after_retries"
    else:
        state["next_attempt_at"] = (
            now + timedelta(minutes=min(240, 5 * (2 ** (count - 1))))
        ).isoformat()
    row.content = json.dumps(state, ensure_ascii=False)
    db.commit()
    _LOG.info("IndexNow notification result: http=%s state=%s", code, state["state"])
    return 1
