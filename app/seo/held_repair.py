"""Bounded repair of unpublished, held SEO articles using existing author/critic services."""
import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models.domain import AgentRun, Approval, Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus
from app.seo.activation import SeoFactoryActivation, is_enabled
from app.seo.publishing import (
    CLEAN_CRITIC_STATUSES, critic_verdict, duplicate_page_problems, reader_hash,
    servgo_buyer_intent_problems, static_problems, unsupported_servgo_pricing,
)
from app.workflows.article_contracts import ArticleArtifact, HumanEditedArticle

ATTEMPT = "seo_held_repair_attempt"
HELD = (ArtifactStatus.AWAITING_APPROVAL, ArtifactStatus.APPROVED)
logger = logging.getLogger(__name__)


class RepairBlocked(ValueError):
    """Internal, allowlisted reason for a rejected repair."""


def checked_candidate(original: ArticleArtifact, reader: HumanEditedArticle) -> ArticleArtifact:
    """Only reader prose may change; evidence, plan, conversion and CTA stay intact."""
    if original.reader_article is None or original.seo_brief is None:
        raise ValueError("Missing original SEO reader")
    if (reader.language != original.reader_article.language
            or reader.fact_renderings != original.reader_article.fact_renderings
            or reader.editorial_notes != original.reader_article.editorial_notes):
        raise ValueError("Immutable article evidence changed")
    if not reader.direct_answer or not reader.meta_description:
        raise ValueError("Incomplete reader copy")
    parts = ["# " + reader.title, reader.lead, "**Коротко:** " + reader.direct_answer]
    parts.extend("## " + section.heading + "\n\n" + section.body for section in reader.sections)
    parts.append(reader.closing)
    if reader.faq:
        parts.append("## Частые вопросы\n\n" + "\n\n".join(
            "### " + f.question + "\n\n" + f.answer for f in reader.faq))
    result = original.model_copy(deep=True)
    result.reader_article = reader
    result.seo.title = reader.title
    result.full_article = "\n\n".join(parts)
    return ArticleArtifact.model_validate(result.model_dump(mode="json"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _eligible(db: Session, row: Artifact) -> bool:
    task = db.get(Task, row.task_id) if row.task_id else None
    return bool(row.kind == "article" and row.status in HELD and task
        and task.product_id == row.product_id and task.assigned_agent == "content_factory"
        and task.title.startswith("SEO-ARTICLE:") and task.status == TaskStatus.COMPLETED
        and is_enabled(db, row.product_id)
        and not db.scalar(select(exists().where(
            Publication.artifact_id == row.id)))
        and not db.scalar(select(exists().where(
            Approval.artifact_id == row.id, Approval.status == "rejected"))))


def _problems(db, row, article):
    return static_problems(article) + unsupported_servgo_pricing(db, row, article)


def _unrepairable(db, row, article):
    return (not article.reader_article or not article.seo_brief
            or bool(servgo_buyer_intent_problems(db, row, article))
            or bool(duplicate_page_problems(db, row, article)))


async def run_held_repair_once(session_factory, settings, *, provider=None) -> int:
    """At most one bounded attempt per artifact, no restart of article generation.

    Claim before network IO, compare original revision and owner state on commit.
    A failed or interrupted attempt stays visible and is never repeated endlessly.
    """
    if not settings.seo_held_repair_enabled:
        return 0
    from app.core.composition import build_llm_provider, SAFE_LLM_METADATA_FIELDS
    from app.llm.audit import persist_critic_audit, persist_observed_llm_call
    from app.llm.schemas import LLMMessage, LLMRequest
    from app.llm.service import LLMService
    from app.privacy import PrivacyGateway
    from app.workflows.context import load_context

    privacy = PrivacyGateway(allowed_context_fields=SAFE_LLM_METADATA_FIELDS)
    with session_factory() as db:
        rows = list(db.scalars(select(Artifact).join(Task, Task.id == Artifact.task_id)
            .join(SeoFactoryActivation, SeoFactoryActivation.product_id == Artifact.product_id)
            .where(Artifact.kind == "article", Artifact.status.in_(HELD),
                   Task.product_id == Artifact.product_id, Task.status == TaskStatus.COMPLETED,
                   Task.assigned_agent == "content_factory", Task.title.like("SEO-ARTICLE:%"),
                   SeoFactoryActivation.enabled.is_(True))
            .order_by(Artifact.created_at, Artifact.id)))
        for row in rows:
            if not _eligible(db, row):
                continue
            product_id, article_id = row.product_id, row.id
            # Same ordering as the publisher and opt-out.
            product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
            if product is None:
                db.rollback()
                continue
            db.refresh(row, with_for_update=True)
            if not _eligible(db, row):
                db.rollback()
                continue
            if db.scalar(select(exists().where(
                    Artifact.product_id == product_id, Artifact.task_id == row.task_id,
                    Artifact.kind == ATTEMPT, Artifact.title == str(article_id)))):
                db.rollback()
                continue
            try:
                original = ArticleArtifact.model_validate_json(row.content)
                if _unrepairable(db, row, original):
                    db.rollback()
                    continue
                problems = _problems(db, row, original)
                verdict = critic_verdict(db, row.task_id, original.reader_article)
                if not problems and verdict in CLEAN_CRITIC_STATUSES:
                    db.rollback()
                    continue
                context = privacy.sanitize_text(load_context(
                    db, product, settings.products_root)["stable_context"])
                if not context.strip():
                    db.rollback()
                    continue
            except (ValueError, TypeError):
                db.rollback()
                continue
            digest, version, status = _digest(row.content), row.version, row.status
            task_id = row.task_id
            final_run = db.scalar(select(AgentRun).where(
                AgentRun.task_id == task_id, AgentRun.agent_name == 'marketing_editor'
            ).order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).limit(1))
            previous_findings = []
            if final_run is not None:
                recorded = db.scalar(select(Artifact).where(
                    Artifact.product_id == product_id, Artifact.task_id == task_id,
                    Artifact.agent_run_id == final_run.id, Artifact.kind == 'seo_final_review'
                ).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(1))
                if recorded is not None:
                    try:
                        data = json.loads(recorded.content)
                        if (isinstance(data, dict) and data.get('reader_hash') == reader_hash(original.reader_article)
                                and isinstance(data.get('findings'), list)):
                            previous_findings = data['findings'][:20]
                    except (ValueError, TypeError):
                        pass
            request = LLMRequest(
                system_prompt=(
                    "Доработай существующую SEO-статью для покупателя компании. "
                    "Устрани служебные формулировки и указанные дефекты, сохрани полезные "
                    "части текста, поисковый запрос, реальные ограничения, проверенные факты "
                    "и естественную маркетинговую подачу. Не выдумывай цены, функции, "
                    "результаты или источники. Не меняй fact_renderings и editorial_notes. "
                    "Не добавляй новых неподтверждённых утверждений. Ответ — полный JSON редактора."
                ),
                messages=[LLMMessage(role="user", content=json.dumps({
                    "seo_brief": original.seo_brief.model_dump(mode="json"),
                    "primary_query": original.seo.primary_query,
                    "reader": original.reader_article.model_dump(mode="json"),
                    "cta": original.conversion.cta,
                    "company_context": context,
                    "static_problems": problems,
                    "previous_critic_status": verdict,
                    "previous_critic_findings": previous_findings,
                }, ensure_ascii=False))],
                model=settings.llm_model, max_tokens=12000,
                response_schema=HumanEditedArticle.model_json_schema(),
                metadata={"team_role": "marketing_editor", "task_type": "article_production",
                          "seo_article": True, "company_slug": product.slug,
                          "reasoning_effort": "low"},
            )
            run = AgentRun(task_id=task_id, agent_name="seo_repair_editor",
                           provider=settings.llm_provider, model=settings.llm_model,
                           status="running", started_at=datetime.now(timezone.utc))
            db.add(run)
            db.flush()
            run_id = run.id
            marker = Artifact(product_id=product_id, task_id=task_id, agent_run_id=run_id,
                              kind=ATTEMPT, title=str(article_id),
                              content=json.dumps({"state": "claimed", "sha256": digest, "version": version}))
            db.add(marker)
            db.flush()
            marker_id = marker.id
            db.commit()
            break
        else:
            return 0

    reply = None
    try:
        service = LLMService(privacy_gateway=privacy, provider=provider or build_llm_provider(settings),
            critic_enabled=True, critic_limits={
                "max_context_chars": max(150000, settings.agent_critic_max_context_chars),
                "max_original_chars": 70000,
                "review_timeout": settings.agent_critic_review_timeout_seconds,
                "revision_timeout": settings.agent_critic_revision_timeout_seconds,
            })

        def observed(stage, response):
            with session_factory() as audit_db:
                persist_observed_llm_call(audit_db, product_id=product_id, task_id=task_id,
                    run_id=run_id, response=response, stage=stage,
                    task_type="article_production", provider=settings.llm_provider)

        # One author correction followed by independent critic. No further author
        # revision: another material issue is a terminal held result for this attempt.
        reply = await asyncio.wait_for(service.generate(request, on_observed=observed),
            timeout=300 + service.extra_timeout_seconds)
        review = reply.quality_review or {}
        if review.get("status") != "no_material_issues":
            raise RepairBlocked("repair_critic_not_clean")
        reader = HumanEditedArticle.model_validate_json(reply.content)
        candidate = checked_candidate(original, reader)
        with session_factory() as db:
            product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
            row = db.scalar(select(Artifact).where(Artifact.id == article_id,
                Artifact.product_id == product_id).with_for_update())
            if (product is None or row is None or not _eligible(db, row)
                    or row.version != version or row.status != status or _digest(row.content) != digest):
                raise RepairBlocked("repair_stale_article")
            current_context = privacy.sanitize_text(load_context(
                db, product, settings.products_root)["stable_context"])
            if current_context != context or _unrepairable(db, row, candidate) or _problems(db, row, candidate):
                raise RepairBlocked("repair_static_block")
            run = db.get(AgentRun, run_id)
            persist_critic_audit(db, product_id=product_id, task_id=task_id,
                                 run_id=run_id, response=reply)
            db.add(Artifact(product_id=product_id, task_id=task_id,
                kind="seo_article_revision_backup", title=str(article_id), content=row.content))
            db.add(Artifact(product_id=product_id, task_id=task_id, agent_run_id=run_id,
                kind="seo_final_review", title="Independent review of SEO repair",
                content=json.dumps({**review, "reader_hash": reader_hash(candidate.reader_article)},
                                   ensure_ascii=False)))
            # Only a successful atomic revision becomes the authoritative final review.
            run.agent_name = "marketing_editor"
            run.status, run.finished_at = "completed", datetime.now(timezone.utc)
            row.content, row.title = candidate.model_dump_json(), reader.title
            row.version = (row.version or 1) + 1
            row.status = ArtifactStatus.DRAFT  # existing publisher enforces date, quotas, veto and critic
            db.get(Artifact, marker_id).content = json.dumps({"state": "repaired", "sha256": digest,
                                                            "version": version})
            db.commit()
    except (Exception, asyncio.CancelledError) as exc:
        with session_factory() as db:
            run = db.get(AgentRun, run_id)
            reason = str(exc) if isinstance(exc, RepairBlocked) else type(exc).__name__
            if reply is not None:
                persist_critic_audit(db, product_id=product_id, task_id=task_id,
                                     run_id=run_id, response=reply)
            run.status, run.error = "failed", "seo_held_repair_failed:" + reason
            run.finished_at = datetime.now(timezone.utc)
            db.get(Artifact, marker_id).content = json.dumps({"state": "failed", "sha256": digest,
                                                            "version": version,
                                                            "error_type": reason})
            db.commit()
        logger.warning("SEO held repair failed article=%s type=%s", article_id, type(exc).__name__)
        if isinstance(exc, asyncio.CancelledError):
            raise
    return 1
