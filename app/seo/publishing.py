"""Automatically publish SEO articles after clean AI critic and static checks.

Articles that fail those checks remain queued for optional owner review.
"""
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import AgentRun, Approval, Artifact, ArtifactStatus, Product, Publication
from app.policies import PolicyEvaluator
from app.seo.clustering import normalize
from app.services.approvals import ApprovalService
from app.tools import ToolOperation
from app.workflows.article_contracts import ArticleArtifact
from app.seo.activation import SeoFactoryActivation, is_enabled, editorial_preferences
from app.seo.schedule import PUBLICATIONS_PER_DAY, db_time, published_today

CHANNEL = "own_site"
CLEAN_CRITIC_STATUSES = frozenset({"no_material_issues", "revised", "all_rejected"})
FINAL_EDITOR = "marketing_editor"
DECIDER = "policy:critic_reviewed"
_logger = logging.getLogger(__name__)

_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                      "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
_LEAKS = re.compile(r"\[REDACTED|\{[a-z_]+\}|TODO|lorem ipsum|proof_needed|schema:", re.IGNORECASE)
# The verified ServGo company profile explicitly has no approved subscription
# prices. Avoid fabricating a commercial offer even if a critic misses the claim.
_SERVGO_PRICE = re.compile(r"(?<!\w)\d[\d\s\u00a0]{0,12}\s*(?:₽|руб(?:\.|ля|лей)?|RUB)(?!\w)", re.IGNORECASE)


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
            # The critic reviewed the final reader-facing version. A later
            # content edit cannot inherit approval from an older version.
            # This is an automatic version guard, NOT a manual owner gate.
            if not isinstance(data, dict):
                continue
            if reader is None or data.get("reader_hash") != reader_hash(reader):
                return "content_mismatch"
            status = str(data.get("status", "missing"))
            if status == "all_rejected":
                # Author may disagree with the critic, but only if every
                # finding was individually assessed in the recorded audit.
                findings, decisions = data.get("findings"), data.get("decisions")
                if not isinstance(findings, list) or not findings or not isinstance(decisions, list):
                    return "unresolved_critic_findings"
                ids = [f.get("id") for f in findings if isinstance(f, dict)]
                assessments = {d.get("id"): d for d in decisions if isinstance(d, dict)}
                if (len(ids) != len(findings) or len(set(ids)) != len(ids)
                    or len(assessments) != len(findings) or set(ids) != set(assessments)
                    or any(d.get("decision") != "reject" or len(str(d.get("rationale") or "").strip()) < 5
                           for d in assessments.values())):
                    return "unresolved_critic_findings"
            if status in CLEAN_CRITIC_STATUSES:
                findings = data.get("findings") or []
                decisions = {decision.get("id"): decision
                             for decision in (data.get("decisions") or [])
                             if isinstance(decision, dict)}
                # The author may disagree with suggestions, but cannot
                # automatically release a material containing disputed
                # critical factual/reliability risks (false price, features,
                # fabricated proof). The owner can resolve the disagreement.
                if any(isinstance(f, dict) and f.get("severity") == "critical"
                       and decisions.get(f.get("id"), {}).get("decision") != "accept"
                       for f in findings):
                    return "critical_finding_unresolved"
            return status
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


def unsupported_servgo_pricing(db: Session, row: Artifact, article: ArticleArtifact) -> list[str]:
    """B2B ServGo currently has no approved public price (profile: pricing under review).

    Restrict this extra check to the ServGo marketing company, not tenant
    articles about their own genuine service prices. The approved offer can
    replace this rule once validated pricing is part of the company profile.
    """
    product = db.get(Product, row.product_id)
    if product is None or product.slug != "servgo" or article.reader_article is None:
        return []
    reader = article.reader_article
    visible = "\n".join([reader.title, reader.lead, reader.direct_answer or "",
                          *(section.body for section in reader.sections), reader.closing])
    return (["unverified ServGo price: no approved public tariff in company profile"]
            if _SERVGO_PRICE.search(visible) else [])


def duplicate_page_problems(db: Session, row: Artifact, article: ArticleArtifact) -> list[str]:
    """Fail on actual repeats; avoid fuzzy heuristics that punish legitimately similar guides."""
    def core_text(value: ArticleArtifact) -> str:
        reader = value.reader_article
        if reader is None:
            return ""
        # Compare the substance, not SEO headings or a boilerplate CTA.
        return " ".join(" ".join(section.body.casefold().split())
                        for section in reader.sections).strip()

    key = normalize(article.seo.primary_query)
    body = core_text(article)
    published = db.scalars(
        select(Artifact).join(Publication, Publication.artifact_id == Artifact.id).where(
            Publication.product_id == row.product_id,
            Publication.channel == CHANNEL,
            Publication.status == "published",
            Artifact.kind == "article",
            Artifact.id != row.id,
        )
    )
    for previous in published:
        try:
            earlier = ArticleArtifact.model_validate_json(previous.content)
        except ValueError:
            continue
        if normalize(earlier.seo.primary_query) == key:
            return ["search query already has a published page for this company"]
        if body and core_text(earlier) == body:
            return ["identical article body is already published for this company"]
    return []


