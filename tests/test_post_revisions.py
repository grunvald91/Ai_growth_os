"""Regression tests for owner-requested immutable post revision workflow."""
import json
from contextlib import contextmanager

import pytest
from sqlalchemy import select

from app.content.revisions import EditRequest, queue_post_edit, run_post_edit, post_history
from app.content.telegram import TelegramContent
from app.content.worker import run_content_once
from app.core.config import Settings
from app.llm.schemas import LLMResponse, LLMUsage
from app.models.domain import Artifact, ArtifactStatus, Product, Task, TaskStatus, Publication


def setup_post(db):
    company = Product(name="Пекарня у дома", slug="neighborhood-bakery")
    db.add(company)
    db.flush()
    generator = Task(
        product_id=company.id, title="CONTENT: test",
        assigned_agent="content_factory", status=TaskStatus.COMPLETED,
    )
    db.add(generator)
    db.flush()
    post = Artifact(
        product_id=company.id, task_id=generator.id,
        kind="channel_text", title="Про хлеб",
        content=json.dumps({
            "origin": "quick_post",
            "channel": "telegram", "title": "Про хлеб",
            "body": "Как выбирать свежий хлеб? Смотрите на состав и условия хранения.",
            "cta": "Напишите нам, чтобы уточнить состав.",
            "publication": "manual_until_channel_connected",
        }, ensure_ascii=False),
        status=ArtifactStatus.AWAITING_APPROVAL,
    )
    db.add(post)
    db.commit()
    return company, post


class FakeRewriteProvider:
    def __init__(self):
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        result = {
            "title": "Хлеб и состав: коротко",
            "body": (
                "Перед покупкой посмотрите на список ингредиентов. "
                "Сравните состав и уточните условия хранения. "
                "Не полагайтесь только на красивое название. "
                "Напишите нам, чтобы уточнить состав."
            ),
            "cta": "Напишите нам, чтобы уточнить состав.",
            "evidence_ids": [],
            "editor_notes": ["Начало сокращено по замечанию владельца."],
        }
        return LLMResponse(
            content=json.dumps(result, ensure_ascii=False), model="fake",
            usage=LLMUsage(input_tokens=70, output_tokens=110),
        )


@pytest.mark.asyncio
async def test_owner_feedback_creates_new_version_and_preserves_old(db, tmp_path):
    company, post = setup_post(db)
    original_text = post.content
    queued = queue_post_edit(db, post.id, "Сократи начало и добавь простой пример")
    assert queued.assigned_agent == "content_editor"
    with pytest.raises(ValueError, match="уже отправлен"):
        queue_post_edit(db, post.id, "Тоже сократи начало")

    req = db.scalar(select(Artifact).where(
        Artifact.task_id == queued.id, Artifact.kind == "content_edit_request",
    ))
    request = EditRequest.model_validate_json(req.content)
    provider = FakeRewriteProvider()
    success = await run_post_edit(
        db, Settings(products_root=tmp_path), request,
        task_id=queued.id, provider=provider,
    )
    assert success is True
    assert len(provider.requests) == 1
    assert db.get(Task, queued.id).status == TaskStatus.COMPLETED
    new = db.scalar(select(Artifact).where(
        Artifact.task_id == queued.id, Artifact.kind == "channel_text",
    ))
    assert new is not None
    assert new.status == ArtifactStatus.AWAITING_APPROVAL
    data = json.loads(new.content)
    assert data["origin"] == "owner_revision"
    assert data["revision_of"] == str(post.id)
    assert data["revision_root_id"] == str(post.id)
    assert data["revision_number"] == 2
    assert data["channel"] == "telegram"
    history = post_history(db, new.id)
    assert [item.id for item in history] == [post.id, new.id]
    assert post.content == original_text
    assert db.get(Artifact, post.id).status == ArtifactStatus.REJECTED
    assert list(db.scalars(select(Publication))) == []
    with pytest.raises(ValueError, match="закрыта"):
        queue_post_edit(db, post.id, "Заново перепиши предыдущую версию")


