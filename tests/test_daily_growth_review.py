import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.composition import build_review
from app.core.config import Settings
from app.llm.fake import FakeLLMProvider, LLMResponse, LLMUsage
from app.models.domain import (Product, Goal, Metric, Experiment, Decision, Task, Artifact,
                               Approval, AgentRun, CostRecord, TaskStatus)
from app.tools import FakeToolGateway
from app.workflows.context import load_context


@pytest.fixture
def product(db):
    product = Product(name='ServGo', slug='servgo')
    db.add(product)
    db.flush()
    db.add_all([Goal(product_id=product.id, name='Organic leads'),
                Metric(product_id=product.id, name='demo_leads', value=3, source='demo fixture',
                       observed_at=datetime.now(timezone.utc)),
                Experiment(product_id=product.id, name='SEO demo', hypothesis='Useful articles help', status='active'),
                Task(product_id=product.id, title='Existing research'),
                Artifact(product_id=product.id, kind='research', title='Existing brief', content='Hypothesis only'),
                Approval(product_id=product.id, action_type='publish', risk_level='YELLOW')])
    db.commit()
    return product


def output(operation=None):
    task = dict(title='Research booking search intent', description='Measure qualified leads, not views.',
                assigned_agent='research_pmm', task_type='research')
    if operation:
        task.update(operation=operation, requires_approval=False)
    return {'summary': 'Test organic customer acquisition', 'decisions': [
        {'decision_type': 'TEST', 'rationale': 'Evidence is limited.', 'next_tasks': [task]}]}


def setup_review(db, data, tmp_path, *, response=None):
    provider = FakeLLMProvider(response or LLMResponse(content=json.dumps(data), model='fake',
        usage=LLMUsage(input_tokens=12, output_tokens=15)))
    tools = FakeToolGateway()
    review = build_review(db, Settings(products_root=tmp_path), provider, tools)
    return review, provider, tools


@pytest.mark.asyncio
async def test_end_to_end_state_evidence_delegation_usage(db, product, tmp_path):
    metric = db.scalar(select(Metric))
    data = output()
    data['decisions'][0]['evidence_references'] = [{'kind': 'metric', 'id': str(metric.id)}]
    review, provider, tools = setup_review(db, data, tmp_path)
    result = await review.run(product.id)
    assert result.status == 'completed'
    assert len(result.decision_ids) == len(result.task_ids) == 1
    assert len(result.artifact_ids) == 3
    assert len(provider.requests) == 1
    context = json.loads(provider.last_request.messages[0].content)['context']['records']
    assert all(context[k] for k in ['goal', 'metric', 'experiment', 'task', 'artifact', 'approval'])
    assert db.get(Decision, result.decision_ids[0]).evidence_references == data['decisions'][0]['evidence_references']
    child = db.get(Task, result.task_ids[0])
    assert child.parent_id == result.task_id and child.assigned_agent == 'research_pmm'
    assert all(run.status == 'completed' for run in db.scalars(select(AgentRun)))
    assert db.scalar(select(CostRecord)).input_tokens == 12
    assert db.scalar(select(CostRecord)).details['cost_known'] is False
    assert tools.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,risk', [('publish', 'YELLOW'), ('send_email', 'YELLOW'),
    ('modify_website', 'YELLOW'), ('change_pricing', 'RED'), ('change_credentials', 'RED'),
    ('destructive_operation', 'RED')])
async def test_policy_overrides_approval_claim(db, product, tmp_path, operation, risk):
    review, _, tools = setup_review(db, output(operation), tmp_path)
    result = await review.run(product.id)
    assert result.status == 'completed'
    approval = db.get(Approval, result.approval_ids[0])
    assert (approval.status, approval.risk_level) == ('pending', risk)
    assert db.get(Artifact, approval.artifact_id).task_id == result.task_ids[0]
    assert db.get(Task, result.task_ids[0]).status == TaskStatus.WAITING_APPROVAL
    assert tools.requests == []