def publish_if_clear(db: Session, article_id, *, now: datetime | None = None,
                     daily_limit: int = PUBLICATIONS_PER_DAY) -> Outcome:
    """Publish immediately when the AI critic and basic article checks pass."""
    row = db.get(Artifact, article_id)
    if row is None or row.kind != "article":
        raise LookupError("Article not found")
    # Serialize publication, duplicate checks, opt-out and owner visibility on
    # the SAME company row. Under PostgreSQL, another worker waits here before
    # it can choose a slug or insert an additional publication.
    if db.scalar(select(Product.id).where(
            Product.id == row.product_id).with_for_update()) is None:
        raise LookupError("Company not found")
    db.refresh(row)  # Do not trust a stale status cached before the row lock.
    now = now or datetime.now(timezone.utc)
    existing = db.scalar(select(Publication).where(
        Publication.artifact_id == row.id, Publication.product_id == row.product_id,
        Publication.channel == CHANNEL).limit(1))
    if existing is not None:
        if existing.status == "published":
            if row.status != ArtifactStatus.PUBLISHED:
                row.status = ArtifactStatus.PUBLISHED
                db.commit()
            return Outcome(True, "already_published",
                           (existing.external_reference or "").removeprefix("/blog/") or None)
        if existing.status == "unpublished":
            return Outcome(False, "manually_unpublished")
        return Outcome(False, "existing_publication")
    if row.status == ArtifactStatus.PUBLISHED:
        return Outcome(True, "already_published")
    # An explicit owner rejection is a veto, not another critic failure.
    # Automatic retries must not create a new policy approval over that vote.
    rejected = db.scalar(select(Approval.id).where(
        Approval.product_id == row.product_id,
        Approval.artifact_id == row.id,
        Approval.action_type == ToolOperation.PUBLISH.value,
        Approval.requested_by == "seo_factory",
        Approval.status == "rejected",
    ).limit(1))
    if row.status == ArtifactStatus.REJECTED or rejected is not None:
        return Outcome(False, "owner_rejected")
    if not is_enabled(db, row.product_id):
        return Outcome(False, "seo_disabled")
    from app.models.domain import Task
    task = db.get(Task, row.task_id) if row.task_id else None
    if task is not None and task.due_at is not None and (
            (task.due_at.replace(tzinfo=timezone.utc) if task.due_at.tzinfo is None else task.due_at)
            > now):
        return Outcome(False, "scheduled_for_later")
    prefs = editorial_preferences(db, row.product_id, default_publications_per_day=daily_limit)
    if published_today(db, row.product_id, now=now, tz=ZoneInfo(prefs.timezone_name)) >= prefs.publications_per_day:
        return Outcome(False, "daily_publication_limit")
    article = ArticleArtifact.model_validate_json(row.content)
    problems = (static_problems(article) + unsupported_servgo_pricing(db, row, article)
                + duplicate_page_problems(db, row, article))
    verdict = critic_verdict(db, row.task_id, article.reader_article)
    if verdict not in CLEAN_CRITIC_STATUSES:
        problems.append(f"critic review: {verdict}")
    policy = PolicyEvaluator().evaluate(ToolOperation.PUBLISH)
    if problems:
        row.status = ArtifactStatus.AWAITING_APPROVAL
        already = db.scalar(select(Approval).where(
            Approval.artifact_id == row.id,
            Approval.requested_by == "seo_factory",
            Approval.status == "pending",
        ))
        if already is None:
            ApprovalService(db).create(
                product_id=row.product_id, artifact_id=row.id,
                action_type=ToolOperation.PUBLISH.value,
                risk_level=policy.risk_level.value,
                status="pending", requested_by="seo_factory",
                reason="Held for the owner: " + "; ".join(problems))
        db.commit()
        return Outcome(False, "; ".join(problems))
    # A failed first critic can leave a pending request. Once the critic
    # passes, resolve that request automatically; never leave a ghost
    # "waiting for owner" notification beside an already published article.
    reason = f"Critic review {verdict}; static checks passed. Automatic SEO publication."
    approval = db.scalar(select(Approval).where(
        Approval.product_id == row.product_id,
        Approval.artifact_id == row.id,
        Approval.action_type == ToolOperation.PUBLISH.value,
        Approval.requested_by == "seo_factory",
        Approval.status == "pending",
    ).order_by(Approval.created_at.desc()).limit(1))
    if approval is None:
        approval = ApprovalService(db).create(
            product_id=row.product_id, artifact_id=row.id,
            action_type=ToolOperation.PUBLISH.value,
            risk_level=policy.risk_level.value,
            status="approved", requested_by="seo_factory",
            decided_by=DECIDER, decided_at=now,
            reason=reason)
    else:
        approval.status = "approved"
        approval.decided_by = DECIDER
        approval.decided_at = now
        approval.reason = reason
    slug = _go_live(db, row, article, approval, now)
    db.commit()
    return Outcome(True, f"critic {verdict}", slug)


