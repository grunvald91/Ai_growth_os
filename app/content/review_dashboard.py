"""Company-scoped, read-only review history and SEO progress for Growth Studio.

Only persisted approvals and confirmed task/artifact/publication rows are used.
The presentation layer never mutates or guesses the outcome of an AI review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.domain import (
    Approval, Artifact, ArtifactStatus, Publication, Task, TaskStatus,
)
from app.seo.diagnostics import audit_seo
from app.content.review import REVIEW_KINDS


PAGE_SIZE = 30
REVIEW_ACTIONS = ("studio:approve_material", "dispatcher:approve_material")
SEO_PUBLICATION_ACTION = "publish"


@dataclass
class Page:
    records: list
    total: int
    page: int
    page_count: int
    issue_total: int = 0
    issue_counts: dict[str, int] = field(default_factory=dict)


def _page(total: int, page: int) -> tuple[int, int]:
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return min(max(1, page), pages), pages


def history(db: Session, product_id: UUID, *, page: int = 1) -> Page:
    """Only actual *decided* events, not inferred from material.status.

    The second product_id predicate prevents a forged cross-company artifact
    binding from leaking its title or a link into the reviewing company's log.
    """
    where = (
        Approval.product_id == product_id,
        Artifact.product_id == product_id,
        Artifact.kind.in_(REVIEW_KINDS),
        Approval.decided_at.is_not(None),
        Approval.status.in_(("approved", "rejected")),
        (
            Approval.action_type.in_(REVIEW_ACTIONS)
            | ((Approval.action_type == SEO_PUBLICATION_ACTION)
               & (Approval.requested_by == "seo_factory"))
        ),
    )
    source = select(Approval, Artifact).join(Artifact, Artifact.id == Approval.artifact_id).where(*where)
    total = db.scalar(
        select(func.count(Approval.id)).join(Artifact, Artifact.id == Approval.artifact_id).where(*where)
    ) or 0
    current, pages = _page(total, page)
    results = list(db.execute(source.order_by(
        Approval.decided_at.desc(), Approval.created_at.desc(), Approval.id.desc()
    ).offset((current - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all())
    return Page(results, total, current, pages)


def seo_progress(db: Session, product_id: UUID, *, page: int = 1,
                 issues_only: bool = False) -> Page:
    """Paginated status view backed by the same evidence as the owner briefing.

    Full task set is read before filtering so the "needs attention" count is
    correct, not limited to the newest page. No changes to publications.
    """
    audit = audit_seo(db, product_id)
    visible = [item for item in audit.rows if item["issue_code"]] if issues_only else audit.rows
    total = len(visible)
    current, pages = _page(total, page)
    return Page(
        visible[(current - 1) * PAGE_SIZE:current * PAGE_SIZE],
        total, current, pages, len(audit.issues), audit.counts,
    )
