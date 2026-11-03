"""One-time reviewed corrections for two existing ServGo consumer-intent SEO pages.

No automatic execution on import/deploy. Without --apply this runs read-only;
--apply requires the configured LLM critic and writes only after it passes.
The original ArticleArtifact is preserved in an append-only backup Artifact.
"""
import argparse
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
from app.models.domain import AgentRun, Artifact, ArtifactStatus, Product, Publication
from app.seo.publishing import (
    CLEAN_CRITIC_STATUSES, reader_hash, static_problems, unsupported_servgo_pricing,
)
from app.workflows.article_contracts import ArticleArtifact, HumanEditedArticle


# These are intentionally consumer-focused pages, matching the existing
# collected primary queries. Business-owner CRM topics need their own queries.
REWRITES = {
    "/blog/zapis-k-psihologu": {
        "language": "ru",
        "title": "Запись к психологу: как выбрать формат и подтвердить встречу",
        "lead": (
            "Перед первой встречей с психологом важно разобраться не только в свободном времени. "
            "Уточните, работает ли специалист с вашим запросом, проводит ли консультации онлайн "
            "или очно, сколько длится встреча и какие действуют условия переноса. "
            "После этого можно выбрать время и отправить заявку."
        ),
        "direct_answer": (
            "Чтобы записаться к психологу, выберите специалиста, уточните формат, "
            "длительность и стоимость встречи, затем согласуйте время в переписке "
            "или выберите свободный слот в форме записи. Оставьте необходимые контакты "
            "и убедитесь, что получили подтверждение. Отправленная заявка не всегда "
            "означает, что встреча уже согласована."
        ),
        "meta_description": (
            "Как записаться к психологу: выбор онлайн- или очной встречи, свободного времени, "
            "условия предоплаты, подтверждение и перенос без путаницы."
        ),
        "sections": [
            {"heading": "Что уточнить перед записью",
             "body": (
                 "Посмотрите, с какими запросами работает специалист. "
                 "Уточните формат встречи, её длительность, стоимость и адрес кабинета "
                 "или способ подключения онлайн. Узнайте правила отмены и переноса. "
                 "Если этих сведений нет на странице, лучше спросить напрямую, "
                 "чем выбирать время без понимания условий."
             )},
            {"heading": "Как согласовать время",
             "body": (
                 "Если у специалиста есть форма онлайн-записи, обычно достаточно нескольких действий:\n"
                 "1. Выбрать консультацию и подходящий формат.\n"
                 "2. Посмотреть доступные даты и время.\n"
                 "3. Оставить имя и контакт для ответа.\n"
                 "4. Отправить заявку и проверить её статус.\n"
                 "Если формы нет, можно договориться в сообщениях. "
                 "Главное — получить однозначное подтверждение даты, времени и формата встречи."
             )},
            {"heading": "Предоплата и подтверждение",
             "body": (
                 "Правила оплаты различаются у специалистов. До перевода денег уточните "
                 "сумму, реквизиты, порядок возврата и условия отмены. "
                 "В сервисах с ручным подтверждением предоплаты отправленный чек "
                 "ещё не означает, что платёж проверен. Дождитесь сообщения "
                 "или статуса, подтверждающего запись."
             )},
            {"heading": "Что сохранить после записи",
             "body": (
                 "Запишите дату, время, адрес или ссылку на встречу и контакт специалиста. "
                 "Если встреча очная, заранее уточните путь до кабинета; если онлайн — "
                 "как подключиться и что делать при техническом сбое. "
                 "При изменении планов предупредите специалиста по согласованным правилам."
             )},
            {"heading": "Когда лучше написать специалисту, а не выбирать слот",
             "body": (
                 "Если вам нужно уточнить особенности формата, доступность кабинета "
                 "или другие важные организационные условия, короткий разговор может "
                 "быть полезнее автоматической формы. Онлайн-запись — удобный вариант, "
                 "но не обязательное условие для получения консультации. "
                 "Не передавайте подробные чувствительные сведения о себе "
                 "в открытых полях записи без необходимости."
             )},
        ],
        "closing": (
            "Хорошая запись — та, после которой обе стороны понимают, где, когда "
            "и на каких условиях пройдёт встреча. Если вы сами ведёте практику, "
            "ServGo позволяет записывать клиентов вручную в админ-календаре "
            "и вести их историю в отдельной базе контактов. "
            "Посмотреть этот рабочий сценарий можно на демонстрации платформы."
        ),
        "faq": [
            {"question": "Если я отправил заявку, встреча уже подтверждена?",
             "answer": "Не обязательно. Проверьте статус или дождитесь явного подтверждения специалиста."},
            {"question": "Обязательна ли онлайн-запись к психологу?",
             "answer": "Нет. Можно согласовать время напрямую; форма записи — дополнительный способ организовать встречу."},
        ],
    },
    "/blog/zapisatsya-na-manikyur-onlayn": {
        "language": "ru",
        "title": "Как записаться на маникюр онлайн и не запутаться в услугах",
        "lead": (
            "Форма онлайн-записи помогает выбрать время без звонка в салон, "
            "но перед подтверждением стоит проверить состав услуги, длительность "
            "и условия предоплаты. Так меньше риск прийти не на ту процедуру "
            "или неверно рассчитать время."
        ),
        "direct_answer": (
            "Чтобы записаться на маникюр онлайн, откройте страницу записи "
            "выбранного салона, уточните нужную услугу, выберите мастера при необходимости, "
            "дату и свободное время, оставьте контакты и отправьте заявку. "
            "Проверьте подтверждение и правила предоплаты: они зависят от салона."
        ),
        "meta_description": (
            "Пошаговая запись на маникюр онлайн: как выбрать услугу и мастера, "
            "проверить время, предоплату и подтверждение визита."
        ),
        "sections": [
            {"heading": "Сначала выберите нужную услугу",
             "body": (
                 "Маникюр без покрытия, с покрытием, укреплением и снятием "
                 "могут отличаться по цене и длительности. Проверьте, "
                 "что именно включено в выбранную позицию. Если описание "
                 "неоднозначное, уточните состав услуги у салона до записи."
             )},
            {"heading": "Как выбрать время и мастера",
             "body": (
                 "1. Откройте страницу записи салона.\n"
                 "2. Выберите услугу и при необходимости мастера.\n"
                 "3. Найдите свободное время с учётом длительности процедуры.\n"
                 "4. Укажите контакты и отправьте заявку.\n"
                 "5. Убедитесь, что запись принята и подтверждена.\n"
                 "Если у салона нет онлайн-календаря, время можно согласовать напрямую."
             )},
            {"heading": "Предоплата: что проверить",
             "body": (
                 "Если салон просит предоплату, заранее уточните сумму, "
                 "получателя, правила отмены и возврата. "
                 "В некоторых системах клиент прикрепляет подтверждение перевода, "
                 "а администратор проверяет платёж вручную. "
                 "Не считайте скриншот оплаты равнозначным подтверждённому визиту."
             )},
            {"heading": "Перед визитом",
             "body": (
                 "Сохраните дату, время, адрес и контакты салона. "
                 "Проверьте правила опоздания и переноса. "
                 "Если возник вопрос о дизайне, снятии старого покрытия "
                 "или чувствительности к материалам, обсудите его с мастером "
                 "заранее, а не полагайтесь только на название услуги в форме."
             )},
        ],
        "closing": (
            "Удобная запись оставляет понятное подтверждение и освобождает "
            "от лишних уточнений, но качество услуги зависит от мастера и салона. "
            "Если вы управляете салоном, в ServGo можно вести визуальное расписание, "
            "записывать клиентов вручную и сохранять историю посещений в CRM. "
            "Самостоятельная онлайн-запись для клиентов — отдельный доступный сценарий."
        ),
        "faq": [
            {"question": "Можно ли записаться без выбора конкретного мастера?",
             "answer": "Это зависит от формы записи салона. Если доступен вариант без выбора мастера, проверьте свободное время и условия услуги."},
            {"question": "Если предоплата отправлена, запись уже подтверждена?",
             "answer": "Не всегда. При ручной проверке платежа дождитесь подтверждения салона или обновления статуса заявки."},
        ],
    },
}
REVISION_NOTE = "servgo-seo-refactor-20261103"

