"""One-time independent critic recovery for a completed but unreviewed ServGo article.

Only pre-existing content_request primary query "ии для продвижения бизнеса".
No bypass of critic, source controls, company opt-in or publication checks.
No fresh article generated, no cross-company records touched, no fake leads.
"""
import asyncio
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.composition import build_llm_provider
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.llm.critic import second_opinion
from app.llm.schemas import LLMMessage, LLMRequest, LLMResponse
from app.models.domain import AgentRun, Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus
from app.seo.activation import require_enabled
from app.seo.publishing import (
    CLEAN_CRITIC_STATUSES, critic_verdict, reader_hash, static_problems,
    unsupported_servgo_pricing, servgo_buyer_intent_problems, duplicate_page_problems,
)
from app.seo.revise_servgo_owner_articles import (
    EditableOwnerArticle, apply_editorial_copy, editable_payload, approved_company_claims,
)
from app.seo.servgo_three_article_release import _publish_for_this_release, _check_production_preconditions
from app.workflows.article_contracts import ArticleArtifact

QUERY = "ии для продвижения бизнеса"


def find_article(db, product_id):
    matches = []
    for task in db.scalars(select(Task).where(
        Task.product_id == product_id, Task.assigned_agent == "content_factory",
        Task.title.like("SEO-ARTICLE:%")
    )):
        request = db.scalar(select(Artifact).where(
            Artifact.product_id == product_id, Artifact.task_id == task.id,
            Artifact.kind == "content_request").limit(1))
        if not request:
            continue
        try:
            content = json.loads(request.content)
        except (TypeError, ValueError):
            continue
        if (content.get("seo_brief") or {}).get("primary_query") == QUERY:
            matches.append(task)
    if len(matches) != 1:
        raise ValueError("Expected exactly one source-backed ServGo new article task")
    task = matches[0]
    if task.status != TaskStatus.COMPLETED:
        raise ValueError("Only a completed, validated task may receive a final critic audit")
    article_row = db.scalar(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id == task.id,
        Artifact.kind == "article").limit(1))
    if not article_row:
        raise ValueError("No completed new article")
    if db.scalar(select(Publication.id).where(
        Publication.product_id == product_id, Publication.artifact_id == article_row.id
    ).limit(1)):
        raise ValueError("Article has an existing publication; do not override it")
    article = ArticleArtifact.model_validate_json(article_row.content)
    if article.seo_brief is None or article.seo_brief.primary_query != QUERY:
        raise ValueError("Unexpected article SEO provenance")
    problems = (static_problems(article) + unsupported_servgo_pricing(db, article_row, article)
                + servgo_buyer_intent_problems(db, article_row, article)
                + duplicate_page_problems(db, article_row, article))
    if problems:
        raise ValueError("Article failed static checks: " + "; ".join(problems))
    if critic_verdict(db, task.id, article.reader_article) != "missing":
        raise ValueError("Critic already has a verdict; do not override an existing decision")
    return task.id, article_row.id, hashlib.sha256(article_row.content.encode()).hexdigest(), article


def rebuild_full_text(article):
    reader = article.reader_article
    items = ["# " + reader.title, reader.lead,
             "**Коротко:** " + reader.direct_answer]
    if reader.fact_renderings:
        items.append("## Проверенные возможности ServGo\n\n" +
                     "\n".join("- " + f.text for f in reader.fact_renderings))
    items.extend("## " + section.heading + "\n\n" + section.body
                 for section in reader.sections)
    items.append(reader.closing)
    if reader.faq:
        items.append("## Частые вопросы\n\n" + "\n\n".join(
            "### " + f.question + "\n\n" + f.answer for f in reader.faq))
    items.append(article.conversion.cta)
    return "\n\n".join(items)