@pytest.mark.asyncio
async def test_ready_cold_start_can_request_publish_approval_without_execution(db, product, tmp_path):
    data = {
        'summary': 'Draft experiment is ready for a bounded approval-gated publish test.',
        'decisions': [{
            'decision_type': 'TEST',
            'rationale': 'The remaining blocker is unpublished status, not negative evidence.',
            'next_tasks': [{
                'title': 'Request approval for one own-site experiment publish',
                'description': 'Publish only the prepared own-site experiment artifact after human approval.',
                'assigned_agent': 'distribution',
                'task_type': 'action_request',
                'operation': 'publish',
                'requires_approval': True,
                'priority': 100,
            }],
        }],
    }
    review, provider, tools = setup_review(db, data, tmp_path)
    result = await review.run(product.id)

    assert result.status == 'completed'
    assert len(result.task_ids) == len(result.approval_ids) == 1
    task = db.get(Task, result.task_ids[0])
    approval = db.get(Approval, result.approval_ids[0])
    assert task.status == TaskStatus.WAITING_APPROVAL
    assert approval.status == 'pending'
    assert approval.action_type == 'publish'
    assert approval.risk_level == 'YELLOW'
    assert tools.requests == []
    assert 'Do not create a cold-start deadlock' in provider.last_request.system_prompt
    assert 'operation=publish' in provider.last_request.system_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize('content', ['not json', '{"summary":"x","decisions":[]}',
    json.dumps({'summary':'x','decisions':[{'decision_type':'BUY','rationale':'bad'}]}),
    json.dumps(output('launch_ad')), json.dumps(output('increase_budget'))])
async def test_malformed_and_paid_fail_atomically(db, product, tmp_path, content):
    before = {m: len(list(db.scalars(select(m)))) for m in [Decision, Task, Artifact, Approval]}
    review, _, tools = setup_review(db, None, tmp_path, response=LLMResponse(content=content, model='fake'))
    result = await review.run(product.id)
    assert result.status == 'failed' and result.error == 'invalid_strategy_output'
    assert db.get(AgentRun, result.run_id).status == 'failed'
    for model in before:
        assert len(list(db.scalars(select(model)))) == before[model] + (model is Task)
    assert tools.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['metric', 'goal', 'experiment'])
async def test_foreign_and_fabricated_evidence_rejected(db, product, tmp_path, kind):
    other = Product(name='Other', slug='other')
    db.add(other); db.flush()
    foreign = Goal(product_id=other.id, name='Foreign goal')
    db.add(foreign); db.commit()
    data = output()
    data['decisions'][0]['evidence_references'] = [{'kind': kind, 'id': str(foreign.id)}]
    review, _, _ = setup_review(db, data, tmp_path)
    result = await review.run(product.id)
    assert result.status == 'failed'
    assert list(db.scalars(select(Decision))) == []


@pytest.mark.asyncio
async def test_organic_article_video_briefs_and_no_evidence(db, product, tmp_path):
    data = output()
    data['decisions'][0]['next_tasks'] += [
        dict(title='Dzen article brief', description='SEO and TenChat adaptation with demo CTA', assigned_agent='content_seo', task_type='article_brief'),
        dict(title='Short video research brief', description='Telegram, YouTube Shorts and VK Clips; no rendering', assigned_agent='content_seo', task_type='video_brief')]
    review, provider, tools = setup_review(db, data, tmp_path)
    result = await review.run(product.id)
    assert result.status == 'completed' and len(result.task_ids) == 3
    assert db.get(Decision, result.decision_ids[0]).evidence_references == []
    assert 'traffic -> lead' in provider.last_request.system_prompt
    assert tools.requests == []