CTA = {
    "/blog/zapis-k-psihologu": (
        "Ведёте практику психолога? Посмотрите, как вручную записывать клиентов "
        "и хранить историю приёмов в ServGo: запросите демонстрацию "
        "в Telegram @Grunvald или по адресу grunvaldlab@gmail.com."
    ),
    "/blog/zapisatsya-na-manikyur-onlayn": (
        "Управляете салоном? Посмотрите расписание и CRM ServGo, "
        "куда администратор заносит записи вручную: запросите демонстрацию "
        "в Telegram @Grunvald или по адресу grunvaldlab@gmail.com."
    ),
}


def checked_article(original: ArticleArtifact, slug: str, reader: HumanEditedArticle) -> ArticleArtifact:
    if slug not in REWRITES:
        raise ValueError("Unapproved article slug")
    if original.reader_article is None or original.seo_brief is None:
        raise ValueError("Missing original SEO reader")
    if reader.fact_renderings != original.reader_article.fact_renderings:
        raise ValueError("Facts must match already approved fact renderings")
    if not reader.meta_description or not reader.direct_answer:
        raise ValueError("Search article needs meta and direct answer")
    parts = ["# " + reader.title, reader.lead,
             "**Коротко:** " + reader.direct_answer]
    parts.extend("## " + item.heading + "\n\n" + item.body for item in reader.sections)
    if reader.fact_renderings:
        parts.append("## Проверенные функции ServGo\n\n" +
                     "\n".join("- " + fact.text for fact in reader.fact_renderings))
    parts.append(reader.closing)
    if reader.faq:
        parts.append("## Частые вопросы\n\n" + "\n\n".join(
            "### " + f.question + "\n\n" + f.answer for f in reader.faq
        ))
    result = original.model_copy(deep=True)
    result.reader_article = reader
    result.full_article = "\n\n".join(parts)
    result.conversion.cta = CTA[slug]
    result.seo.title = reader.title
    return ArticleArtifact.model_validate(result.model_dump(mode="json"))


