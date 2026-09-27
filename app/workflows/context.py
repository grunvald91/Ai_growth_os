"""Allowlisted persisted context; no arbitrary paths or unbounded ORM serialization."""
import json
from datetime import date
from pathlib import Path
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.domain import Approval, Artifact, Decision, Experiment, Goal, Metric, Product, Task, TaskStatus

ROW_LIMIT = 20
TEXT_LIMIT = 1500
CONTEXT_LIMIT = 150_000
FIELDS = {
    'goal': (Goal, ('name', 'description', 'target_metric', 'target_value', 'starts_on', 'ends_on', 'status')),
    'metric': (Metric, ('name', 'value', 'unit', 'source', 'observed_at', 'goal_id', 'experiment_id')),
    'experiment': (Experiment, ('name', 'hypothesis', 'channel', 'target_metric', 'success_condition', 'status', 'conclusion')),
    'decision': (Decision, ('decision_type', 'rationale')),
    'task': (Task, ('title', 'description', 'assigned_agent', 'status', 'priority')),
    'artifact': (Artifact, ('kind', 'title', 'content', 'status')),
    'approval': (Approval, ('artifact_id', 'action_type', 'risk_level', 'status')),
}


def load_context(db: Session, product: Product, products_root: Path) -> dict:
    context = {'product': {'id': str(product.id), 'name': product.name[:200],
                           'description': (product.description or '')[:TEXT_LIMIT]},
               'stable_context': '', 'records': {}, 'limits': {'rows_per_kind': ROW_LIMIT,
               'text_characters': TEXT_LIMIT, 'truncated_kinds': []}}
    # Caller-supplied context_path is deliberately not read. Only the versioned
    # canonical file under this product's directory is eligible.
    if product.slug and all(c.isascii() and (c.isalnum() or c == '-') for c in product.slug):
        root = products_root.resolve()
        candidate = (root / product.slug / '.claude' / 'product-marketing-context.md').resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            with candidate.open(encoding='utf-8') as stream:
                context['stable_context'] = stream.read(12_000)
    today = date.today()
    for kind, (model, fields) in FIELDS.items():
        query = select(model).where(model.product_id == product.id)
        if model is Goal:
            query = query.where(Goal.status == 'active', or_(Goal.starts_on.is_(None), Goal.starts_on <= today),
                                or_(Goal.ends_on.is_(None), Goal.ends_on >= today))
        elif model is Task:
            query = query.where(Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL]))
        elif model is Approval:
            query = query.where(Approval.status == 'pending')
        # Active experiments first, then recent history; metrics by observation time.
        if model is Experiment:
            query = query.order_by(Experiment.status.in_(['active', 'running']).desc())
        if model is Metric:
            query = query.order_by(Metric.observed_at.desc())
        rows = list(db.scalars(query.order_by(model.created_at.desc(), model.id).limit(ROW_LIMIT + 1)))
        if len(rows) > ROW_LIMIT:
            context['limits']['truncated_kinds'].append(kind)
        context['records'][kind] = [dict(id=str(row.id), **{
            field: str(getattr(row, field))[:TEXT_LIMIT] if getattr(row, field) is not None else None
            for field in fields}) for row in rows[:ROW_LIMIT]]
    if len(json.dumps(context)) > CONTEXT_LIMIT:
        raise ValueError('Context exceeds bound')
    return context


def analytics_snapshot(context: dict) -> dict:
    """Report observations, never sum incompatible sources/windows or invent rates."""
    metrics = context['records']['metric']
    return {'method': 'persisted_observations_only', 'metric_count': len(metrics),
            'observations': metrics, 'missing_evidence': not bool(metrics),
            'funnel': ['traffic', 'lead', 'demo/registration/booking', 'customer'],
            'caveat': 'Bounded observations, not totals. No inferred attribution or conversion rates.'}


def validate_references(review, context: dict) -> None:
    allowed = {kind: {UUID(row['id']) for row in rows} for kind, rows in context['records'].items()}
    for decision in review.decisions:
        if decision.goal_id is not None and decision.goal_id not in allowed['goal']:
            raise ValueError('Goal outside supplied context')
        if decision.experiment_id is not None and decision.experiment_id not in allowed['experiment']:
            raise ValueError('Experiment outside supplied context')
        for ref in decision.evidence_references:
            if ref.id not in allowed[ref.kind]:
                raise ValueError('Evidence outside supplied context')