def _go_live(db: Session, row: Artifact, article: ArticleArtifact, approval: Approval, now: datetime) -> str:
    slug = unique_slug(db, row.product_id, article.seo.primary_query)
    db.add(Publication(product_id=row.product_id, artifact_id=row.id, approval_id=approval.id, channel=CHANNEL,
                       status="published", external_reference=f"/blog/{slug}", published_at=now))
    row.status = ArtifactStatus.PUBLISHED
    return slug


def publish_owner_approved(db: Session, *, now: datetime | None = None,
                           daily_limit: int = PUBLICATIONS_PER_DAY) -> int:
    """Publish held plan articles once the owner approved them, in the Studio (artifact approved) or on the
    approval itself. A rejected approval keeps the article off the blog."""
    held = db.scalars(select(Approval).where(Approval.requested_by == "seo_factory",
                                             Approval.action_type == ToolOperation.PUBLISH.value,
                                             Approval.status.in_(["pending", "approved"]),
                                             Approval.decided_by.is_distinct_from(DECIDER)))
    published = 0
    for approval in list(held):
        row = db.get(Artifact, approval.artifact_id)
        if row is None or row.kind != "article":
            continue
        # The automatic and manual paths must use the same per-company lock:
        # an owner action must not race a clean-critic worker.
        if db.scalar(select(Product.id).where(
                Product.id == row.product_id).with_for_update()) is None:
            continue
        db.refresh(row)
        db.refresh(approval)
        if row.status == ArtifactStatus.PUBLISHED or approval.status not in ("pending", "approved"):
            continue
        if approval.decided_by == DECIDER:
            continue
        if not is_enabled(db, row.product_id):
            continue
        from app.models.domain import Task
        task = db.get(Task, row.task_id) if row.task_id else None
        release_time = now or datetime.now(timezone.utc)
        if task is not None and task.due_at is not None and (
                (task.due_at.replace(tzinfo=timezone.utc) if task.due_at.tzinfo is None else task.due_at)
                > release_time):
            continue
        prefs = editorial_preferences(db, row.product_id, default_publications_per_day=daily_limit)
        if published_today(db, row.product_id, now=release_time, tz=ZoneInfo(prefs.timezone_name)) >= prefs.publications_per_day:
            continue
        owner_said_yes = row.status == ArtifactStatus.APPROVED or (
            approval.status == "approved" and str(approval.decided_by or "").startswith("owner:"))
        if not owner_said_yes or db.scalar(select(Publication.id).where(Publication.artifact_id == row.id).limit(1)):
            continue
        # Manual override is only used for articles the critic held back.
        # Keep duplicate protection, but do not require a version-specific signoff.
        try:
            article = ArticleArtifact.model_validate_json(row.content)
        except ValueError:
            _logger.warning("SEO owner-approved article has invalid content: %s", row.id)
            continue
        # Owner confirmation can override the critic's editorial verdict, not
        # basic page completeness: public_blog dereferences reader_article.
        problems = (static_problems(article) + unsupported_servgo_pricing(db, row, article)
                + duplicate_page_problems(db, row, article))
        if problems:
            _logger.warning("SEO owner-approved article failed static checks %s: %s",
                            row.id, "; ".join(problems))
            continue
        release_time = now or datetime.now(timezone.utc)
        if approval.status == "pending":
            approval.status, approval.decided_by, approval.decided_at = "approved", "owner:studio", release_time
        _go_live(db, row, article, approval, release_time)
        db.commit()
        published += 1
    return published



def publish_pending(db: Session, *, now: datetime | None = None,
                    daily_limit: int = PUBLICATIONS_PER_DAY) -> int:
    from app.models.domain import Task, TaskStatus
    from sqlalchemy import or_
    moment = now or datetime.now(timezone.utc)
    rows = list(db.scalars(select(Artifact)
        .join(Task, Artifact.task_id == Task.id)
        .join(SeoFactoryActivation, SeoFactoryActivation.product_id == Artifact.product_id)
        .where(Artifact.kind == "article", Artifact.status == ArtifactStatus.DRAFT,
               Task.status == TaskStatus.COMPLETED, Task.title.like("SEO-ARTICLE:%"),
               SeoFactoryActivation.enabled.is_(True),
               or_(Task.due_at.is_(None), Task.due_at <= db_time(db, moment)))
        .order_by(Task.due_at, Artifact.created_at, Artifact.id).limit(100)))
    for row in rows:
        if not is_enabled(db, row.product_id):
            continue
        try:
            publish_if_clear(db, row.id, now=moment, daily_limit=daily_limit)
        except Exception:
            db.rollback()
            _logger.exception("SEO publication recovery failed for article %s", row.id)
    return len(rows)
