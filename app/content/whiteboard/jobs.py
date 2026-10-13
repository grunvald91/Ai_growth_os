"""Immutable paid requests, persisted approvals and a bounded per-job call ledger."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.models.domain import AgentRun, Approval, Artifact, ArtifactStatus, Product, Task, TaskStatus
from app.privacy import PrivacyGateway
from .contracts import Contract, WhiteboardRequest
from .runtime import ReadinessError, WhiteboardModels
from pydantic import Field, model_validator

AGENT = 'whiteboard_renderer'
ACTION = 'paid_whiteboard_video_generation'


class JobRequest(Contract):
    schema_version: int = 1
    request: WhiteboardRequest
    models: WhiteboardModels
    max_api_calls: int = Field(default=25, ge=13, le=25)
    visual_review: bool = False
    visual_repair_from: UUID | None = None
    repair_source_digest: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    repair_manifest_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    inherited_calls: int = Field(default=0, ge=0, le=25)

    @model_validator(mode='after')
    def review_budget(self):
        if self.visual_review and self.max_api_calls < 18:
            raise ValueError('One visual review and up to three redraws require at least 18 calls')
        if self.visual_repair_from is not None:
            if not self.visual_review or not self.repair_source_digest or not self.repair_manifest_sha256:
                raise ValueError('A visual repair must bind its original approved request and media manifest')
        elif self.repair_source_digest is not None or self.repair_manifest_sha256 is not None or self.inherited_calls:
            raise ValueError('Repair metadata requires a source job')
        return self


def digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def records(db, task, *, lock=False):
    rows = select(Artifact).where(Artifact.task_id == task.id, Artifact.product_id == task.product_id,
                                 Artifact.kind.in_(['whiteboard_request', 'whiteboard_job_state']))
    if lock:
        rows = rows.with_for_update().execution_options(populate_existing=True)
    entries = {r.kind: r for r in db.scalars(rows)}
    if set(entries) != {'whiteboard_request', 'whiteboard_job_state'}:
        raise ReadinessError('approval_invalid')
    source, state = entries['whiteboard_request'], entries['whiteboard_job_state']
    approval_statement = select(Approval).where(Approval.product_id == task.product_id,
                                                Approval.artifact_id == source.id,
                                                Approval.action_type == ACTION)
    if lock:
        approval_statement = approval_statement.with_for_update().execution_options(populate_existing=True)
    approval = db.scalar(approval_statement)
    if approval is None:
        raise ReadinessError('approval_invalid')
    payload = JobRequest.model_validate_json(source.content)
    if payload.schema_version != 1 or payload.request.product_id != task.product_id or source.version != 1:
        raise ReadinessError('approval_invalid')
    if not secrets.compare_digest(approval.reason or '', 'sha256:' + digest(source.content)):
        raise ReadinessError('approval_invalid')
    return source, state, approval, payload


def queue_job(db, request: WhiteboardRequest, models: WhiteboardModels, *, max_api_calls=25, visual_review=False):
    if PrivacyGateway().sanitize_text(request.topic) != request.topic:
        raise ValueError('Sensitive text is not allowed')
    product = db.scalar(select(Product).where(Product.id == request.product_id).with_for_update())
    if product is None:
        raise LookupError('Product not found')
    payload = JobRequest(request=request.model_copy(update={'request_generation': True}),
                         models=models, max_api_calls=max_api_calls, visual_review=visual_review)
    # Preserve the exact legacy serialization when this feature is not selected.
    content = payload.model_dump_json(exclude={'visual_repair_from', 'repair_source_digest',
        'repair_manifest_sha256', 'inherited_calls'} | ({'visual_review'} if not visual_review else set()))
    title = 'WHITEBOARD-JOB: ' + digest(content)[:24]
    existing = db.scalar(select(Task).where(Task.product_id == product.id, Task.assigned_agent == AGENT,
                                           Task.title == title, Task.status.in_([
                                               TaskStatus.WAITING_APPROVAL, TaskStatus.QUEUED, TaskStatus.RUNNING])))
    if existing:
        return existing
    task = Task(product_id=product.id, assigned_agent=AGENT, title=title, priority=50,
                status=TaskStatus.WAITING_APPROVAL, description='Рисованное видео: ' + request.topic)
    db.add(task); db.flush()
    source = Artifact(product_id=product.id, task_id=task.id, kind='whiteboard_request',
                      title='Рисованное видео: ' + request.topic[:180], content=content,
                      status=ArtifactStatus.AWAITING_APPROVAL)
    db.add(source); db.flush()
    db.add(Artifact(product_id=product.id, task_id=task.id, kind='whiteboard_job_state',
                    title='Ход генерации', content=json.dumps({'calls': 0, 'stage': 'waiting_approval',
                        'error': None, 'cost_usd': None, 'cost_known': False, 'request_digest': digest(content)})))
    db.add(Approval(product_id=product.id, artifact_id=source.id, action_type=ACTION, risk_level='YELLOW',
                    status='pending', requested_by='owner', reason='sha256:' + digest(content)))
    db.commit()
    return task


def owner_task(db, product_id: UUID, task_id: UUID, *, lock=False):
    statement = select(Task).where(Task.id == task_id, Task.product_id == product_id, Task.assigned_agent == AGENT)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    task = db.scalar(statement)
    if task is None:
        raise LookupError('Job not found')
    return task


def approve_job(db, product_id, task_id, *, request_digest, confirmed, decided_by):
    db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    task = owner_task(db, product_id, task_id, lock=True)
    source, state, approval, payload = records(db, task, lock=True)
    if not confirmed or not secrets.compare_digest(digest(source.content), request_digest):
        raise ReadinessError('approval_invalid')
    if task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.COMPLETED} and approval.status == 'approved':
        return task
    if task.status != TaskStatus.WAITING_APPROVAL or approval.status != 'pending':
        raise ReadinessError('approval_invalid')
    approval.status = 'approved'; approval.decided_by = decided_by
    approval.decided_at = datetime.now(timezone.utc)
    source.status = ArtifactStatus.APPROVED
    value = json.loads(state.content); value.update(stage='queued', error=None)
    state.content = json.dumps(value)
    task.status = TaskStatus.QUEUED
    db.commit()
    return task


def cancel_job(db, product_id, task_id):
    task = owner_task(db, product_id, task_id, lock=True)
    if task.status not in {TaskStatus.WAITING_APPROVAL, TaskStatus.QUEUED, TaskStatus.RUNNING}:
        return task
    _, state, approval, _ = records(db, task, lock=True)
    task.status = TaskStatus.CANCELLED
    if approval.status == 'pending':
        approval.status = 'rejected'; approval.decided_at = datetime.now(timezone.utc)
    value = json.loads(state.content); value.update(stage='cancelled', error='cancelled')
    state.content = json.dumps(value)
    db.commit()
    return task


def job_directory(root: Path, product_id: UUID, task_id: UUID) -> Path:
    root = Path(root).absolute()
    result = root / '_whiteboard' / str(UUID(str(product_id))) / str(UUID(str(task_id)))
    for part in (result, *result.parents):
        if part.is_symlink():
            raise ValueError('Symlink storage rejected')
    return result


class CallLedger:
    def __init__(self, session_factory, product_id, task_id):
        self.session_factory, self.product_id, self.task_id = session_factory, product_id, task_id

    def begin(self, stage):
        with self.session_factory() as db:
            task = owner_task(db, self.product_id, self.task_id, lock=True)
            source, state, approval, payload = records(db, task, lock=True)
            if task.status == TaskStatus.CANCELLED:
                raise ReadinessError('cancelled')
            if task.status != TaskStatus.RUNNING or approval.status != 'approved' or source.status != ArtifactStatus.APPROVED:
                raise ReadinessError('approval_invalid')
            value = json.loads(state.content)
            if value['calls'] >= payload.max_api_calls:
                raise ReadinessError('call_limit')
            model_stage = {'visual_review': 'regions', 'visual_decision': 'storyboard', 'image_revision': 'image'}.get(stage, stage)
            run = AgentRun(task_id=task.id, agent_name='whiteboard_' + stage,
                           provider='configured_text' if model_stage == 'storyboard' else 'openrouter',
                           model=getattr(payload.models, model_stage), status='running',
                           started_at=datetime.now(timezone.utc), input_references=[str(source.id)])
            db.add(run); db.flush()
            value.update(calls=value['calls'] + 1, stage=stage, error=None,
                         last_call_started_at=datetime.now(timezone.utc).isoformat())
            state.content = json.dumps(value)
            db.commit()
            return run.id

    def finish(self, run_id, *, error=None):
        with self.session_factory() as db:
            run = db.get(AgentRun, run_id)
            run.status = 'failed' if error else 'completed'
            run.error = error
            run.finished_at = datetime.now(timezone.utc)
            db.commit()


class GuardedStage:
    def __init__(self, provider, stage, ledger):
        self.provider, self.stage, self.ledger = provider, stage, ledger

    def __getattr__(self, name):
        method = getattr(self.provider, name)
        async def guarded(*args, **kwargs):
            run_id = self.ledger.begin(self.stage)
            try:
                result = await method(*args, **kwargs)
            except BaseException:
                self.ledger.finish(run_id, error='provider_call_failed_billing_unknown')
                raise
            self.ledger.finish(run_id)
            return result
        return guarded