@pytest.mark.asyncio
async def test_revision_rejects_stale_source_without_creating_new_artifact(db, tmp_path):
    _, post = setup_post(db)
    queued = queue_post_edit(db, post.id, "Перепиши заголовок и сделай короче")
    request_artifact = db.scalar(select(Artifact).where(
        Artifact.task_id == queued.id, Artifact.kind == "content_edit_request"
    ))
    request = EditRequest.model_validate_json(request_artifact.content)
    post.version += 1
    db.commit()
    result = await run_post_edit(
        db, Settings(products_root=tmp_path), request,
        task_id=queued.id, provider=FakeRewriteProvider(),
    )
    assert result is False
    assert db.get(Task, queued.id).status == TaskStatus.FAILED
    assert post.status == ArtifactStatus.AWAITING_APPROVAL
    assert list(db.scalars(select(Artifact).where(
        Artifact.task_id == queued.id, Artifact.kind == "channel_text"
    ))) == []


class FakeBot:
    def __init__(self):
        self.outgoing = []

    async def _send(self, chat, text, reply_markup=None):
        self.outgoing.append((text, reply_markup))


def session_factory(db):
    @contextmanager
    def session():
        yield db
    return session


@pytest.mark.asyncio
async def test_telegram_owner_can_request_edit_and_approve_new_version(db, tmp_path):
    company, post = setup_post(db)
    bot = FakeBot()
    ui = TelegramContent(bot, session_factory(db),
                         lambda: Settings(products_root=tmp_path))
    await ui.callback(123, "content:asset:" + str(post.id))
    assert "Попросить доработку" in str(bot.outgoing[-1][1])
    await ui.callback(123, "content:editpost:" + str(post.id))
    assert ui.post_edit_target == post.id
    consumed = await ui.message({
        "chat": {"id": 123}, "text": "Убери слишком длинное вступление",
    })
    assert consumed and ui.post_edit_target is None
    task = db.scalar(select(Task).where(
        Task.product_id == company.id,
        Task.assigned_agent == "content_editor",
    ))
    assert task is not None and task.status == TaskStatus.QUEUED

    request_artifact = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "content_edit_request"
    ))
    await run_post_edit(
        db, Settings(products_root=tmp_path),
        EditRequest.model_validate_json(request_artifact.content),
        task_id=task.id, provider=FakeRewriteProvider(),
    )
    revised = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.kind == "channel_text"
    ))
    await ui.callback(123, "content:asset:" + str(revised.id))
    assert "Утвердить текст" in str(bot.outgoing[-1][1])
    assert "История версий" in str(bot.outgoing[-1][1])
    await ui.callback(123, "content:history:" + str(revised.id))
    assert "Версия 1" in str(bot.outgoing[-1][1])
    assert "Версия 2" in str(bot.outgoing[-1][1])
    await ui.callback(123, "content:approvepost:" + str(revised.id))
    db.refresh(revised)
    assert revised.status == ArtifactStatus.APPROVED
    assert list(db.scalars(select(Publication))) == []


@pytest.mark.asyncio
async def test_worker_routes_post_edit_requests_without_launching_long_form(monkeypatch, db, tmp_path):
    company, post = setup_post(db)
    task = queue_post_edit(db, post.id, "Добавь более короткий заголовок")
    calls = []

    async def fake_rewriter(db_arg, settings, request, *, task_id, provider=None):
        calls.append((request.source_id, task_id))
        db_arg.get(Task, task_id).status = TaskStatus.COMPLETED
        db_arg.commit()
        return True

    monkeypatch.setattr("app.content.worker.run_post_edit", fake_rewriter)
    result = await run_content_once(session_factory(db), Settings(products_root=tmp_path))
    assert result == 1
    assert calls == [(post.id, task.id)]


@pytest.mark.asyncio
async def test_old_post_cannot_be_approved_while_owner_revision_is_queued(db, tmp_path):
    _, post = setup_post(db)
    pending = queue_post_edit(db, post.id, "Поменяй первый абзац и сделай проще")
    bot = FakeBot()
    ui = TelegramContent(bot, session_factory(db),
                         lambda: Settings(products_root=tmp_path))
    await ui.callback(123, "content:approvepost:" + str(post.id))
    db.refresh(post)
    assert post.status == ArtifactStatus.AWAITING_APPROVAL
    assert "сейчас дорабатывается" in bot.outgoing[-1][0]
    assert db.get(Task, pending.id).status == TaskStatus.QUEUED


