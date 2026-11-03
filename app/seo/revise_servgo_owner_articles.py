"""One-time owner-intent correction of two ServGo SEO articles, with real-query provenance.

Dry-run by default. --apply performs one critic review per draft, validates
all sources, then atomically updates both publications. Originals and review
records remain in the DB. This is NOT a recurring factory workflow.
"""
import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import Field

from sqlalchemy import select

from app.core.composition import build_llm_provider
from app.company_profile import load_company_profile
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.llm.critic import second_opinion
from app.llm.schemas import LLMMessage, LLMRequest, LLMResponse
from app.models.domain import AgentRun, Artifact, ArtifactStatus, Product, Publication
from app.seo.clustering import normalize
from app.seo.publishing import (
    CLEAN_CRITIC_STATUSES, reader_hash, static_problems, unsupported_servgo_pricing,
)
from app.seo.service import latest_semantic_core
from app.workflows.article_contracts import (
    ArticleArtifact, Contract, FaqItem, HumanEditedArticle, ReaderSection,
    SeoBrief, ShortText, Text,
)

# The previous 90-second limit cut off the first live review without a verdict.
# Keep the independent critic mandatory; allow a bounded gateway-latency budget.
REVIEW_TIMEOUT_SECONDS = 240
AUTHOR_REVISION_TIMEOUT_SECONDS = 180

class EditableOwnerArticle(Contract):
    """Only reader-facing prose is editable by the reviewer.

    Verified product facts and application-generated editorial audit notes are
    NOT editable fields. The critic still sees them as read-only source context.
    """
    language: Literal["ru", "en"]
    title: ShortText
    lead: Text
    sections: list[ReaderSection] = Field(min_length=1, max_length=25)
    closing: Text
    direct_answer: Annotated[str, Field(min_length=1, max_length=2500)]
    faq: list[FaqItem] = Field(default_factory=list)
    meta_description: Annotated[str, Field(min_length=1, max_length=1000)]


_EDITABLE_FIELDS = frozenset(EditableOwnerArticle.model_fields)


def approved_company_claims(products_root) -> list[dict[str, str]]:
    """Load all current verified ServGo facts rather than a stale short list."""
    loaded = load_company_profile(products_root, "servgo")
    if loaded.profile is None:
        raise ValueError("Current verified ServGo profile unavailable for editorial review")
    facts = [{"statement": fact.statement, "evidence_ref": fact.evidence_ref}
             for fact in loaded.profile.proof.approved_facts]
    if not facts:
        raise ValueError("No approved company facts; cannot review product feature claims")
    return facts


def editable_payload(reader: HumanEditedArticle) -> dict:
    return {k: v for k, v in reader.model_dump(mode="json").items()
            if k in _EDITABLE_FIELDS}


def apply_editorial_copy(original: HumanEditedArticle, edit: dict) -> HumanEditedArticle:
    """Merge revised prose onto the immutable company-evidence fields.

    No AI-generated value can replace source IDs, verified fact renderings,
    or earlier review/audit markers. The completed article is revalidated.
    """
    copy = EditableOwnerArticle.model_validate(edit)
    return HumanEditedArticle.model_validate({
        **original.model_dump(mode="json"),
        **copy.model_dump(mode="json"),
    })