async def main():
    settings = _check_production_preconditions()
    with SessionLocal() as db:
        product = db.scalar(select(Product).where(Product.slug == "servgo"))
        if product is None:
            raise ValueError("ServGo company not found")
        require_enabled(db, product.id)
        company_id = product.id
        task_id, article_id, original_hash, article = find_article(db, company_id)

    reader = article.reader_article
    if reader is None:
        raise ValueError("No final reader content")
    claims = approved_company_claims(settings.products_root)
    editable = editable_payload(reader)
    prompt = (
        "You are the independent editor and fact-checker for a final Russian "
        "B2B SEO article addressed to owners/managers of small service businesses. "
        "Review the complete reader-facing article for the query 'ии для продвижения бизнеса'. "
        "Check search intent, readability, spelling, factual grounding against the "
        "approved ServGo product evidence, invented outcomes and prices, unclear "
        "automation boundaries, privacy claims, and unverified product features. "
        "ServGo's CRM contact notes and next-contact dates are manually managed; "
        "CRM-to-Mail automation and guaranteed leads are NOT verified. "
        "Use the response schema to edit ONLY reader-facing text. "
        "Never change verified sources or evidence IDs. "
        "Prefer actionable buyer guidance over promotional repetition."
    )
    request = LLMRequest(
        system_prompt=prompt,
        messages=[LLMMessage(role="user", content=json.dumps({
            "query": QUERY,
            "candidate": editable,
            "immutable_verified_fact_renderings": [
                f.model_dump(mode="json") for f in reader.fact_renderings
            ],
            "approved_company_facts": claims,
        }, ensure_ascii=False))],
        model=settings.llm_model, max_tokens=18000,
        response_schema=EditableOwnerArticle.model_json_schema(),
        metadata={"reasoning_effort":"low", "team_role":"marketing_editor",
                  "task_type":"article_production", "seo_article":True,
                  "company_slug":"servgo"},
    )

    def validate_correction(data):
        updated = apply_editorial_copy(reader, data)
        if not updated.direct_answer or not updated.meta_description:
            raise ValueError("Missing essential SEO answer")

    provider = build_llm_provider(settings)
    response = await second_opinion(
        request=request,
        original=LLMResponse(content=json.dumps(editable, ensure_ascii=False),
                             model="persisted_editor", stop_reason="end_turn"),
        generate=provider.generate,
        validate_revision=validate_correction,
        review_timeout=300,
        revision_timeout=480,
        max_context_chars=150000,
        max_original_chars=90000,
    )
    review = response.quality_review or {}
    status = str(review.get("status") or "missing")
    print("SEO_THIRD_CRITIC", json.dumps({
        "status": status,
        "findings": len(review.get("findings") or []),
        "revision_attempts": review.get("attempted_extra_calls"),
    }), flush=True)
    if status not in CLEAN_CRITIC_STATUSES:
        raise ValueError("Independent critic did not finish with a publishable verdict")
    revised = apply_editorial_copy(
        reader, EditableOwnerArticle.model_validate_json(response.content).model_dump(mode="json"))
    candidate = article.model_copy(deep=True)
    candidate.reader_article = revised
    candidate.seo.title = revised.title
    candidate.full_article = rebuild_full_text(candidate)
    candidate = ArticleArtifact.model_validate(candidate.model_dump(mode="json"))

    with SessionLocal() as db:
        db.scalar(select(Product.id).where(
            Product.id == company_id).with_for_update())
        require_enabled(db, company_id)
        locked = db.scalar(select(Artifact).where(
            Artifact.id == article_id, Artifact.product_id == company_id
        ).with_for_update())
        if locked is None or hashlib.sha256(locked.content.encode()).hexdigest() != original_hash:
            raise ValueError("Article modified since critic began")
        if db.get(Task, task_id).status != TaskStatus.COMPLETED:
            raise ValueError("Original task changed since critic began")
        # Re-run static checks on the revised final reader before recording approval evidence.
        problems = (static_problems(candidate) + unsupported_servgo_pricing(db, locked, candidate)
                    + servgo_buyer_intent_problems(db, locked, candidate)
                    + duplicate_page_problems(db, locked, candidate))
        if problems:
            raise ValueError("Revised article blocked: " + "; ".join(problems))
        now = datetime.now(timezone.utc)
        run = AgentRun(task_id=task_id, agent_name="marketing_editor",
                       provider=settings.llm_provider, model=settings.llm_model,
                       status="completed", started_at=now, finished_at=now,
                       input_references=[{"kind":"artifact","id":str(article_id)}],
                       output_references=[{"kind":"artifact","id":str(article_id)}])
        db.add(run)
        db.flush()
        review_copy = {**review, "reader_hash": reader_hash(candidate.reader_article)}
        db.add(Artifact(product_id=company_id, task_id=task_id,
                        agent_run_id=run.id, kind="seo_final_review",
                        title="Independent review of completed new ServGo article",
                        content=json.dumps(review_copy, ensure_ascii=False),
                        status=ArtifactStatus.DRAFT))
        locked.content = candidate.model_dump_json()
        locked.title = revised.title
        locked.version = (locked.version or 1) + 1
        db.commit()
        outcome = _publish_for_this_release(db, company_id, article_id)
        print("SEO_THIRD_PUBLISH", json.dumps({
            "published": outcome.published,
            "reason": outcome.reason[:150],
            "slug": ("/blog/" + outcome.slug) if outcome.slug else None,
        }, ensure_ascii=False), flush=True)
        if not outcome.published:
            raise ValueError("Independent review finished but publication guards blocked the page")


if __name__ == "__main__":
    asyncio.run(main())