async def _queued_edit(db, feedback="Сделай заход острее и добавь пример расчёта"):
    company, post = setup_post(db)
    queued = queue_post_edit(db, post.id, feedback)
    req = db.scalar(select(Artifact).where(
        Artifact.task_id == queued.id, Artifact.kind == "content_edit_request",
    ))
    return company, queued, EditRequest.model_validate_json(req.content)


def _revised(task_id, db):
    row = db.scalar(select(Artifact).where(Artifact.task_id == task_id, Artifact.kind == "channel_text"))
    return json.loads(row.content) if row else None


@pytest.mark.asyncio
async def test_revision_uses_quick_post_rules_and_owner_feedback_first(db, tmp_path):
    _, queued, request = await _queued_edit(db)
    provider = FakeRewriteProvider()
    assert await run_post_edit(db, Settings(_env_file=None, products_root=tmp_path), request,
                               task_id=queued.id, provider=provider)
    system = provider.requests[0].system_prompt
    assert system.startswith("Ты — сильный маркетолог")  # shared quick post rules
    assert "выполни ВСЕ замечания из owner_feedback" in system
    assert provider.requests[0].metadata["task_type"] == "quick_post"  # marketing critic profile


@pytest.mark.asyncio
async def test_revision_appends_paraphrased_cta_instead_of_failing(db, tmp_path):
    _, queued, request = await _queued_edit(db)

    class Provider(FakeRewriteProvider):
        async def generate(self, request):
            reply = await super().generate(request)
            data = json.loads(reply.content)
            data["body"] = data["body"].replace("Напишите нам, чтобы уточнить состав.", "Пишите — подскажем.")
            return reply.model_copy(update={"content": json.dumps(data, ensure_ascii=False)})

    assert await run_post_edit(db, Settings(_env_file=None, products_root=tmp_path), request,
                               task_id=queued.id, provider=Provider())
    data = _revised(queued.id, db)
    assert data["body"].endswith("Напишите нам, чтобы уточнить состав.")


@pytest.mark.asyncio
async def test_rejected_revision_is_repaired_once_with_the_validation_problem(db, tmp_path):
    _, queued, request = await _queued_edit(db)

    class Provider(FakeRewriteProvider):
        async def generate(self, request):
            reply = await super().generate(request)
            if len(self.requests) == 1:
                data = json.loads(reply.content)
                data["evidence_ids"] = ["invented-source"]
                return reply.model_copy(update={"content": json.dumps(data, ensure_ascii=False)})
            return reply

    provider = Provider()
    assert await run_post_edit(db, Settings(_env_file=None, products_root=tmp_path), request,
                               task_id=queued.id, provider=provider)
    assert len(provider.requests) == 2
    assert "rejected by validation" in provider.requests[1].system_prompt
    assert _revised(queued.id, db)["evidence_ids"] == []


@pytest.mark.asyncio
async def test_revision_with_enabled_critic_records_review_and_every_call(db, tmp_path):
    from app.models.domain import CostRecord
    company, queued, request = await _queued_edit(db)
    base = FakeRewriteProvider()

    class Provider:
        def __init__(self):
            self.calls = []

        async def generate(self, request):
            self.calls.append(request)
            if request.metadata.get("task_type") == "quality_review":
                return LLMResponse(content=json.dumps({"findings": []}), model="fake",
                                   usage=LLMUsage(input_tokens=40, output_tokens=5), stop_reason="end_turn")
            return await base.generate(request)

    provider = Provider()
    settings = Settings(_env_file=None, products_root=tmp_path, agent_critic_enabled=True)
    assert await run_post_edit(db, settings, request, task_id=queued.id, provider=provider)
    review = provider.calls[1]
    assert review.metadata["task_type"] == "quality_review"
    assert "критик-редактор" in review.system_prompt  # marketing critic, not the fact critic
    assert list(db.scalars(select(Artifact).where(
        Artifact.task_id == queued.id, Artifact.kind == "agent_quality_review")))
    stages = [c.details["llm_stage"] for c in db.scalars(
        select(CostRecord).where(CostRecord.product_id == company.id))]
    assert sorted(stages) == ["author", "critic"]