REVISION_NOTE = "servgo-buyer-intent-typography-20261103-v2"
PREVIOUS_NOTE = "servgo-seo-refactor-20261103"
BUYER = "Владельцы и управляющие небольших сервисных бизнесов с записью по времени"
NEXT_STEP = "Запросить демонстрацию календаря и CRM ServGo"
CTA = (
    "Ведёте приём или салон? Посмотрите на календарь и собственную CRM-базу "
    "ServGo: запросите демонстрацию в Telegram @Grunvald "
    "или по адресу grunvaldlab@gmail.com."
)
CONTENT = json.loads(r'''{"/blog/zapis-k-psihologu":{"new_slug":"/blog/kak-psihologu-organizovat-zapis-klientov","primary_query":"как организовать запись клиентов","reader_problem":"Владелец частной психологической практики хочет вести единое расписание онлайн- и очных встреч и не терять историю обращений.","title":"Как психологу организовать запись клиентов и не путаться в консультациях","meta_description":"Как психологу вести календарь консультаций, учитывать очные и онлайн-встречи, кабинет, перерывы и историю клиентов. Практическая схема и возможности ServGo.","lead":"Когда консультации согласуются в мессенджере, а время хранится ещё и в телефоне, легко забыть о переносе или занять один интервал дважды. **Психологу нужен не обязательно сервис самозаписи, а прежде всего единое рабочее расписание.** Разберём, как его организовать.","direct_answer":"Чтобы организовать запись клиентов психолога, заведите один календарь для всех встреч, отмечайте формат консультации, длительность, подтверждение и перенос, отдельно проверяйте доступность кабинета и сохраняйте контактную историю. Клиент может записываться через вас напрямую: самостоятельная онлайн-запись необязательна.","sections":[{"heading":"Начните с правил практики, а не с кнопки «Записаться»","body":"**Определите, какие встречи вы проводите:** индивидуальные, семейные, онлайн или очные. Укажите для каждой реальную продолжительность и время на подготовку.\n\n- Зафиксируйте рабочие дни и часы приёма.\n- Решите, кто подтверждает встречу и сообщает о переносе.\n- Оставляйте необходимый перерыв между консультациями в своём планировании.\n- Для очного формата отдельно проверяйте, свободен ли кабинет.\n\n*Не считайте отправленную заявку подтверждением аренды кабинета:* его доступность может требовать отдельного согласования."},{"heading":"Один календарь для всех обращений","body":"### Как выглядит рабочий сценарий\n1. Клиент пишет вам или звонит.\n2. Вы уточняете тип встречи и предлагаете подходящие интервалы.\n3. Вносите согласованную запись в календарь с именем, временем и форматом.\n4. При переносе обновляете эту же запись, а не создаёте параллельную заметку.\n5. Сообщаете клиенту окончательное подтверждение.\n\n**Главное правило:** переписка — канал общения, а календарь — место, где хранится актуальное время. Если есть администратор, он должен работать с тем же расписанием."},{"heading":"Онлайн и очно: где чаще возникает путаница","body":"Для онлайн-консультации обычно нужно согласовать время и способ подключения. Для очной встречи добавляются адрес, дорога и доступность помещения.\n\n> Перед подтверждением очной консультации проверьте кабинет. Запись клиента и бронирование помещения — не всегда одно действие.\n\nПерерыв между встречами стоит учитывать при определении доступного времени. Не нужно обещать посетителю слот, который ещё не согласован."},{"heading":"Клиентская база без лишних чувствительных данных","body":"### Что полезно хранить\n- Имя и способ связи, необходимый для организационных вопросов.\n- Даты предыдущих и будущих консультаций.\n- Факт переноса и согласованное время следующего контакта.\n\n**Календарь отвечает на вопрос «когда встреча», клиентская база — «с кем и что уже согласовано».** Подробности психологической работы, диагнозы или содержание консультации не следует автоматически переносить в обычную CRM: для них нужны отдельные правила конфиденциальности и защиты данных."},{"heading":"Что из этого уже можно делать в ServGo","body":"В ServGo есть **административный календарь с режимами дня и недели**. Администратор может вручную создать запись, указать услугу, специалиста или зал, время, имя и телефон клиента, добавить комментарий и перенести встречу.\n\nОтдельная CRM-база организации позволяет хранить контакты, историю записей, заметки и дату следующего контакта. **Самостоятельная онлайн-запись — дополнительный сценарий**, а не условие, без которого нельзя вести журнал.\n\n*Настройка перерывов как отдельной автоматической функции и автоматическое резервирование стороннего кабинета здесь не обещаются:* эти организационные условия нужно проверять самостоятельно."}],"closing":"Начните с одного согласованного календаря и понятных правил подтверждения — этого достаточно, чтобы перестать искать записи в разных чатах. Если хотите посмотреть, как вести расписание и клиентскую базу в одной системе, **запросите демонстрацию ServGo**.","faq":[{"question":"Психологу обязательно открывать самостоятельную онлайн-запись?","answer":"Нет. Можно принимать запросы лично или через администратора и фиксировать подтверждённое время в общем календаре. Онлайн-запись — дополнительный способ приёма обращений."},{"question":"Как учитывать время на дорогу и кабинет?","answer":"При планировании очных встреч оставляйте нужные интервалы и подтверждайте доступность кабинета отдельно. Не обещайте автоматическое бронирование помещения без соответствующей настройки."},{"question":"Зачем психологу клиентская база, если есть календарь?","answer":"Календарь показывает время встреч, а клиентская база помогает найти контакт, предыдущие и будущие записи. Содержимое консультаций требует отдельного режима конфиденциальности."}]},"/blog/zapisatsya-na-manikyur-onlayn":{"new_slug":"/blog/kak-masteru-manikyura-vesti-zapis-klientov","primary_query":"программа для записи клиентов в салоне красоты","reader_problem":"Владелец салона или мастер маникюра выбирает способ вести рабочее расписание, разные по длительности процедуры и клиентские контакты.","title":"Как мастеру маникюра вести запись клиентов: календарь, услуги и база","meta_description":"Как организовать запись клиентов в салоне маникюра: длительность услуг, расписание мастеров, переносы, единый журнал и CRM. Разбираем рабочую схему и ServGo.","lead":"Клиентка пишет: «Есть свободное время сегодня?». Мастер отвечает между процедурами, администратор смотрит другой календарь — и данные расходятся. **Сначала наведите порядок в общем расписании**, а уже потом выбирайте, нужна ли самостоятельная онлайн-запись.","direct_answer":"Мастеру маникюра или салону нужен единый журнал записи: актуальные часы работы мастеров, точная длительность каждой услуги, запись клиента с контактами и единые правила переноса. Клиентская база сохраняет историю визитов. Онлайн-запись можно добавить, но вести расписание можно и вручную.","sections":[{"heading":"Опишите услуги так, чтобы хватало времени на процедуру","body":"**Название услуги недостаточно.** Маникюр без покрытия, снятие, укрепление и дизайн могут требовать разного времени. Уточните состав каждой услуги и её фактическую продолжительность, прежде чем предлагать клиенту интервал.\n\n- Услуга и её точное название.\n- Продолжительность с учётом выбранных дополнительных работ.\n- Какие мастера могут её выполнить.\n- Согласованные условия переноса и предоплаты, если они применяются.\n\n*Не нужно придумывать одинаковую длительность для всех мастеров*: опирайтесь на реальный рабочий процесс салона."},{"heading":"Заведите единый рабочий календарь","body":"### Запись через администратора\n1. Клиент обращается в мессенджере или по телефону.\n2. Администратор выбирает услугу, мастера и свободное время.\n3. Создаёт запись в одном расписании и сохраняет контакт.\n4. При переносе изменяет запись там же.\n5. Подтверждает клиенту новые условия.\n\n**Важно:** сообщения о записи могут приходить из разных мест, но итоговое время должно быть в одном журнале. Иначе два канала могут предложить один и тот же интервал."},{"heading":"Учитывайте смены, подготовку и переносы","body":"Для мастера важны не только свободные часы, но и фактическая занятость: смены, перерывы, уборка рабочего места и дополнительные процедуры.\n\n> Свободное окно — не любой пробел между записями. Убедитесь, что в него целиком помещается нужная услуга и время на подготовку.\n\nЕсли расписание меняется, обновляйте его в том же календаре. **Не обещайте, что система автоматически рассчитает все перерывы**, пока такая функция не проверена."},{"heading":"Храните историю клиентов в своей базе","body":"### Чем помогает CRM\n- Быстро найти контакт, не перелистывая переписки.\n- Посмотреть предыдущие и будущие записи.\n- Зафиксировать организационные предпочтения и согласованный следующий контакт.\n\nКлиентская база должна принадлежать вашему бизнесу и использоваться аккуратно. **История визитов — это полезный контекст**, но не повод отправлять рекламу без согласия. Автоматические рассылки из CRM не следует считать включёнными по умолчанию."},{"heading":"Где здесь ServGo и нужна ли форма онлайн-записи","body":"В ServGo владелец или администратор может **вести календарь по дням и неделям**, вручную добавлять записи по услугам и специалистам и переносить их. CRM организации хранит контакты, историю посещений, заметки и даты следующего контакта.\n\nСамостоятельная онлайн-запись тоже предусмотрена, но её стоит использовать тогда, когда она удобна вашему формату работы. **Основой остаются общий журнал и клиентская база**, а не количество кнопок на сайте.\n\n*Автоматическую связку CRM с почтовыми кампаниями и измеренный рост повторных визитов здесь не обещаем.*"}],"closing":"Для начала достаточно упорядочить услуги, смены и записи в одном месте. **Хотите увидеть календарь и CRM на практике? Запросите демонстрацию ServGo**, чтобы оценить сценарий для своего салона или рабочего места.","faq":[{"question":"Мастеру обязательно нужна самостоятельная онлайн-запись?","answer":"Нет. Если клиентам привычнее писать или звонить, администратор может вносить подтверждённые записи вручную. Форма онлайн-записи — дополнительный вариант."},{"question":"Что делать, если процедуры имеют разную длительность?","answer":"Укажите продолжительность каждой услуги и уточняйте дополнительные работы при записи. Проверьте, что интервал целиком подходит под выбранную процедуру."},{"question":"Зачем хранить историю клиентов?","answer":"Чтобы видеть предыдущие и будущие визиты и не искать контакт по разным чатам. Для рекламных сообщений необходимо учитывать согласие получателя."}]}}''')


