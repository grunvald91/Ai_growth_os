"""A saved response is reusable only after model deferral of the same work."""
from sqlalchemy import select

from app.llm.deferred_checkpoint import DeferredCallCheckpoint, KIND
from app.llm.schemas import LLMMessage, LLMRequest, LLMResponse
from app.models.domain import AgentRun, Artifact, Task, TaskStatus
from app.privacy import PrivacyGateway
from test_seo_factory import env  # noqa: F401


def test_checkpoint_is_scoped_to_exact_request_and_deferred_run(env):
    factory, _, product_id = env
    with factory() as db:
        task = Task(product_id=product_id, title="SEO-ARTICLE:test:a001",
                    assigned_agent="content_factory", status=TaskStatus.QUEUED)
        db.add(task)
        db.flush()
        first = AgentRun(task_id=task.id, agent_name="marketing_editor", status="running")
        second = AgentRun(task_id=task.id, agent_name="marketing_editor", status="running")
        db.add_all([first, second])
        db.commit()

        def checkpoint(run, scope="article:marketing_editor"):
            return DeferredCallCheckpoint(db, product_id=product_id, task_id=task.id,
                run_id=run.id, agent_name="marketing_editor", scope=scope,
                privacy=PrivacyGateway(), provider="fake")

        request = LLMRequest(model="model-a", messages=[LLMMessage(role="user", content="draft")])
        reply = LLMResponse(content='{"title":"article"}', model="model-a", stop_reason="end_turn")
        checkpoint(first).save("author", request, reply)
        assert checkpoint(second).load("author", request) is None
        first.status, first.error = "failed", "invalid_article_output:marketing_editor"
        db.commit()
        assert checkpoint(second).load("author", request) is None
        first.error = "seo_model_wait"
        db.commit()
        assert checkpoint(second).load("author", request) == reply
        assert checkpoint(second).load("critic", request) is None
        assert checkpoint(second, scope="different").load("author", request) is None
        assert checkpoint(second).load("author", request.model_copy(update={"model": "model-b"})) is None
        changed = request.model_copy(update={"messages": [LLMMessage(role="user", content="new draft")]})
        assert checkpoint(second).load("author", changed) is None
        checkpoint(second).save("author", changed, reply.model_copy(update={"content": "password: secret"}))
        checkpoint(second).save("author", changed, reply.model_copy(update={"stop_reason": "length"}))
        assert len(list(db.scalars(select(Artifact).where(Artifact.kind == KIND)))) == 1