def build_reader(slug: str, original: ArticleArtifact) -> HumanEditedArticle:
    data = {**REWRITES[slug], "editorial_notes": [REVISION_NOTE],
            "fact_renderings": [
                fact.model_dump(mode="json")
                for fact in original.reader_article.fact_renderings
            ]}
    return HumanEditedArticle.model_validate(data)


async def revise_one(product_id, artifact_id, slug: str, original_hash: str, apply: bool) -> dict:
    with SessionLocal() as db:
        row = db.get(Artifact, artifact_id)
        original = ArticleArtifact.model_validate_json(row.content)
        if REVISION_NOTE in (original.reader_article.editorial_notes if original.reader_article else []):
            return {"slug": slug, "state": "already_revised"}
        reader = build_reader(slug, original)
        draft = checked_article(original, slug, reader)
        problems = static_problems(draft) + unsupported_servgo_pricing(db, row, draft)
        if problems:
            raise ValueError("Draft blocked by static checks: " + "; ".join(problems))

    if not apply:
        return {"slug": slug, "state": "ready_for_review", "sha256": original_hash,
                "new_sections": len(reader.sections)}

    settings = get_settings()
    provider = build_llm_provider(settings)
    original_reply = LLMResponse(content=reader.model_dump_json(), model="manual_editor",
                                 stop_reason="end_turn")
    request = LLMRequest(
        system_prompt=(
            "Review the supplied fully written Russian SEO article. It is for a "
            "consumer asking how to make an appointment, NOT a business software buyer. "
            "A short, explicitly owner-targeted final paragraph may connect to ServGo, "
            "but the main text must serve the consumer search intent. "
            "Check each factual claim against supplied verified capabilities. "
            "Critically flag any claim that customers can book an arbitrary real "
            "psychologist/salon directly through ServGo without the business offering "
            "that booking, made-up prices and percentages, automatic CRM-to-Mail "
            "reactivation, forced online self-booking, fabricated availability, "
            "or claims about treatment outcomes. "
            "Check clarity, real numbered lists and unnecessary repetition. "
            "Respect all caveats; do not fabricate sources. "
            "Answer as the prescribed HumanEditedArticle JSON schema."
        ),
        messages=[LLMMessage(role="user", content=json.dumps({
            "primary_query": original.seo.primary_query,
            "reader_intent": "consumer appointment logistics",
            "candidate": reader.model_dump(mode="json"),
            "verified_servgo_features": [
                "owner-admin day/week visual schedule",
                "owner-admin manual appointment entry, rescheduling",
                "business-scoped client CRM, appointment history and notes",
                "optional customer self-booking with confirmation",
                "optional manual prepayment verification",
                "no verified CRM-to-Mail automated customer reactivation",
            ],
            "editorial_goal": "One search intent, informative, concise without artificial length cap",
        }, ensure_ascii=False))],
        model=settings.llm_model, max_tokens=12000,
        response_schema=HumanEditedArticle.model_json_schema(),
        metadata={"reasoning_effort": "low", "team_role": "marketing_editor",
                  "task_type": "article_production", "seo_article": True,
                  "company_slug": "servgo"},
    )

    def validate(data):
        checked_article(original, slug, HumanEditedArticle.model_validate(data))

    review_reply = await second_opinion(
        request=request, original=original_reply, generate=provider.generate,
        validate_revision=validate,
        max_context_chars=150_000, max_original_chars=70_000,
        review_timeout=90, revision_timeout=120,
    )
    verdict = review_reply.quality_review or {}
    status = verdict.get("status")
    if status not in CLEAN_CRITIC_STATUSES:
        raise ValueError("Published revision blocked by critic status: " + str(status))
    if any(item.get("severity") == "critical" and
           not any(d.get("id") == item.get("id") and d.get("decision") == "accept"
                   for d in verdict.get("decisions", []))
           for item in verdict.get("findings", [])):
        raise ValueError("Unresolved critical reviewer finding")
    revised = HumanEditedArticle.model_validate_json(review_reply.content)
    candidate = checked_article(original, slug, revised)

    # All reviewed revisions are prepared before any published content changes.
    # One failing review must never leave a partially rewritten blog.
    return {"slug": slug, "state": "reviewed_ready_to_commit",
            "critic": status, "candidate": candidate.model_dump_json(),
            "review": verdict, "original_sha256": original_hash}