def _query_in_collected_core(db, product_id, query: str):
    core = latest_semantic_core(db, product_id)
    if core is None:
        raise ValueError("No collected semantic core; cannot assert query provenance")
    matches = [(c, q) for c in core.clusters for q in c.queries
               if normalize(q) == normalize(query)]
    if len(matches) != 1:
        raise ValueError("New primary query must match one current collected cluster")
    return matches[0][0]


def candidate_for(original: ArticleArtifact, slug: str, cluster) -> ArticleArtifact:
    if slug not in CONTENT:
        raise ValueError("Unknown published page")
    if original.reader_article is None or original.seo_brief is None:
        raise ValueError("Expected original SEO reader and brief")
    data = CONTENT[slug]
    notes = [x for x in original.reader_article.editorial_notes if x != REVISION_NOTE]
    notes.append(REVISION_NOTE)
    reader = HumanEditedArticle.model_validate({
        **original.reader_article.model_dump(mode="json"),
        **{k: data[k] for k in ("title", "lead", "direct_answer", "meta_description",
                              "sections", "closing", "faq")},
        "editorial_notes": notes,
    })
    new = original.model_copy(deep=True)
    new.reader_article = reader
    new.seo.primary_query = data["primary_query"]
    new.seo.title = data["title"]
    new.seo.related_queries = []
    new.seo_brief = SeoBrief(
        plan_item_id=original.seo_brief.plan_item_id,
        primary_query=data["primary_query"],
        related_queries=[],
        stage=cluster.stage,
        intent=cluster.intent,
        reader_problem=data["reader_problem"],
        target_segment=BUYER,
        ai_questions=[data["reader_problem"]],
        next_step=NEXT_STEP,
    )
    new.conversion.cta = CTA
    parts = ["# " + reader.title, reader.lead,
             "**Коротко:** " + reader.direct_answer]
    parts.extend("## " + section.heading + "\n\n" + section.body
                 for section in reader.sections)
    if reader.fact_renderings:
        parts.append("## Проверенные факты о компании\n\n" + "\n".join(
            "- " + fact.text for fact in reader.fact_renderings))
    parts.append(reader.closing)
    if reader.faq:
        parts.append("## Частые вопросы\n\n" + "\n\n".join(
            "### " + f.question + "\n\n" + f.answer for f in reader.faq))
    new.full_article = "\n\n".join(parts)
    return ArticleArtifact.model_validate(new.model_dump(mode="json"))