@pytest.mark.asyncio
async def test_provider_exception_never_persisted(db, product, tmp_path):
    class BrokenProvider:
        async def generate(self, request):
            raise RuntimeError('api_key=private-provider-secret')
    review = build_review(db, Settings(products_root=tmp_path), BrokenProvider())
    result = await review.run(product.id)
    assert result.status == 'failed'
    assert db.get(AgentRun, result.run_id).error == 'provider_request_failed'
    assert list(db.scalars(select(Decision))) == []


@pytest.mark.asyncio
async def test_strategy_failure_rolls_back_all_partial_writes(db, product, tmp_path, monkeypatch):
    from app.services.decisions import DecisionService
    original = DecisionService.create
    count = 0
    def fail_second(self, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise ValueError('password=secret-failure')
        return original(self, **kwargs)
    monkeypatch.setattr(DecisionService, 'create', fail_second)
    data = output()
    data['decisions'].append({'decision_type': 'KEEP', 'rationale': 'Second'})
    review, _, _ = setup_review(db, data, tmp_path)
    result = await review.run(product.id)
    assert result.status == 'failed' and result.decision_ids == []
    assert list(db.scalars(select(Decision))) == []
    assert len(list(db.scalars(select(Artifact)))) == 1
    assert len(list(db.scalars(select(Task)))) == 2
    assert len(list(db.scalars(select(CostRecord)))) == 1


@pytest.mark.asyncio
async def test_privacy_on_input_output_and_canonical_file(db, product, tmp_path):
    path = tmp_path / 'servgo' / '.claude'
    path.mkdir(parents=True)
    (path / 'product-marketing-context.md').write_text('Verified product. api_key=private-context-secret')
    product.description = 'password=private-db-secret'
    product.context_path = '/etc/passwd'
    db.commit()
    data = output()
    data['summary'] = 'api_key=private-response-secret'
    review, provider, _ = setup_review(db, data, tmp_path)
    result = await review.run(product.id)
    assert result.status == 'completed'
    request = provider.last_request.model_dump_json()
    assert 'private-context-secret' not in request and 'private-db-secret' not in request
    assert 'Verified product' in request
    assert all('private-response-secret' not in a.content for a in db.scalars(select(Artifact)))


def test_context_bounded_and_bootstrap_idempotent(db, product, tmp_path):
    from app.bootstrap import bootstrap
    for index in range(25):
        db.add(Task(product_id=product.id, title=f'Task {index}', description='x' * 10000))
    db.commit()
    context = load_context(db, product, tmp_path)
    assert len(context['records']['task']) == 20
    assert 'task' in context['limits']['truncated_kinds']
    descriptions = [row['description'] for row in context['records']['task'] if row['description'] is not None]
    assert descriptions
    assert all(len(description) <= 1500 for description in descriptions)
    assert any(len(description) == 1500 for description in descriptions)
    assert bootstrap(db) == bootstrap(db) == product.id
    assert len(list(db.scalars(select(Metric)))) == 1


@pytest.mark.asyncio
async def test_active_duplicate_specialist_task_is_not_recreated(db, product, tmp_path):
    existing = Task(
        product_id=product.id,
        title='Research booking search intent',
        description='Already queued from a prior review.',
        assigned_agent='research_pmm',
        status=TaskStatus.QUEUED,
        priority=50,
    )
    db.add(existing)
    db.commit()

    review, _, _ = setup_review(db, output(), tmp_path)
    result = await review.run(product.id)

    assert result.status == 'completed'
    assert len(result.decision_ids) == 1
    assert result.task_ids == []
    active = list(db.scalars(
        select(Task).where(
            Task.product_id == product.id,
            Task.title == 'Research booking search intent',
            Task.assigned_agent == 'research_pmm',
            Task.status == TaskStatus.QUEUED,
        )
    ))
    assert active == [existing]
    task_briefs = list(db.scalars(
        select(Artifact).where(
            Artifact.product_id == product.id,
            Artifact.kind == 'task_brief',
            Artifact.title == 'Research booking search intent',
        )
    ))
    assert task_briefs == []