async def run(apply: bool) -> list[dict]:
    with SessionLocal() as db:
        product = db.scalar(select(Product).where(Product.slug == "servgo"))
        if product is None:
            raise LookupError("ServGo product profile missing")
        published = list(db.execute(
            select(Artifact, Publication).join(
                Publication, Publication.artifact_id == Artifact.id
            ).where(
                Publication.product_id == product.id,
                Publication.channel == "own_site",
                Publication.status == "published",
                Publication.external_reference.in_(tuple(REWRITES))
            ).order_by(Publication.published_at)
        ))
        if (len(published) != len(REWRITES) or
            {pub.external_reference for _, pub in published} != set(REWRITES)):
            raise ValueError("Expected exactly the two inspected published pages")
        entries = [
            (product.id, article.id, publication.external_reference,
             hashlib.sha256(article.content.encode("utf-8")).hexdigest())
            for article, publication in published
        ]

    # Phase 1: validate and critic-review ALL replacements with zero DB mutation.
    prepared = [await revise_one(*args, apply=apply) for args in entries]
    if not apply:
        return prepared

    # Phase 2: publish everything atomically, or roll back all changes.
    outcomes = []
    with SessionLocal() as db:
        staged = []
        for (product_id, artifact_id, slug, original_hash), revision in zip(entries, prepared):
            if revision["state"] == "already_revised":
                outcomes.append({"slug": slug, "state": "already_revised"})
                continue
            if revision["state"] != "reviewed_ready_to_commit":
                raise ValueError("Article did not pass the editorial critic")
            row = db.scalar(select(Artifact).where(Artifact.id == artifact_id).with_for_update())
            if row is None or row.product_id != product_id or row.status != ArtifactStatus.PUBLISHED:
                raise ValueError("Published article changed during review")
            if hashlib.sha256(row.content.encode("utf-8")).hexdigest() != original_hash:
                raise ValueError("Article changed since read-only preflight")
            pub = db.scalar(select(Publication).where(
                Publication.product_id == product_id,
                Publication.artifact_id == artifact_id,
                Publication.channel == "own_site", Publication.status == "published",
                Publication.external_reference == slug
            ).limit(1))
            if pub is None:
                raise ValueError("Published article reference changed")
            candidate = ArticleArtifact.model_validate_json(revision["candidate"])
            errors = static_problems(candidate) + unsupported_servgo_pricing(db, row, candidate)
            if errors:
                raise ValueError("Revised article failed static checks: " + "; ".join(errors))
            staged.append((row, product_id, slug, revision, candidate))

        for row, product_id, slug, revision, candidate in staged:
            backup = Artifact(
                product_id=product_id, task_id=row.task_id,
                kind="seo_article_revision_backup", title="Before editorial fix: " + slug,
                content=row.content, status=ArtifactStatus.DRAFT,
            )
            db.add(backup)
            now = datetime.now(timezone.utc)
            reviewer = AgentRun(
                task_id=row.task_id, agent_name="marketing_editor", status="completed",
                provider=get_settings().llm_provider, model="manual_editor+critic",
                started_at=now, finished_at=now,
                input_references=[{"kind": "artifact", "id": str(row.id)}],
                output_references=[{"kind": "artifact", "id": str(row.id)}],
            )
            db.add(reviewer)
            db.flush()
            db.add(Artifact(
                product_id=product_id, task_id=row.task_id, agent_run_id=reviewer.id,
                kind="seo_final_review", title="Review of published editorial correction",
                content=json.dumps(
                    {**revision["review"], "reader_hash": reader_hash(candidate.reader_article)},
                    ensure_ascii=False
                ), status=ArtifactStatus.DRAFT,
            ))
            row.content = candidate.model_dump_json()
            row.title = candidate.reader_article.title
            row.version = (row.version or 1) + 1
            db.flush()
            outcomes.append({
                "slug": slug, "state": "revised_published", "critic": revision["critic"],
                "backup_artifact_id": str(backup.id), "version": row.version
            })
        db.commit()
    return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Use a real critic and update two reviewed published articles")
    args = parser.parse_args()
    results = asyncio.run(run(args.apply))
    for result in results:
        print("SERVGO_ARTICLE_REVISION=" + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