async def _review(entry: dict, apply: bool) -> dict:
    with SessionLocal() as db:
        row = db.get(Artifact, entry["id"])
        original = ArticleArtifact.model_validate_json(row.content)
        if REVISION_NOTE in (original.reader_article.editorial_notes if original.reader_article else []):
            if entry["old"] != entry["new"]:
                raise ValueError("Already revised reader but still using old page slug")
            return {**entry, "state": "already_revised"}
        if PREVIOUS_NOTE not in (original.reader_article.editorial_notes if original.reader_article else []):
            raise ValueError("Expected exact previously reviewed consumer article version")
        cluster = _query_in_collected_core(db, entry["product_id"], entry["query"])
        candidate = candidate_for(original, entry["old"], cluster)
        errors = static_problems(candidate) + unsupported_servgo_pricing(db, row, candidate)
        if errors:
            raise ValueError("Draft failed static checks: " + "; ".join(errors))

    if not apply:
        return {**entry, "state": "ready", "sections": len(candidate.reader_article.sections),
                "query": entry["query"], "new_slug": entry["new"]}

    settings = get_settings()
    verified_facts = approved_company_claims(settings.products_root)
    provider = build_llm_provider(settings)
    reader = candidate.reader_article
    # Reviewer receives the full intended article exactly once, plus separately
    # pinned source evidence. It can edit prose but not the evidence contract.
    editable = editable_payload(reader)
    request = LLMRequest(
        system_prompt=(
            "You are reviewing the final buyer-facing SEO article for the company "
            "ServGo, a B2B system used by service business owners. The reader is "
            "the owner or manager of a psychologist practice or beauty salon. "
            "The article must answer a REAL collected owner search query, not "
            "explain how a patient/consumer books a service. Check all company "
            "features against the VERIFIED facts; a generic industry example "
            "must not be presented as a feature of ServGo. Do not claim automated "
            "room booking, automatic slot buffers, CRM-to-Mail sync, outcomes or "
            "unverified pricing. Check accurate scope, helpful procedures, privacy "
            "caveats, semantic H2/H3, restrained **bold**, *italic*, > callouts, "
            "numbered lists and natural Russian. No fixed word quota. "
            "The output schema contains ONLY editable prose. Verified product "
            "facts and editorial audit notes are immutable, supplied as read-only "
            "source context, and must never be changed by the author. If a "
            "verified fact itself appears mistaken, mark it as critical rather "
            "than silently rewriting it. Preserve actual buyer intent."
        ),
        messages=[LLMMessage(role="user", content=json.dumps({
            "buyer": BUYER, "primary_query": entry["query"],
            "reader_problem": CONTENT[entry["old"]]["reader_problem"],
            # The draft is supplied once through second_opinion's candidate.
            "immutable_approved_fact_renderings": [
                fact.model_dump(mode="json") for fact in reader.fact_renderings
            ],
            "approved_company_facts": verified_facts,
            "unverified_automation_boundaries": [
                "No confirmed automatic reservation of a separately rented therapy room",
                "No confirmed automatic appointment gap/buffer setting",
                "No confirmed CRM-to-Mail synchronization or automated reacquisition",
            ],
        }, ensure_ascii=False))],
        model=settings.llm_model, max_tokens=15000,
        response_schema=EditableOwnerArticle.model_json_schema(),
        metadata={"reasoning_effort": "low", "team_role": "marketing_editor",
                  "task_type": "article_production", "seo_article": True,
                  "company_slug": "servgo"},
    )

    validation_failure = {"category": None}

    def validate_revision(value):
        try:
            revised = apply_editorial_copy(reader, value)
            if not revised.direct_answer or not revised.meta_description:
                raise ValueError("Missing search-answer metadata")
        except Exception as exc:
            # Diagnostic category only; never log untrusted article JSON or
            # provider error bodies from the editorial revision.
            validation_failure["category"] = type(exc).__name__
            raise

    reply = await second_opinion(
        request=request,
        original=LLMResponse(content=json.dumps(editable, ensure_ascii=False),
                             model="owner_editor", stop_reason="end_turn"),
        generate=provider.generate,
        validate_revision=validate_revision,
        review_timeout=REVIEW_TIMEOUT_SECONDS,
        revision_timeout=AUTHOR_REVISION_TIMEOUT_SECONDS,
        max_context_chars=150_000, max_original_chars=80_000,
    )
    review = reply.quality_review or {}
    status = review.get("status")
    if status not in CLEAN_CRITIC_STATUSES:
        # second_opinion converts infrastructure errors into review_unavailable.
        # Expose only error class in logs; never output provider credentials.
        # The article must not be published without independent review.
        reason_class = str(review.get("failure_reason") or "unknown").partition(":")[0]
        print("SERVGO_OWNER_CRITIC_BLOCKED=" + json.dumps({
            "slug": entry["old"], "status": status,
            "failure_class": reason_class,
            "extra_calls": review.get("extra_calls"),
            "attempted_extra_calls": review.get("attempted_extra_calls"),
            "revision_validation": validation_failure["category"],
            "findings": [
                {"severity": f.get("severity"), "problem": str(f.get("problem") or "")[:260]}
                for f in (review.get("findings") or [])[:6] if isinstance(f, dict)
            ],
        }, ensure_ascii=False), flush=True)
        raise ValueError("Critic rejected owner article: " + str(status))
    if any(f.get("severity") == "critical" and not any(
        d.get("id") == f.get("id") and d.get("decision") == "accept"
        for d in review.get("decisions", []))
           for f in review.get("findings", [])):
        raise ValueError("Unresolved critical finding")
    improved = apply_editorial_copy(reader, EditableOwnerArticle.model_validate_json(
        reply.content).model_dump(mode="json"))
    # A critical complaint against a locked verified fact cannot be resolved
    # by only rewriting prose; block and request a new source-backed review.
    for finding in review.get("findings") or []:
        if finding.get("severity") != "critical":
            continue
        evidence = str(finding.get("evidence") or "")
        if any(fact.text and fact.text in evidence for fact in reader.fact_renderings):
            raise ValueError("Critical finding cites immutable verified fact; manual source correction required")
    candidate.reader_article = improved
    # Refresh the public full text if reviewer legitimately edited prose.
    parts = ["# " + improved.title, improved.lead,
             "**Коротко:** " + improved.direct_answer]
    parts.extend("## " + section.heading + "\n\n" + section.body
                 for section in improved.sections)
    parts.append(improved.closing)
    if improved.faq:
        parts.append("## Частые вопросы\n\n" + "\n\n".join(
            "### " + f.question + "\n\n" + f.answer for f in improved.faq))
    candidate.full_article = "\n\n".join(parts)
    candidate.seo.title = improved.title
    return {**entry, "state": "critic_passed", "critic": status,
            "article": candidate.model_dump_json(), "review": review}


