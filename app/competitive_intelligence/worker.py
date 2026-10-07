"""Existing PostgreSQL queue, atomic claims, and bounded crash recovery."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.competitive_intelligence.service import AGENT, IntelligenceService
from app.models.domain import AgentRun, Task, TaskStatus


async def run_intelligence_once(session_factory, settings, *, builder=None):
    from app.core.composition import build_intelligence
    with session_factory() as db:
        # The execution deadline is 20 minutes. Interrupted runs are explicitly failed,
        # never silently restarted with different evidence or duplicated model spend.
        stale = list(db.scalars(select(Task).where(Task.assigned_agent == AGENT,
            Task.status == TaskStatus.RUNNING,
            Task.updated_at < datetime.now(timezone.utc) - timedelta(minutes=25)).limit(10)))
        service = IntelligenceService(db)
        for task in stale:
            artifact, state = service._state(task.id)
            task.status = TaskStatus.FAILED
            state["error"] = "research_interrupted"
            for run in db.scalars(select(AgentRun).where(AgentRun.task_id == task.id, AgentRun.status == "running")):
                run.status = "failed"
                run.error = "research_interrupted"
                run.finished_at = datetime.now(timezone.utc)
            service._save(artifact, state, "failed", status="failed")
        run_id = db.scalar(select(Task.id).where(Task.assigned_agent == AGENT,
            Task.status == TaskStatus.QUEUED).order_by(Task.created_at, Task.id).limit(1))
        if run_id is None:
            return 0
        try:
            configured = (builder or build_intelligence)(db, settings)
        except ValueError:
            claimed = db.execute(update(Task).where(Task.id == run_id,
                Task.status == TaskStatus.QUEUED).values(status=TaskStatus.FAILED))
            if claimed.rowcount != 1:
                db.rollback()
                return 0
            artifact, state = service._state(run_id)
            state["error"] = "research_model_unconfigured"
            service._save(artifact, state, "failed", status="failed")
            return 1
        return int(await configured.execute(run_id))
