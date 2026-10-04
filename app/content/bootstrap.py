"""Explicit launch of the first Issue #8 package; idempotent across deployments."""
import json
from sqlalchemy import select
from app.content.service import queue_content
from app.db.session import SessionLocal
from app.models.domain import Artifact, Product, Task, TaskStatus, AgentRun, CostRecord

def recover_truncated_package(db, request):
    """One recovery of the original owner-requested package; preserve failure/cost history."""
    task = db.scalar(select(Task).where(Task.id == request.task_id).with_for_update())
    if task is None or task.status != TaskStatus.FAILED or task.assigned_agent != 'content_factory':
        return False
    if not json.loads(request.content).get('produce_ready_assets'):
        return False
    marker = db.scalar(select(Artifact.id).where(Artifact.task_id == task.id, Artifact.kind == 'content_recovery'))
    failure = db.scalar(select(AgentRun.id).join(CostRecord, CostRecord.agent_run_id == AgentRun.id).where(
        AgentRun.task_id == task.id, AgentRun.agent_name == 'repurposing_editor',
        AgentRun.error == 'invalid_article_output:repurposing_editor', CostRecord.output_tokens == 4000).limit(1))
    if marker or failure is None:
        return False
    db.add(Artifact(product_id=task.product_id, task_id=task.id, kind='content_recovery',
                    title='One recovery after truncated adaptation response',
                    content=json.dumps({'reason': 'repurposing_response_budget_repair', 'failure_run_id': str(failure)})))
    task.status = TaskStatus.QUEUED
    db.commit()
    return True

def main():
    with SessionLocal() as db:
        product = db.scalar(select(Product).where(Product.slug == 'servgo').with_for_update())
        if product is None:
            raise LookupError('ServGo not found')
        from app.content.calendar import queue_plan, recover_stale_planner
        if recover_stale_planner(db):
            print('Interrupted editorial task recovered once; attempt history preserved')
        planner = db.scalar(select(Task).where(Task.product_id == product.id, Task.assigned_agent == 'editorial_planner').order_by(Task.created_at).limit(1))
        if planner is None:
            planning = queue_plan(db, product.id)
            print('First editorial plan queued:', planning.id)
        elif planner.status == TaskStatus.FAILED and '[schema_recovery_once]' not in (planner.description or ''):
            run = db.scalar(select(AgentRun).where(AgentRun.task_id == planner.id, AgentRun.error == 'editorial_plan_failed:ValidationError').limit(1))
            if run:
                planner.status = TaskStatus.QUEUED
                planner.description = (planner.description or '') + ' [schema_recovery_once]'
                db.commit()
                print('Initial editorial draft requeued once after schema repair:', planner.id)
        if planner is not None and planner.status == TaskStatus.FAILED and '[calendar_coherence_recovery_once]' not in (planner.description or ''):
            run = db.scalar(select(AgentRun).where(AgentRun.task_id == planner.id, AgentRun.error == 'editorial_plan_failed:schema::value_error:ValidationError').limit(1))
            if run:
                planner.status = TaskStatus.QUEUED
                planner.description = (planner.description or '') + ' [calendar_coherence_recovery_once]'
                db.commit()
                print('Initial editorial task requeued once after explicit calendar constraints:', planner.id)
        if planner is not None and planner.status == TaskStatus.FAILED and '[evidence_enum_recovery_once]' not in (planner.description or ''):
            run = db.scalar(select(AgentRun).where(AgentRun.task_id == planner.id, AgentRun.error == 'editorial_plan_failed:schema:context:unsupported_evidence:Use supplied evidence IDs only, not invented IDs or URLs:ValueError').limit(1))
            if run:
                planner.status = TaskStatus.QUEUED
                planner.description = (planner.description or '') + ' [evidence_enum_recovery_once]'
                db.commit()
                print('Initial editorial task requeued once after evidence enum constraints:', planner.id)
        existing = db.scalar(select(Artifact).where(Artifact.product_id == product.id,
            Artifact.kind == 'content_request').order_by(Artifact.created_at).limit(1))
        if existing:
            if recover_truncated_package(db, existing):
                print('Issue #8 truncated content package requeued once:', existing.task_id)
            else:
                print('Issue #8 content package already requested; not duplicated')
        else:
            task = queue_content(db, product.id)
            print('Issue #8 first content task queued:', task.id)

if __name__ == '__main__':
    main()