async def run(apply: bool) -> list[dict]:
    with SessionLocal() as db:
        product = db.scalar(select(Product).where(Product.slug == "servgo"))
        if product is None:
            raise LookupError("ServGo is not registered")
        old_slugs = tuple(CONTENT)
        rows = list(db.execute(select(Artifact, Publication).join(
            Publication, Publication.artifact_id == Artifact.id).where(
            Publication.product_id == product.id, Publication.channel == "own_site",
            Publication.status == "published",
            Publication.external_reference.in_(old_slugs),
        )))
        new_slugs = {data["new_slug"] for data in CONTENT.values()}
        if not rows:
            already = list(db.scalars(select(Publication).where(
                Publication.product_id == product.id,
                Publication.channel == "own_site",
                Publication.status == "published",
                Publication.external_reference.in_(new_slugs))))
            if {pub.external_reference for pub in already} == new_slugs:
                return [{"state": "already_revised", "new_slug": x} for x in sorted(new_slugs)]
        if len(rows) != 2 or {pub.external_reference for _, pub in rows} != set(old_slugs):
            raise ValueError("Two expected current articles were not found")
        if db.scalar(select(Publication.id).where(
                Publication.product_id == product.id,
                Publication.channel == "own_site",
                Publication.external_reference.in_(new_slugs)).limit(1)):
            raise ValueError("New target slug already in use")
        entries = []
        for artifact, pub in rows:
            old_slug = pub.external_reference
            data = CONTENT[old_slug]
            _query_in_collected_core(db, product.id, data["primary_query"])
            entries.append({
                "id": artifact.id, "product_id": product.id,
                "old": old_slug, "new": data["new_slug"], "query": data["primary_query"],
                "sha": hashlib.sha256(artifact.content.encode()).hexdigest(),
            })

    prepared = [await _review(entry, apply) for entry in entries]
    if not apply:
        return prepared

    # All two critic decisions completed before any write. Single transaction.
    outcomes = []
    with SessionLocal() as db:
        staged = []
        for entry, reviewed in zip(entries, prepared):
            if reviewed["state"] != "critic_passed":
                raise ValueError("Every article needs a clean reviewed draft")
            row = db.scalar(select(Artifact).where(
                Artifact.id == entry["id"]).with_for_update())
            pub = db.scalar(select(Publication).where(
                Publication.artifact_id == entry["id"],
                Publication.status == "published",
                Publication.external_reference == entry["old"]).with_for_update())
            if (row is None or pub is None
                or row.status != ArtifactStatus.PUBLISHED
                or row.product_id != entry["product_id"]
                or hashlib.sha256(row.content.encode()).hexdigest() != entry["sha"]):
                raise ValueError("Production article changed during review")
            reviewed_art = ArticleArtifact.model_validate_json(reviewed["article"])
            errors = static_problems(reviewed_art) + unsupported_servgo_pricing(db, row, reviewed_art)
            if errors:
                raise ValueError("Final article failed checks: " + "; ".join(errors))
            _query_in_collected_core(db, entry["product_id"], reviewed_art.seo_brief.primary_query)
            staged.append((entry, row, pub, reviewed, reviewed_art))
        for entry, row, pub, review, article in staged:
            db.add(Artifact(product_id=entry["product_id"], task_id=row.task_id,
                            kind="seo_article_revision_backup", status=ArtifactStatus.DRAFT,
                            title="Pre-owner-intent revision " + entry["old"], content=row.content))
            now = datetime.now(timezone.utc)
            reviewer = AgentRun(task_id=row.task_id, agent_name="marketing_editor",
                                status="completed", provider=get_settings().llm_provider,
                                model="manual_editor+critic", started_at=now, finished_at=now,
                                input_references=[{"kind": "artifact", "id": str(row.id)}],
                                output_references=[{"kind": "artifact", "id": str(row.id)}])
            db.add(reviewer)
            db.flush()
            db.add(Artifact(product_id=entry["product_id"], task_id=row.task_id,
                            agent_run_id=reviewer.id, kind="seo_final_review",
                            status=ArtifactStatus.DRAFT,
                            title="Buyer-intent editorial review",
                            content=json.dumps(
                                {**review["review"],
                                 "reader_hash": reader_hash(article.reader_article)},
                                ensure_ascii=False)))
            row.content = article.model_dump_json()
            row.title = article.reader_article.title
            row.version = (row.version or 1) + 1
            pub.external_reference = entry["new"]
            outcomes.append({"old_slug": entry["old"], "new_slug": entry["new"],
                             "query": entry["query"], "version": row.version,
                             "critic": review["critic"], "state": "published"})
        db.commit()
    return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    for result in asyncio.run(run(args.apply)):
        print("SERVGO_OWNER_ARTICLE=" + json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
