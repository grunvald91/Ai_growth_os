"""SEO publication requires a clean review AND explicit human approval for each article.

A good model-critic verdict makes an article ready for owner review, not automatically public.
Only accepted, reviewable pages become visible in the company blog and sitemap.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import AgentRun, Approval, Artifact, ArtifactStatus, Publication
from app.policies import PolicyEvaluator
from app.seo.clustering import normalize
from app.services.approvals import ApprovalService
from app.tools import ToolOperation
from app.workflows.article_contracts import ArticleArtifact
from app.seo.activation import is_enabled

CHANNEL = "own_site"
CLEAN_CRITIC_STATUSES = frozenset({"no_material_issues", "revised"})
FINAL_EDITOR = "marketing_editor"
DECIDER = "policy:critic_reviewed"  # Legacy autonomous approvals; never used for new SEO pages.

_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                      "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
_LEAKS = re.compile(r"\[REDACTED|\{[a-z_]+\}|TODO|lorem ipsum|proof_needed|schema:", re.IGNORECASE)


@dataclass(frozen=True)
class Outcome:
    published: bool
    reason: str
    slug: str | None = None


def slugify(text: str) -> str:
    out = "".join(_TRANSLIT.get(ch, ch) for ch in normalize(text))
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return out[:80].strip("-") or "article"


def unique_slug(db: Session, product_id, text: str) -> str:
    taken = {p.external_reference for p in db.scalars(
        select(Publication).where(Publication.product_id == product_id, Publication.channel == CHANNEL))}
    base = slugify(text)
    slug, n = base, 2
    while f"/blog/{slug}" in taken:
        slug, n = f"{base[:74]}-{n}", n + 1
    return slug


def reader_hash(reader) -> str:
    return hashlib.sha256(json.dumps(reader.model_dump(mode="json"), sort_keys=True,
                                    ensure_ascii=False).encode()).hexdigest()


def critic_verdict(db: Session, task_id, reader=None) -> str:
    """Status of the critic review of the final reader-facing editor, or 'missing' when no review happened."""
    run = db.scalar(select(AgentRun).where(AgentRun.task_id == task_id, AgentRun.agent_name == FINAL_EDITOR)
                    .order_by(AgentRun.created_at.desc()).limit(1))
    if run is None:
        return "missing"
    reviews = db.scalars(select(Artifact).where(Artifact.agent_run_id == run.id, Artifact.kind == "seo_final_review")
                         .order_by(Artifact.created_at.desc(), Artifact.id.desc()))
    for review in reviews:
        try:
            data = json.loads(review.content)
            if reader is None or data.get("reader_hash") != reader_hash(reader):
                return "content_mismatch"
            return str(data.get("status", "missing"))
        except (TypeError, ValueError):
            continue
    return "missing"


def static_problems(article: ArticleArtifact) -> list[str]:
    problems = []
    reader = article.reader_article
    if article.seo_brief is None:
        problems.append("article does not belong to the search plan")
    if reader is None:
        return problems + ["no reader-facing article"]
    if not reader.direct_answer:
        problems.append("no direct answer")
    # FAQ blocks are optional. Fabricating three questions for every article
    # makes the blog repetitive and does not improve its factual reliability.
    if not reader.meta_description:
        problems.append("no meta description")
    if _LEAKS.search(article.full_article):
        problems.append("service text leaked into the article")
    return problems


def publish_if_clear(db: Session, article_id) -> Outcome:
    """Prepare a reviewable draft. A clean AI critic is not a human publication decision."""
    row = db.get(Artifact, article_id)
    if row is None or row.kind != "article":
        raise LookupError("Article not found")
    if row.status == ArtifactStatus.PUBLISHED:
        return Outcome(True, "already_published")
    if not is_enabled(db, row.product_id):
        return Outcome(False, "seo_disabled")
    article = ArticleArtifact.model_validate_json(row.content)
    problems = static_problems(article)
    verdict = critic_verdict(db, row.task_id, article.reader_article)
    if verdict not in CLEAN_CRITIC_STATUSES:
        problems.append(f"critic review: {verdict}")
    policy = PolicyEvaluator().evaluate(ToolOperation.PUBLISH)

    # Never mark a clean article public merely because a critic has no findings.
    # The owner must review the actual version and decide in Studio.
    row.status = ArtifactStatus.AWAITING_APPROVAL
    reason = ("Требуется ручная проверка пользы, оригинальности и фактов перед публикацией."
              if not problems else "Требуется исправление до публикации: " + "; ".join(problems))
    approval = db.scalar(select(Approval).where(
        Approval.artifact_id == row.id,
        Approval.requested_by == "seo_factory",
        Approval.status.in_(["pending", "approved"]),
    ).order_by(Approval.created_at.desc()).limit(1))
    if approval is None:
        ApprovalService(db).create(
            product_id=row.product_id, artifact_id=row.id,
            action_type=ToolOperation.PUBLISH.value, risk_level=policy.risk_level.value,
            status="pending", requested_by="seo_factory", reason=reason)
    elif approval.status == "pending":
        approval.reason = reason
    db.commit()
    return Outcome(False, "; ".join(problems) if problems else "human_review_required")


def _go_live(db: Session, row: Artifact, article: ArticleArtifact, approval: Approval, now: datetime) -> str:
    slug = unique_slug(db, row.product_id, article.seo.primary_query)
    db.add(Publication(product_id=row.product_id, artifact_id=row.id, approval_id=approval.id, channel=CHANNEL,
                       status="published", external_reference=f"/blog/{slug}", published_at=now))
    row.status = ArtifactStatus.PUBLISHED
    return slug


def publish_owner_approved(db: Session) -> int:
    """Publish held plan articles once the owner approved them, in the Studio (artifact approved) or on the
    approval itself. A rejected approval keeps the article off the blog."""
    held = db.scalars(select(Approval).where(Approval.requested_by == "seo_factory",
                                             Approval.action_type == ToolOperation.PUBLISH.value,
                                             Approval.status.in_(["pending", "approved"]),
                                             Approval.decided_by.is_distinct_from(DECIDER)))
    published = 0
    for approval in list(held):
        row = db.get(Artifact, approval.artifact_id)
        if row is None or row.kind != "article" or row.status == ArtifactStatus.PUBLISHED:
            continue
        if not is_enabled(db, row.product_id):
            continue
        owner_said_yes = row.status == ArtifactStatus.APPROVED or (
            approval.status == "approved" and str(approval.decided_by or "").startswith("owner:"))
        if not owner_said_yes or db.scalar(select(Publication.id).where(Publication.artifact_id == row.id).limit(1)):
            continue
        # Re-check the exact version at the moment of publishing. Owner signoff
        # cannot bypass an invalid, changed, or unreviewed SEO article.
        try:
            article = ArticleArtifact.model_validate_json(row.content)
            problems = static_problems(article)
            verdict = critic_verdict(db, row.task_id, article.reader_article)
            if verdict not in CLEAN_CRITIC_STATUSES:
                problems.append(f"critic review: {verdict}")
        except (TypeError, ValueError) as exc:
            problems = [f"invalid article: {type(exc).__name__}"]
        if problems:
            approval.reason = "Публикация остановлена: " + "; ".join(problems)
            db.commit()
            continue
        now = datetime.now(timezone.utc)
        if approval.status == "pending":
            approval.status, approval.decided_by, approval.decided_at = "approved", "owner:studio", now
        _go_live(db, row, article, approval, now)
        db.commit()
        published += 1
    return published



def publish_pending(db: Session) -> int:
    from app.models.domain import Task, TaskStatus
    rows = list(db.scalars(select(Artifact).join(Task, Artifact.task_id == Task.id).where(
        Artifact.kind == "article", Artifact.status == ArtifactStatus.DRAFT,
        Task.status == TaskStatus.COMPLETED, Task.title.like("SEO-ARTICLE:%"))
        .order_by(Artifact.created_at, Artifact.id).limit(10)))
    for row in rows:
        if not is_enabled(db, row.product_id):
            continue
        try:
            publish_if_clear(db, row.id)
        except Exception:
            db.rollback()
    return len(rows)
