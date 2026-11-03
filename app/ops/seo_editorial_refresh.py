"""Owner-approved, one-shot concise edit of existing published ServGo SEO pages.

Does not run on normal worker/deploy. No changes until a complete model response
passes one critic, source-preservation checks, SEO checks and a row-version lock.
Old text is backed up in the same transaction. Original blog URL is preserved.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.composition import build_llm_provider, SAFE_LLM_METADATA_FIELDS
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.llm.critic import second_opinion
from app.llm.schemas import LLMMessage, LLMRequest
from app.models.domain import AgentRun, Artifact, ArtifactStatus, Product, Publication
from app.privacy import PrivacyGateway
from app.seo.publishing import (
    critic_verdict, duplicate_page_problems, reader_hash, static_problems, unsupported_servgo_pricing,
)
from app.workflows.article_contracts import ArticleArtifact, HumanEditedArticle

ALLOWED_SLUGS = frozenset({"zapis-k-psihologu", "zapisatsya-na-manikyur-onlayn"})
_ALLOWED_REVIEW = frozenset({"revised", "no_material_issues"})
_MODEL_PROMPT = (
    "Ты редактор русскоязычных SEO-статей. Переработай существующую опубликованную "
    "статью в ясный, компактный текст, в духе принципов информационного стиля "
    "(без копирования текста книги): убери повторы, длинные сцены, общие рассуждения, "
    "пустые рекламные фразы и отступления в другие темы. Пиши для одного основного "
    "поискового намерения, указанного в primary_query; читатель должен получить "
    "конкретный ответ в начале и практические шаги дальше. Для запроса "
    "'запись к психологу' или 'записаться на маникюр онлайн' основной читатель — "
    "человек, который хочет записаться; не посвящай половину страницы управлению "
    "практикой или салоном. При необходимости оставь короткий вывод для владельца. "
    "Каждый пункт перечисления записывай с новой строки, отдельные абзацы отделяй "
    "переносом строки. Не добавляй обещаний, цифр, цены, медицинских советов, "
    "несуществующих функций, выдуманных отзывов и исходов. Сведения о ServGo "
    "описывай только согласно исходным подтверждённым данным и ограничениям; "
    "ИИ-чат доступен там, где он включён владельцем бизнеса, и использует "
    "настроенные услуги, цены и время, а не произвольные знания о бизнесе. "
    "Не придумывай новых фактов о способах подтверждения предоплаты. "
    "Сохраняй исходный title, language, meta_description по смыслу и важные "
    "условия, но всё остальное излагай короче: ориентир 3500–5600 знаков "
    "суммарного видимого текста, не норма ради нормы. Не увеличивай число разделов. "
    "fact_renderings должны быть точной копией original.fact_renderings; "
    "не добавляй ни одного нового source_id. Верни полный JSON указанной схемы."
)


def reader_chars(reader: HumanEditedArticle) -> int:
    return sum(map(len, (
        reader.lead, reader.direct_answer or "",
        *(section.body for section in reader.sections), reader.closing,
    )))


def validate_refresh(old: HumanEditedArticle, candidate: HumanEditedArticle) -> None:
    """Machine-check invariants; this is not a replacement for the factual critic."""
    if candidate.title != old.title or candidate.language != old.language:
        raise ValueError("original_title_or_language_changed")
    if candidate.fact_renderings != old.fact_renderings:
        raise ValueError("verified_facts_changed")
    if not candidate.direct_answer or not candidate.meta_description:
        raise ValueError("search_answer_or_description_missing")
    original_length, length = reader_chars(old), reader_chars(candidate)
    if length >= original_length or length > min(6100, int(original_length * .75)):
        raise ValueError("article_not_materially_shorter")
    if length < 1800:
        raise ValueError("essential_guidance_may_be_missing")
    if len(candidate.sections) > len(old.sections):
        raise ValueError("section_count_increased")


def assemble_reader(reader: HumanEditedArticle, cta: str) -> str:
    verified = ""
    if reader.fact_renderings:
        verified = (
            "## Проверенные возможности\n\n"
            + "\n".join("- " + fact.text for fact in reader.fact_renderings)
            + "\n\n"
        )
    body = "\n\n".join(
        f"## {section.heading}\n\n{section.body}"
        for section in reader.sections
    )
    faq = ""
    if reader.faq:
        faq = "## Частые вопросы\n\n" + "\n\n".join(
            f"### {point.question}\n\n{point.answer}" for point in reader.faq
        )
    return "\n\n".join(part for part in (
        "# " + reader.title, reader.lead,
        ("**Коротко:** " + reader.direct_answer) if reader.direct_answer else "",
        verified.strip(), body, reader.closing, faq, cta,
    ) if part)


def get_published(db, slug: str):
    if slug not in ALLOWED_SLUGS:
        raise ValueError("slug_is_not_in_owner_approved_scope")
    prod = db.scalar(select(Product).where(Product.slug == "servgo"))
    if prod is None:
        raise LookupError("servgo_product_missing")
    row = db.execute(
        select(Artifact, Publication).join(Publication, Publication.artifact_id == Artifact.id)
        .where(
            Artifact.product_id == prod.id,
            Artifact.kind == "article",
            Publication.product_id == prod.id,
            Publication.channel == "own_site",
            Publication.status == "published",
            Publication.external_reference == "/blog/" + slug,
        )
    ).first()
    if row is None:
        raise LookupError("approved_published_article_missing")
    return prod, row[0], row[1]


async def edit_published_slug(slug: str, *, apply: bool) -> dict:
    settings = get_settings()
    with SessionLocal() as db:
        prod, row, _ = get_published(db, slug)
        prior_content = row.content
        original = ArticleArtifact.model_validate_json(prior_content)
        original_reader = original.reader_article
        if original_reader is None or original.seo_brief is None or row.task_id is None:
            raise ValueError("missing_source_seo_article")
        if db.scalar(select(Artifact.id).where(
            Artifact.product_id == prod.id, Artifact.task_id == row.task_id,
            Artifact.kind == "seo_editorial_revision",
        )):
            return {"slug": slug, "status": "already_edited"}
        primary_query = original.seo.primary_query
        old_length = reader_chars(original_reader)
        if not apply:
            return {"slug": slug, "status": "inspection_only", "chars": old_length}
        context = {
            "primary_query": primary_query,
            "reader_problem": original.seo_brief.reader_problem,
            "reader_intent": original.seo_brief.intent,
            "original": original_reader.model_dump(mode="json"),
        }
        task_id, product_id = row.task_id, prod.id

    provider = build_llm_provider(settings)
    privacy = PrivacyGateway(allowed_context_fields=SAFE_LLM_METADATA_FIELDS)
    schema = HumanEditedArticle.model_json_schema()
    request = LLMRequest(
        system_prompt=_MODEL_PROMPT + "\nSchema: " + json.dumps(schema),
        messages=[LLMMessage(role="user", content=json.dumps(context, ensure_ascii=False))],
        model=settings.llm_model, max_tokens=7000, response_schema=schema,
        metadata={"reasoning_effort": "low", "team_role": "marketing_editor",
                  "task_type": "article_production", "seo_article": True},
    )
    initial = await provider.generate(privacy.sanitize(request))
    if initial.stop_reason not in (None, "end_turn", "stop_sequence"):
        raise ValueError("incomplete_editorial_rewrite")
    draft = HumanEditedArticle.model_validate_json(initial.content)
    validate_refresh(original_reader, draft)

    # The reviewer sees the *entire* revised draft and original facts.
    # The revised reader is applied only if the critic has no unresolved issues.
    critic_request = request.model_copy(update={
        "system_prompt": (
            "Fact-check and tighten this edited SEO article against the original "
            "grounded source. Prioritize factual correctness, one search intent, "
            "readable ordered steps and preservation of all material caveats. "
            "No unverified ServGo capability claims. Schema: " + json.dumps(schema)
        ),
    })
    result = await second_opinion(
        request=privacy.sanitize(critic_request),
        original=initial,
        generate=lambda req: provider.generate(privacy.sanitize(req)),
        validate_revision=lambda data: validate_refresh(
            original_reader, HumanEditedArticle.model_validate(data)
        ),
        max_context_chars=75000, review_timeout=130, revision_timeout=160,
    )
    verdict = (result.quality_review or {}).get("status")
    if verdict not in _ALLOWED_REVIEW:
        raise ValueError("critic_not_clean:" + str(verdict))
    revised_reader = HumanEditedArticle.model_validate_json(result.content)
    validate_refresh(original_reader, revised_reader)

    with SessionLocal() as db:
        # Detect concurrent content edits; never overwrite an owner's newer version.
        prod, row, pub = get_published(db, slug)
        db.scalar(select(Product.id).where(Product.id == prod.id).with_for_update())
        db.refresh(row)
        if hashlib.sha256(row.content.encode()).digest() != hashlib.sha256(prior_content.encode()).digest():
            raise RuntimeError("article_changed_during_editorial_review")
        if db.scalar(select(Artifact.id).where(
            Artifact.product_id == prod.id, Artifact.task_id == task_id,
            Artifact.kind == "seo_editorial_revision",
        )):
            return {"slug": slug, "status": "already_edited"}
        revised = original.model_copy(update={
            "reader_article": revised_reader,
            "full_article": assemble_reader(revised_reader, original.conversion.cta),
        })
        issues = (static_problems(revised)
                  + unsupported_servgo_pricing(db, row, revised)
                  + duplicate_page_problems(db, row, revised))
        if issues:
            raise ValueError("static_quality_gate_failed:" + "|".join(issues))
        now = datetime.now(timezone.utc)
        backup = Artifact(product_id=prod.id, task_id=task_id,
                          kind="seo_editorial_backup", title="Before concise revision: " + slug,
                          content=row.content, status=ArtifactStatus.DRAFT)
        db.add(backup)
        review_run = AgentRun(
            task_id=task_id, agent_name="marketing_editor",
            provider=settings.llm_provider, model=result.model,
            status="completed", started_at=now, finished_at=now,
        )
        db.add(review_run)
        db.flush()
        report = dict(result.quality_review or {})
        report["reader_hash"] = reader_hash(revised_reader)
        db.add(Artifact(
            product_id=prod.id, task_id=task_id, agent_run_id=review_run.id,
            kind="seo_final_review", title="Concise SEO article fact review",
            content=json.dumps(report, ensure_ascii=False), status=ArtifactStatus.DRAFT,
        ))
        row.content = revised.model_dump_json()
        row.title = revised_reader.title
        row.version += 1
        row.updated_at = now
        db.flush()
        if critic_verdict(db, task_id, revised_reader) not in _ALLOWED_REVIEW:
            raise ValueError("fresh_critic_version_check_failed")
        db.add(Artifact(
            product_id=prod.id, task_id=task_id,
            kind="seo_editorial_revision", title="Concise SEO editorial audit",
            content=json.dumps({
                "slug": slug, "previous_artifact_id": str(backup.id),
                "old_chars": old_length, "new_chars": reader_chars(revised_reader),
                "primary_query": primary_query, "critic_status": verdict,
                "publication_url_unchanged": pub.external_reference,
            }, ensure_ascii=False), status=ArtifactStatus.DRAFT,
        ))
        db.commit()
        return {
            "slug": slug, "status": "revised_published",
            "old_chars": old_length, "new_chars": reader_chars(revised_reader),
            "critic": verdict, "url": pub.external_reference,
        }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", choices=sorted(ALLOWED_SLUGS), required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        report = await edit_published_slug(args.slug, apply=args.apply)
        print("SEO_EDITORIAL_RESULT=" + json.dumps(report, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        print("SEO_EDITORIAL_ERROR=" + type(exc).__name__
              + ":" + str(exc)[:150], flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
