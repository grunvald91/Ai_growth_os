"""Durable Task/Artifact orchestration; model calls always cross LLMService."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.competitive_intelligence.contracts import ResearchPlan, RunInput, SynthesisDraft
from app.competitive_intelligence.evidence_store import normalize
from app.competitive_intelligence.jeff_router import triage
from app.competitive_intelligence.synthesis import PLAN_PROMPT, SYNTHESIS_PROMPT, validate_report
from app.llm.audit import critic_cost_details, persist_critic_audit
from app.llm.schemas import LLMMessage, LLMRequest
from app.models.domain import AgentRun, Artifact, CostRecord, Product, Task, TaskStatus
from app.privacy import PrivacyGateway
from app.services.artifacts import ArtifactService
from app.services.tasks import TaskService
from app.workflows.context import load_context
from app.workflows.research_contracts import ResearchSourceBundle
from app.workflows.research_sources import load_source_bundles

AGENT = "competitive_intelligence"
RUN_KIND = "competitive_intelligence_run"


class IntelligenceService:
    def __init__(self, db: Session, *, llm=None, jeff=None, model="", jeff_model="",
                 provider_name="", products_root=Path("products"), threshold=.8):
        self.db = db
        self.llm = llm
        self.jeff = jeff
        self.model = model
        self.jeff_model = jeff_model
        self.provider_name = provider_name
        self.products_root = products_root
        self.threshold = threshold
        self.privacy = PrivacyGateway()

    def _clean(self, value):
        if isinstance(value, str):
            return self.privacy.sanitize_text(value)
        if isinstance(value, list):
            return [self._clean(item) for item in value]
        if isinstance(value, dict):
            return {key: self._clean(item) for key, item in value.items()}
        return value

    def start(self, request: RunInput, *, parent_id: UUID | None = None, frozen_bundles=None) -> dict:
        request = RunInput.model_validate(self._clean(request.model_dump(mode="json")))
        product = self.db.get(Product, request.product_id)
        if product is None:
            raise LookupError("Product not found")
        if parent_id is not None:
            parent = self.db.get(Task, parent_id)
            if parent is None or parent.product_id != product.id:
                raise LookupError("Parent task not found for this product")
        task = TaskService(self.db).create(product_id=product.id, parent_id=parent_id,
            title=f"Бизнес-разведка: {request.brief[:240]}", description=request.brief,
            assigned_agent=AGENT)
        # Freeze source input at enqueue time, so a later ingestion cannot change the run.
        bundles = (frozen_bundles if frozen_bundles is not None else
                   [request.source_bundle] if request.source_bundle else load_source_bundles(self.db, product.id))
        if request.source_bundle:
            ArtifactService(self.db).create(product_id=product.id, task_id=task.id,
                kind="research_source_bundle", title=request.source_bundle.label,
                content=request.source_bundle.model_dump_json())
        state = {"id": str(task.id), "product_id": str(product.id), "status": "queued",
                 "stage": "queued", "request": request.model_dump(mode="json"),
                 "source_bundles": [bundle.model_dump(mode="json") for bundle in bundles],
                 "events": [], "counts": {"found": 0, "retained": 0, "excluded": 0,
                                          "escalated": 0, "jeff_processed": 0},
                 "report_artifact_id": None, "evidence_artifact_id": None, "error": None}
        ArtifactService(self.db).create(product_id=product.id, task_id=task.id,
            kind=RUN_KIND, title=task.title, content=json.dumps(self._clean(state), ensure_ascii=False))
        self.db.commit()
        return self.get(task.id)

    def _state(self, run_id):
        artifact = self.db.scalar(select(Artifact).where(Artifact.task_id == run_id,
            Artifact.kind == RUN_KIND).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(1))
        if artifact is None:
            raise LookupError("Intelligence run not found")
        return artifact, json.loads(artifact.content)

    def get(self, run_id: UUID) -> dict:
        _, state = self._state(run_id)
        return {key: value for key, value in state.items() if key != "source_bundles"}

    def list_runs(self, product_id: UUID):
        result = []
        for row in self.db.scalars(select(Artifact).where(Artifact.product_id == product_id,
            Artifact.kind == RUN_KIND).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(20)):
            state = json.loads(row.content)
            result.append({**{key: value for key, value in state.items()
                if key not in {"source_bundles", "request", "events"}},
                "title": state["request"]["brief"][:160], "created_at": row.created_at.isoformat()})
        return result

    def evidence(self, run_id: UUID):
        _, state = self._state(run_id)
        if not state["evidence_artifact_id"]:
            return []
        return json.loads(self.db.get(Artifact, UUID(state["evidence_artifact_id"])).content)["evidence"]

    def report(self, run_id: UUID):
        _, state = self._state(run_id)
        if not state["report_artifact_id"]:
            raise ValueError("Report is not ready")
        return json.loads(self.db.get(Artifact, UUID(state["report_artifact_id"])).content)

    def _save(self, artifact, state, stage, *, status=None):
        state["stage"] = stage
        if status:
            state["status"] = status
        state["events"].append({"id": len(state["events"]) + 1, "stage": stage,
            "status": state["status"], "at": datetime.now(timezone.utc).isoformat(),
            "counts": dict(state["counts"])})
        ArtifactService(self.db).update(artifact.id, content=json.dumps(self._clean(state), ensure_ascii=False))
        self.db.commit()

    async def execute(self, run_id: UUID):
        claimed = self.db.execute(update(Task).where(Task.id == run_id,
            Task.assigned_agent == AGENT, Task.status == TaskStatus.QUEUED).values(status=TaskStatus.RUNNING))
        if claimed.rowcount != 1:
            self.db.rollback()
            return False
        self.db.commit()
        artifact, state = self._state(run_id)
        try:
            await asyncio.wait_for(self._execute(artifact, state), timeout=1200)
        except (Exception, asyncio.CancelledError) as exc:
            self.db.rollback()
            artifact, state = self._state(run_id)
            self.db.get(Task, run_id).status = TaskStatus.FAILED
            state["error"] = "research_interrupted" if isinstance(exc, asyncio.CancelledError) else "research_failed"
            self._save(artifact, state, "failed", status="failed")
            if isinstance(exc, asyncio.CancelledError):
                raise
        return True

    async def _execute(self, artifact, state):
        request = RunInput.model_validate(state["request"])
        product = self.db.get(Product, request.product_id)
        self._active_state = state
        self._save(artifact, state, "planning", status="running")
        context = load_context(self.db, product, self.products_root)
        plan = await self._call(ResearchPlan, PLAN_PROMPT,
            {"request": request.model_dump(mode="json", exclude={"source_bundle"}),
             "company_context": context["stable_context"]}, "planning")
        if plan.mode != request.mode:
            raise ValueError("Planner changed requested mode")
        state["plan"] = plan.model_dump(mode="json")
        self._save(artifact, state, "sources")
        evidence = normalize([ResearchSourceBundle.model_validate(bundle) for bundle in state["source_bundles"]],
                             request, datetime.now(timezone.utc))
        state["counts"]["found"] = len(evidence)
        limit = {"quick": 10, "standard": 25, "deep": 50}[request.depth]
        candidates = 0
        for item in evidence:
            if item.exclusion_reason is None:
                candidates += 1
                if candidates > limit:
                    item.exclusion_reason = "depth_limit"
        self._save(artifact, state, "triage")
        routing = await triage(evidence, request.brief, self._call, self.threshold, self.jeff is not None)
        state["counts"].update(routing)
        retained = [item for item in evidence if item.disposition == "retained"]
        state["counts"].update(retained=len(retained), excluded=len(evidence) - len(retained))
        evidence_artifact = ArtifactService(self.db).create(product_id=product.id,
            task_id=UUID(state["id"]), kind="competitive_evidence_bundle", title="Источники бизнес-разведки",
            content=json.dumps({"evidence": [item.model_dump(mode="json") for item in evidence]}, ensure_ascii=False))
        state["evidence_artifact_id"] = str(evidence_artifact.id)
        self._save(artifact, state, "analysis")
        draft = SynthesisDraft()
        if retained:
            draft = await self._call(SynthesisDraft, SYNTHESIS_PROMPT,
                {"brief": request.brief, "mode": request.mode, "region": request.region,
                 "plan": state["plan"], "company_context": context["stable_context"],
                 "evidence": [item.model_dump(mode="json") for item in retained]}, "synthesis")
        self._save(artifact, state, "validation")
        routing = {**routing, "threshold": self.threshold, "jeff_available": self.jeff is not None,
                   "depth_limit": limit, "counts": state["counts"]}
        report = validate_report(draft, evidence, request, routing)
        if not retained:
            report.evidence_gaps.extend(plan.source_requests)
        report_artifact = ArtifactService(self.db).create(product_id=product.id,
            task_id=UUID(state["id"]), kind="competitive_intelligence_report", title="Отчёт бизнес-разведки",
            content=report.model_dump_json())
        state["report_artifact_id"] = str(report_artifact.id)
        for view, kind in (("pricing", "pricing_snapshot"), ("opportunities", "market_opportunity_report")):
            items = [item.model_dump(mode="json") for item in report.insights if item.view == view]
            if items:
                ArtifactService(self.db).create(product_id=product.id, task_id=UUID(state["id"]),
                    kind=kind, title=f"Бизнес-разведка — {view}",
                    content=json.dumps({"report_artifact_id": str(report_artifact.id), "insights": items}))
        self.db.get(Task, UUID(state["id"])).status = TaskStatus.COMPLETED
        self._save(artifact, state, "ready", status=report.status)

    async def _call(self, schema, prompt, payload, stage, use_jeff=False):
        llm = self.jeff if use_jeff else self.llm
        if llm is None:
            raise ValueError("Research model is not configured")
        state = self._active_state
        run = AgentRun(task_id=UUID(state["id"]), agent_name=f"{AGENT}:{stage}",
            provider="jeff" if use_jeff else self.provider_name,
            model=self.jeff_model if use_jeff else self.model, status="running",
            started_at=datetime.now(timezone.utc),
            input_references=[{"kind": "task", "id": state["id"]},
                *([{"kind": "artifact", "id": state["evidence_artifact_id"]}]
                  if state["evidence_artifact_id"] else [])])
        self.db.add(run)
        self.db.commit()
        response = None
        try:
            response = await asyncio.wait_for(llm.generate(LLMRequest(
                system_prompt=prompt, messages=[LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False))],
                model=self.jeff_model if use_jeff else self.model,
                max_tokens=1500 if "triage" in stage else 4500,
                response_schema=schema.model_json_schema(),
                metadata={"task_type": stage, "team_role": AGENT, "reasoning_effort": "low"})),
                timeout=(30 if use_jeff else 120) + llm.extra_timeout_seconds)
            if len(response.content) > 100_000 or response.stop_reason not in (None, "end_turn", "stop_sequence"):
                raise ValueError("Incomplete model response")
            parsed = schema.model_validate(self._clean(json.loads(response.content)))
            if "triage" in stage:
                expected = {item["evidence_id"] for item in payload["items"]}
                actual = [label.evidence_id for label in parsed.labels]
                if len(actual) != len(set(actual)) or set(actual) != expected:
                    raise ValueError("Incomplete or invalid decision batch")
            stage_artifact = ArtifactService(self.db).create(product_id=UUID(state["product_id"]),
                task_id=UUID(state["id"]), agent_run_id=run.id, kind="competitive_intelligence_stage",
                title=f"Бизнес-разведка — {stage}", content=parsed.model_dump_json())
            run.output_references = [{"kind": "artifact", "id": str(stage_artifact.id)}]
            run.model = response.model
            run.status = "completed"
            return parsed
        except (Exception, asyncio.CancelledError):
            run.status = "failed"
            run.error = "model_stage_failed"
            raise
        finally:
            run.finished_at = datetime.now(timezone.utc)
            if response is not None and response.usage is not None:
                self.db.add(CostRecord(product_id=UUID(state["product_id"]), agent_run_id=run.id,
                    category="llm", provider=run.provider, amount=0, currency="USD",
                    input_tokens=max(0, response.usage.input_tokens), output_tokens=max(0, response.usage.output_tokens),
                    incurred_at=datetime.now(timezone.utc),
                    details=critic_cost_details(response, stage=stage)))
            persist_critic_audit(
                self.db, product_id=UUID(state["product_id"]), task_id=UUID(state["id"]),
                run_id=run.id, response=response,
            )
            self.db.commit()

    def follow_up(self, run_id: UUID, brief: str):
        _, state = self._state(run_id)
        # A new immutable research run; never rewrite a prior report.
        request = RunInput.model_validate({**state["request"], "brief": brief})
        bundles = [ResearchSourceBundle.model_validate(bundle) for bundle in state["source_bundles"]]
        if bundles:
            request.source_bundle = ResearchSourceBundle(label="Follow-up sources",
                sources=[source for bundle in bundles for source in bundle.sources][:50])
        return self.start(request, parent_id=run_id, frozen_bundles=bundles)

    def export(self, run_id: UUID, insight_id: str, target: str):
        _, state = self._state(run_id)
        report = self.report(run_id)
        insight = next((item for item in report["insights"] if item["id"] == insight_id), None)
        if insight is None:
            raise LookupError("Insight not found")
        # Serialize repeated exports against this run; supported by PostgreSQL in production.
        self.db.scalar(select(Task).where(Task.id == run_id).with_for_update())
        key = f"intelligence_export:{insight_id}:{target}"
        existing = self.db.scalar(select(Artifact).where(Artifact.task_id == run_id, Artifact.title == key,
            Artifact.kind == "competitive_intelligence_export"))
        if existing:
            result = json.loads(existing.content)
            self.db.commit()
            return result
        rows = [row for row in self.evidence(run_id) if row["id"] in insight["evidence_ids"]]
        description = insight["statement"] + "\nГипотеза/сигнал для проверки.\n" + insight["suggested_test"]
        description += "\nИсточники:\n" + "\n".join(row["source_url"] for row in rows)
        task = TaskService(self.db).create(product_id=UUID(state["product_id"]), parent_id=run_id,
            title=f"Проверить: {insight['statement'][:240]}", description=description,
            assigned_agent="content_seo" if target == "content_hypothesis" else "research_pmm")
        result = {"task_id": str(task.id), "target": target,
                  "report_artifact_id": state["report_artifact_id"], "insight_id": insight_id}
        hypothesis = ArtifactService(self.db).create(product_id=task.product_id, task_id=task.id,
            kind=target if target != "task" else "research_hypothesis", title=task.title,
            content=json.dumps({**result, "insight": insight, "evidence": rows}, ensure_ascii=False))
        result["artifact_id"] = str(hypothesis.id)
        ArtifactService(self.db).create(product_id=task.product_id, task_id=task.id,
            kind="task_brief", title=task.title, content=json.dumps({
                "title": task.title, "description": description, "assigned_agent": task.assigned_agent,
                "task_type": "content_plan" if target == "content_hypothesis" else "research",
                "operation": None, "requires_approval": False,
                "evidence_references": [{"kind": "artifact", "id": state["report_artifact_id"]},
                                        {"kind": "artifact", "id": str(hypothesis.id)}],
            }, ensure_ascii=False))
        ArtifactService(self.db).create(product_id=task.product_id, task_id=run_id,
            kind="competitive_intelligence_export", title=key, content=json.dumps(result))
        self.db.commit()
        return result
