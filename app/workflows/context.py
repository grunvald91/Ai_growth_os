"""Allowlisted persisted context; no arbitrary paths or unbounded ORM serialization."""
import json
from datetime import date
from pathlib import Path
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from app.company_profile import load_company_profile, profile_snapshot, render_company_profile

from app.models.domain import Approval, Artifact, Decision, Experiment, Goal, Metric, Product, Task, TaskStatus

ROW_LIMIT = 20
# Business facts from the booking platform come as one metric per fact, so metrics get a longer list.
KIND_LIMITS = {'metric': 45}
TEXT_LIMIT = 1500
CONTEXT_LIMIT = 150_000
FIELDS = {
    'goal': (Goal, ('name', 'description', 'target_metric', 'target_value', 'starts_on', 'ends_on', 'status')),
    'metric': (Metric, ('name', 'value', 'unit', 'source', 'observed_at', 'goal_id', 'experiment_id')),
    'experiment': (Experiment, ('experiment_key', 'name', 'hypothesis', 'audience', 'channel', 'action', 'target_metric', 'secondary_signals', 'attribution_method', 'baseline_summary', 'observation_window_days', 'time_budget_minutes', 'success_condition', 'stop_condition', 'inconclusive_condition', 'budget', 'currency', 'status', 'conclusion')),
    'decision': (Decision, ('decision_type', 'rationale')),
    'task': (Task, ('title', 'description', 'assigned_agent', 'status', 'priority')),
    'artifact': (Artifact, ('kind', 'title', 'content', 'status')),
    'approval': (Approval, ('artifact_id', 'action_type', 'risk_level', 'status')),
}


def _serialized_size(value: dict) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _compact_context_to_bound(context: dict) -> dict:
    """Deterministically reduce historical records before failing the workflow.

    Dynamic history is useful context but must never prevent a new content task
    from starting merely because the product accumulated many long artifacts.
    Canonical company profile and stable context remain present; only bounded
    history excerpts are progressively shortened.
    """
    if _serialized_size(context) <= CONTEXT_LIMIT:
        return context

    passes = (
        (12, 900, 40_000),
        (8, 600, 28_000),
        (5, 350, 18_000),
    )
    for row_limit, text_limit, stable_limit in passes:
        for kind, rows in list(context.get('records', {}).items()):
            compact_rows = []
            for row in rows[:row_limit]:
                compact_rows.append({
                    key: (value[:text_limit] if isinstance(value, str) else value)
                    for key, value in row.items()
                })
            context['records'][kind] = compact_rows
        context['stable_context'] = str(context.get('stable_context') or '')[:stable_limit]
        context['limits']['history_compacted'] = True
        context['limits']['effective_rows_per_kind'] = row_limit
        context['limits']['effective_text_characters'] = text_limit
        if _serialized_size(context) <= CONTEXT_LIMIT:
            return context
    raise ValueError('Context exceeds bound after compaction')


def load_context(db: Session, product: Product, products_root: Path) -> dict:
    context = {'product': {'id': str(product.id), 'name': product.name[:200],
                           'description': (product.description or '')[:TEXT_LIMIT]},
               'stable_context': '', 'records': {}, 'limits': {'rows_per_kind': ROW_LIMIT,
               'text_characters': TEXT_LIMIT, 'truncated_kinds': []}}
    # Caller-supplied context_path is deliberately not read. Only the versioned
    # canonical file under this product's directory is eligible.
    loaded = load_company_profile(products_root, product.slug)
    context['company_profile'] = profile_snapshot(loaded)
    if loaded.profile is not None:
        context['stable_context'] = render_company_profile(loaded.profile)
    elif loaded.source == 'legacy_markdown' and loaded.legacy_text is not None:
        context['stable_context'] = loaded.legacy_text[:12_000]
    elif loaded.validation_errors:
        raise ValueError('Invalid canonical company profile')
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
        elif model is Artifact:
            # Unreviewed company material is confined to intake. Marketing sees
            # the canonical profile and onboarding state, never pending sources.
            query = query.where(Artifact.kind.not_in([
                'company_intake_sources', 'company_profile_draft', 'company_profile_review',
                'company_profile_previous', 'company_profile_apply_intent',
            ]))
        # Active experiments first, then recent history; metrics by observation time.
        if model is Experiment:
            query = query.order_by(Experiment.status.in_(['active', 'running']).desc())
        if model is Metric:
            query = query.order_by(Metric.observed_at.desc())
        limit = KIND_LIMITS.get(kind, ROW_LIMIT)
        rows = list(db.scalars(query.order_by(model.created_at.desc(), model.id).limit(limit + 1)))
        if len(rows) > limit:
            context['limits']['truncated_kinds'].append(kind)
        context['records'][kind] = [dict(id=str(row.id), **{
            field: str(getattr(row, field))[:TEXT_LIMIT] if getattr(row, field) is not None else None
            for field in fields}) for row in rows[:limit]]
    active_intake = db.scalar(select(Task.id).where(
        Task.product_id == product.id, Task.assigned_agent == 'company_intake',
        Task.status.in_([TaskStatus.QUEUED, TaskStatus.WAITING_APPROVAL, TaskStatus.RUNNING])
    ).limit(1)) is not None
    readiness = context['company_profile']['readiness']
    state = ('intake_in_progress' if active_intake else
             'no_profile' if loaded.profile is None else
             'profile_incomplete' if not readiness['strategy']['ready'] else
             'ready' if all(gate['ready'] for gate in readiness.values()) else 'partially_ready')
    context['company_onboarding'] = {'state': state, 'intake_in_progress': active_intake}
    return _compact_context_to_bound(context)


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
