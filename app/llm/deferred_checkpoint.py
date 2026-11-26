"""Request-bound checkpoints for model-deferred SEO work.

No request bodies, credentials or prompts are persisted by this helper.
Only complete, privacy-checked responses from an explicitly deferred run
may be replayed. Ordinary failures are not replayed.
"""
import hashlib
import json

from sqlalchemy import select

from app.llm.schemas import LLMResponse
from app.models.domain import AgentRun, Artifact

KIND = "seo_model_call_checkpoint"
WAIT_ERRORS = ("seo_model_wait", "seo_held_repair_model_wait")
STAGES = {"author", "critic", "revision"}
MAX_CONTENT = 150000


class DeferredCallCheckpoint:
    def __init__(self, db, *, product_id, task_id, run_id, agent_name,
                 scope, privacy, provider):
        self.db = db
        self.product_id, self.task_id, self.run_id = product_id, task_id, run_id
        self.agent_name, self.scope = agent_name, scope
        self.privacy, self.provider = privacy, provider

    def _key(self, stage, request):
        return hashlib.sha256(json.dumps({
            "version": 1, "scope": self.scope, "provider": self.provider,
            "stage": stage, "request": request.model_dump(mode="json"),
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def _usable(self, response):
        return (response.stop_reason in (None, "end_turn", "stop_sequence")
                and 0 < len(response.content) <= MAX_CONTENT
                and response.quality_review is None
                and self.privacy.sanitize_text(response.content) == response.content
                and self.privacy.sanitize_text(response.model) == response.model)

    def load(self, stage, request):
        if stage not in STAGES:
            return None
        key = self._key(stage, request)
        # The artifact and its source run must agree on tenant, task and role.
        # Do not replay a successful or terminally failed editorial attempt.
        rows = self.db.scalars(select(Artifact).join(
            AgentRun, AgentRun.id == Artifact.agent_run_id).where(
            Artifact.product_id == self.product_id, Artifact.task_id == self.task_id,
            Artifact.kind == KIND, Artifact.title == key,
            AgentRun.task_id == self.task_id, AgentRun.agent_name == self.agent_name,
            AgentRun.id != self.run_id, AgentRun.status == "failed",
            AgentRun.error.in_(WAIT_ERRORS)
        ).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(5))
        for row in rows:
            try:
                response = LLMResponse.model_validate_json(row.content)
                if self._usable(response):
                    return response
            except (ValueError, TypeError):
                continue
        return None

    def save(self, stage, request, response):
        if stage not in STAGES or not self._usable(response):
            return
        self.db.add(Artifact(
            product_id=self.product_id, task_id=self.task_id,
            agent_run_id=self.run_id, kind=KIND, title=self._key(stage, request),
            content=response.model_dump_json(), status="draft",
        ))
        self.db.commit()
