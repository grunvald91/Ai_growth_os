"""Reviewable, append-only post revisions from owner feedback.

Only the already owner-authenticated Telegram control bot should enqueue edits.
This module deliberately has no public web mutation route.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.prompts import register as register_prompt, text as prompt_text
from app.content.quick_post import CHANNEL_GUIDANCE, _decode, append_missing_cta, _validation_summary
from app.llm.audit import persist_critic_audit, persist_observed_llm_call
from app.content.quality import post_quality_warnings, factual_claim_warnings
from app.llm.schemas import LLMMessage, LLMRequest
from app.models.domain import AgentRun, Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus
from app.services.artifacts import ArtifactService
from app.services.tasks import TaskService
from app.workflows.article_production import product_facts
from app.workflows.context import load_context



POST_REVISION_PROMPT = (
    "Ты — редактор, который переписывает готовый пост по замечаниям владельца компании. "
    "ГЛАВНОЕ: выполни ВСЕ замечания из owner_feedback. Это задание владельца, а не пожелание. "
    "Если замечание противоречит правилам ниже, выполни его в пределах правил: никаких выдуманных фактов, "
    "цен, отзывов и гарантий; оставшийся вопрос запиши в editor_notes. "
    "То, что в посте уже работает и чего замечания не касаются, — сохрани: сильный заход, конкретные советы, "
    "удачные формулировки. Не переписывай пост с нуля без просьбы владельца. "
    "Итоговый пост должен соответствовать тем же правилам, что и новый пост: польза для читателя прежде "
    "рекламы (конкретные приёмы, шаги, метрики или расчёты, которые читатель применит сам), уверенный простой "
    "тон без оговорок и канцелярита, первая строка останавливает ленту, абзац о компании — 1–2 фразы о выгоде "
    "для читателя, в конце ровно один призыв к действию с понятным действием. "
    "Поле cta должно дословно стоять в конце body. Учитывай channel_guidance; для Telegram — 800–1500 "
    "символов, если владелец не просит другую длину. "
    "Перед ответом сверь: каждое замечание выполнено, количество пунктов совпадает с обещанным в тексте, "
    "числа и заголовок согласованы. В editor_notes коротко перечисли, что изменено по каждому замечанию. "
    "Не упоминай ServGo, если этого нет в исходном посте или в замечаниях. "
    "Не утверждай, что что-то опубликовал или проверял в интернете. Верни только JSON по схеме."
)
register_prompt('revision.post', group='Правка поста по замечаниям', title='Редактор: правка поста по вашим замечаниям',
                description='Как переписывать пост по комментарию владельца: выполнить все замечания, сохранить сильное, '
                            'не выдумывать факты. Общие правила быстрого поста применяются тоже.',
                default=POST_REVISION_PROMPT)

class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EditRequest(StrictRecord):
    product_id: UUID
    source_id: UUID
    source_version: int = Field(ge=1)
    source_digest: str = Field(min_length=64, max_length=64)
    feedback: str = Field(min_length=8, max_length=1800)


class RevisedPost(StrictRecord):
    title: str = Field(min_length=5, max_length=960)
    body: str = Field(min_length=90, max_length=14000)
    cta: str = Field(min_length=3, max_length=1040)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    editor_notes: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def cta_in_body(cls, data):
        return append_missing_cta(data)


def source_fingerprint(source: Artifact) -> str:
    return hashlib.sha256(source.content.encode("utf-8")).hexdigest()


def post_history(db: Session, post_id: UUID) -> list[Artifact]:
    """Return prior immutable versions without scanning unrelated client companies.

    Revision linkage is kept in artifact JSON until a dedicated version table
    is introduced. The root post remains queryable even when superseded.
    """
    source = db.get(Artifact, post_id)
    if source is None or source.kind not in {"channel_text", "article", "creative_brief", "video_script"}:
        raise LookupError("Пост не найден")
    try:
        value = json.loads(source.content)
        root_id = UUID(value.get("revision_root_id") or str(source.id))
    except (ValueError, TypeError, AttributeError):
        raise LookupError("Связи версий повреждены") from None
    root = db.get(Artifact, root_id)
    if root is None or root.kind != source.kind or root.product_id != source.product_id:
        raise LookupError("Первая версия поста не найдена")
    pattern = '%"revision_root_id": "' + str(root.id) + '"%'
    revisions = list(db.scalars(
        select(Artifact).where(
            Artifact.product_id == root.product_id,
            Artifact.kind == source.kind,
            Artifact.content.like(pattern),
        ).order_by(Artifact.created_at, Artifact.id).limit(250)
    ))
    return [root, *[row for row in revisions if row.id != root.id]]


def queue_post_edit(db: Session, post_id: UUID, feedback: str):
    """Lock owner company, then source post. Reject stale and duplicate requests."""
    feedback = (feedback or "").strip()
    if not 8 <= len(feedback) <= 1800:
        raise ValueError("Опиши, что изменить в тексте, от 8 до 1800 символов.")
    source = db.get(Artifact, post_id)
    if source is None or source.kind != "channel_text":
        raise LookupError("Пост не найден")
    company = db.scalar(
        select(Product).where(Product.id == source.product_id).with_for_update()
    )
    if company is None:
        raise LookupError("Компания не найдена")
    source = db.scalar(
        select(Artifact).where(
            Artifact.id == post_id, Artifact.product_id == company.id,
            Artifact.kind == "channel_text"
        ).with_for_update().execution_options(populate_existing=True)
    )
    if source.status not in {ArtifactStatus.AWAITING_APPROVAL, ArtifactStatus.APPROVED}:
        raise ValueError("Эта версия поста уже закрыта для редактирования")
    pending_publication = db.scalar(select(Publication.id).where(
        Publication.artifact_id == post_id, Publication.status.in_(["scheduled", "sending", "delivery_unknown"])))
    if pending_publication:
        raise ValueError("Сначала отмените отправку или проверьте её результат, затем доработайте пост")
    try:
        value = json.loads(source.content)
    except (ValueError, TypeError):
        raise ValueError("Исходный материал повреждён") from None
    if not value.get("body") or value.get("channel") not in CHANNEL_GUIDANCE:
        raise ValueError("Материал не поддерживает редактуру поста")
    key = "EDIT: " + str(source.id)
    duplicate = TaskService(db).find_active_duplicate(
        product_id=company.id, title=key, assigned_agent="content_editor"
    )
    if duplicate:
        raise ValueError("Этот пост уже отправлен на доработку")
    request = EditRequest(
        product_id=company.id, source_id=source.id, source_version=source.version,
        source_digest=source_fingerprint(source), feedback=feedback,
    )
    task = TaskService(db).create(
        product_id=company.id, title=key, assigned_agent="content_editor",
        status=TaskStatus.QUEUED, priority=100,
        description="Owner-requested content rewrite; no external publication",
    )
    ArtifactService(db).create(
        product_id=company.id, task_id=task.id,
        kind="content_edit_request", title="Замечания владельца",
        content=request.model_dump_json(), status="draft",
    )
    db.commit()
    return task


async def run_post_edit(db: Session, settings, request: EditRequest, *, task_id: UUID, provider=None):
    from app.core.composition import SAFE_LLM_METADATA_FIELDS, build_llm_provider
    from app.llm.service import LLMService
    from app.privacy import PrivacyGateway

    privacy = PrivacyGateway(allowed_context_fields=SAFE_LLM_METADATA_FIELDS)
    llm = LLMService(
        privacy_gateway=privacy, provider=provider or build_llm_provider(settings),
        critic_enabled=settings.agent_critic_enabled,
    )
    product = db.get(Product, request.product_id)
    task = db.get(Task, task_id)
    source = db.get(Artifact, request.source_id)
    if product is None or task is None or task.product_id != request.product_id:
        raise LookupError("Invalid revision task")
    if source is None or source.product_id != request.product_id or source.kind != "channel_text":
        raise LookupError("Invalid revision source")
    claimed = db.execute(
        update(Task).where(Task.id == task.id, Task.status == TaskStatus.QUEUED)
        .values(status=TaskStatus.RUNNING)
    )
    if claimed.rowcount != 1:
        db.rollback()
        return False
    run_id = None
    try:
        run = AgentRun(
            task_id=task.id, agent_name="post_rewriter",
            provider=settings.llm_provider, model=settings.llm_model,
            status="running", started_at=datetime.now(timezone.utc),
            input_references=[{"kind": "artifact", "id": str(source.id)}],
        )
        db.add(run)
        db.flush()
        run_id = run.id
        db.commit()
        if source.version != request.source_version or source_fingerprint(source) != request.source_digest:
            raise ValueError("Source changed since edit request")
        if source.status not in {ArtifactStatus.AWAITING_APPROVAL, ArtifactStatus.APPROVED}:
            raise ValueError("Source no longer editable")
        old = json.loads(source.content)
        channel = old.get("channel")
        if channel not in CHANNEL_GUIDANCE:
            raise ValueError("Unsupported channel")

        context = load_context(db, product, settings.products_root)
        stable = privacy.sanitize_text(context["stable_context"])
        facts = product_facts(stable)
        payload = {
            "company_name": privacy.sanitize_text(product.name),
            "original_title": privacy.sanitize_text(old.get("title", source.title)),
            "original_body": privacy.sanitize_text(old["body"][:11900]),
            "original_cta": privacy.sanitize_text(old.get("cta") or ""),
            "owner_feedback": privacy.sanitize_text(request.feedback),
            "channel": channel,
            "channel_guidance": CHANNEL_GUIDANCE[channel],
            "company_context": stable[:10500],
            "verified_facts": facts,
        }
        system = prompt_text("quick_post.common") + prompt_text("revision.post")
        metadata = {
            "team_role": "post_rewriter", "task_type": "quick_post",
            "reasoning_effort": "low", "provider_timeout_seconds": 100,
        }

        def validate_candidate(data):
            parsed = RevisedPost.model_validate(data)
            if channel == "telegram":
                from app.content.telegram_publishing import validate_telegram_text
                validate_telegram_text(parsed.body)
            if any(source_id not in facts for source_id in parsed.evidence_ids):
                raise ValueError("Unverified company fact in revision")
            if (product.slug != "servgo"
                    and "servgo" not in request.feedback.casefold()
                    and "servgo" not in old["body"].casefold()
                    and "servgo" in (parsed.title + " " + parsed.body).casefold()):
                raise ValueError("Unexpected third-party brand in revision")
            return parsed

        def record_model_call(stage, observed):
            persist_observed_llm_call(
                db, product_id=product.id, task_id=task.id, run_id=run_id,
                response=observed, stage=stage, task_type="post_revision",
                provider=settings.llm_provider,
            )

        def record_review_progress(snapshot):
            persist_critic_audit(db, product_id=product.id, task_id=task.id, run_id=run_id, response=snapshot)
            db.commit()

        reply = await asyncio.wait_for(
            llm.generate(LLMRequest(
                system_prompt=system,
                messages=[LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False))],
                response_schema=RevisedPost.model_json_schema(),
                model=settings.llm_model, max_tokens=7500, metadata=metadata,
            ), validate_revision=validate_candidate,
                on_observed=record_model_call, on_review_progress=record_review_progress),
            timeout=210 + llm.extra_timeout_seconds,
        )
        persist_critic_audit(db, product_id=product.id, task_id=task.id, run_id=run_id, response=reply)
        db.commit()
        try:
            if reply.stop_reason not in (None, "end_turn", "stop_sequence"):
                raise ValueError("Incomplete revision output")
            draft = validate_candidate(_decode(RevisedPost, reply.content).model_dump(mode="json"))
        except (ValueError, TypeError) as exc:
            # One repair attempt with the exact problem; no second critic round.
            problem = _validation_summary(exc)
            retry = LLMService(privacy_gateway=privacy, provider=llm._provider, critic_enabled=False)
            reply = await asyncio.wait_for(retry.generate(LLMRequest(
                system_prompt=system + " Your previous answer was rejected by validation: " + problem
                + ". Fix exactly this and return the complete JSON again.",
                messages=[LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False))],
                response_schema=RevisedPost.model_json_schema(),
                model=settings.llm_model, max_tokens=7500, metadata=metadata,
            ), on_observed=record_model_call), timeout=210)
            if reply.stop_reason not in (None, "end_turn", "stop_sequence"):
                raise ValueError("Incomplete revision output") from exc
            draft = validate_candidate(_decode(RevisedPost, reply.content).model_dump(mode="json"))
        db.scalar(select(Product).where(Product.id == product.id).with_for_update())
        source = db.scalar(
            select(Artifact).where(
                Artifact.id == request.source_id,
                Artifact.product_id == product.id,
                Artifact.kind == "channel_text"
            ).with_for_update().execution_options(populate_existing=True)
        )
        if (source is None or source.version != request.source_version
                or source_fingerprint(source) != request.source_digest
                or source.status not in {
                    ArtifactStatus.AWAITING_APPROVAL, ArtifactStatus.APPROVED
                }):
            raise ValueError("Revision source has changed; retry against latest material")
        old = json.loads(source.content)
        root_id = old.get("revision_root_id") or str(source.id)
        warnings = post_quality_warnings(draft.title, draft.body, channel)
        facts_warnings = factual_claim_warnings(draft.title + " " + draft.body, facts)
        warnings += facts_warnings
        new_value = {
            "channel": old["channel"],
            "title": draft.title,
            "body": draft.body,
            "cta": draft.cta,
            "editorial_checks": draft.editor_notes,
            "quality_warnings": warnings,
            "fact_check_required": bool(facts_warnings),
            "quality_review": "heuristic_only_requires_owner_review",
            "evidence_ids": draft.evidence_ids,
            "origin": "owner_revision",
            "revision_of": str(source.id),
            "revision_root_id": root_id,
            "revision_number": int(old.get("revision_number", 1)) + 1,
            "publication": "manual_until_channel_connected",
        }
        revised = ArtifactService(db).create(
            product_id=product.id, task_id=task.id, agent_run_id=run_id,
            kind="channel_text", title=draft.title,
            content=json.dumps(new_value, ensure_ascii=False),
            status="awaiting_approval", version=source.version + 1,
        )
        # Preserve old immutable text for audit; it is no longer the current draft.
        source.status = ArtifactStatus.REJECTED
        run = db.get(AgentRun, run_id)
        run.status = "completed"
        run.finished_at = datetime.now(timezone.utc)
        run.output_references = [{"kind": "artifact", "id": str(revised.id)}]
        db.get(Task, task.id).status = TaskStatus.COMPLETED
        db.commit()
        return True
    except (Exception, asyncio.CancelledError) as exc:
        db.rollback()
        if run_id:
            run = db.get(AgentRun, run_id)
            if run is not None:
                run.status = "failed"
                run.error = ("post_revision_failed:" + type(exc).__name__ + ":" + _validation_summary(exc))[:1000]
                run.finished_at = datetime.now(timezone.utc)
        old_task = db.get(Task, task.id)
        if old_task:
            old_task.status = TaskStatus.FAILED
        db.commit()
        if isinstance(exc, asyncio.CancelledError):
            raise
        return False
