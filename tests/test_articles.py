"""Offline article workflow integration and API acceptance tests."""
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.products import get_article_production
from app.core.composition import build_article_production
from app.core.config import Settings
from app.db.models import Base
from app.db.session import get_db
from app.llm.fake import LLMResponse, LLMUsage
from app.main import app
from app.models.domain import Product, Task, Artifact, Approval, AgentRun, CostRecord, ToolCall, Publication, TaskStatus, Experiment
from app.workflows.article_contracts import ArticleProductionInput, ArticleArtifact


class SequenceProvider:
    def __init__(self, outputs):
        self.outputs = outputs
        self.requests = []

    async def generate(self, request):
        self.requests.append(request.model_copy(deep=True))
        output = self.outputs[len(self.requests) - 1]
        if isinstance(output, Exception):
            raise output
        return LLMResponse(content=output if isinstance(output, str) else json.dumps(output),
            model='fake', usage=LLMUsage(input_tokens=10, output_tokens=20))


def valid_outputs():
    measurement = {k: dict(event=k + '_event', metric=k + '_rate', attribution='Proposed campaign attribution')
        for k in ['content', 'traffic', 'lead', 'activation', 'customer']}
    measurement.update(activation_definition='demo', review_window_days=14, success_hypothesis='Useful guides may generate qualified inquiries')
    kinds = ['dzen', 'tenchat', 'vc_ru', 'telegram', 'faq', 'founder', 'short_video']
    hooks = ['A missed appointment changes the day', 'Discuss an operations checklist with peers',
        'An editorial experiment in service design', 'Save these three booking reminders',
        'Which reservation questions need answers?', 'Why I would interview owners before building',
        'Open on a calendar and cut to an empty chair']
    frames = ['Narrative educational reading', 'Professional network discussion', 'Analytical publication',
        'Compact channel note', 'Question and answer reference', 'Personal reflective memo', 'Visual storyboard instructions']
    return [dict(audience='Service business owners', pain='Manual scheduling friction', search_intent='Assess a booking workflow',
        angle='Practical diagnosis before buying', titles=['A booking checklist', 'Evaluate appointment friction'], hypothesis='Clear information may improve demo interest'),
        dict(primary_query='online service booking', related_queries=['appointment checklist'], title='A booking checklist',
        hook='Start with the customer journey', outline=['Understand the problem', 'Map the workflow', 'Plan an experiment'], adaptation_notes='Educational draft'),
        dict(sections=[dict(heading=h, kind='advice', claim_ids=[], body=(b + ' ') * 12) for h, b in [
            ('Map the journey', 'Consider writing down each booking step and asking an owner which steps need clarification.'),
            ('Discuss expectations', 'Try discussing scheduling expectations with a small group before deciding what to change.'),
            ('Prepare an experiment', 'Consider a limited trial with a clear stopping rule and a review of qualitative feedback.'),
            ('Review outcomes', 'Plan to inspect qualified inquiries and completed bookings rather than assuming views create customers.')]],
            claims=[], proof_needed_notes=['Verify features and collect real funnel evidence']),
        dict(cta='Request a demo to discuss your booking workflow', landing_page=dict(label='Proposed demo page', destination='/demo'),
            internal_links=[dict(label='Proposed guide', destination='/guides/booking')], measurement=measurement,
            distribution=[dict(channel='own_site', action='prepare_draft', rationale='Start with an owned draft', downstream_goal='lead')],
            derivatives=[dict(kind=k, audience='Service owners', framing=f, hook=h, outline=['Explain the problem', 'Invite a qualified next step'],
                cta='Discuss a demo', landing_page=dict(label='Proposed demo', destination='/demo'), adaptation_notes='Draft only', measurement=measurement,
                video_mode='brief_only' if k == 'short_video' else None) for k, h, f in zip(kinds, hooks, frames)])]


@pytest.fixture
def product(db):
    row = Product(name='ServGo', slug='servgo')
    db.add(row); db.commit()
    return row


def setup(db, product, tmp_path, outputs=None, **kwargs):
    provider = SequenceProvider(outputs if outputs is not None else valid_outputs())
    workflow = build_article_production(db, Settings(products_root=tmp_path), provider)
    request = ArticleProductionInput(product_id=product.id, topic='Booking checklist', audience='Owners', problem='Scheduling friction', **kwargs)
    return workflow, provider, request


@pytest.mark.asyncio
async def test_long_form_distinct_variants_measurement_and_audit(db, product, tmp_path):
    workflow, provider, request = setup(db, product, tmp_path)
    result = await workflow.run(request)
    assert result.status == 'completed', result.error
    assert len(result.artifact_ids) == 8 and len(result.run_ids) == len(provider.requests) == 4
    content = ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content)
    assert len(content.full_article) >= 2000 and len(content.draft.sections) >= 4
    assert content.conversion.cta
    assert len({d.hook for d in content.conversion.derivatives}) == 7
    assert len({d.framing for d in content.conversion.derivatives}) == 7
    assert next(d for d in content.conversion.derivatives if d.kind == 'short_video').video_mode == 'brief_only'
    for plan in [content.conversion.measurement] + [d.measurement for d in content.conversion.derivatives]:
        assert all(getattr(plan, k).event for k in ['content', 'traffic', 'lead', 'activation', 'customer'])
    assert all(db.get(Artifact, aid).task_id == result.task_id for aid in result.artifact_ids)
    assert all(db.get(AgentRun, rid).status == 'completed' for rid in result.run_ids)
    assert len(list(db.scalars(select(CostRecord)))) == 4
    assert all(c.details['cost_known'] is False for c in db.scalars(select(CostRecord)))
    assert list(db.scalars(select(ToolCall))) == list(db.scalars(select(Publication))) == []
    assert result.approval_ids == []


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['malformed', 'short', 'missing_cta', 'missing_measurement', 'copies', 'fake_claim', 'video', 'external_link', 'extra', 'derivative_measurement', 'source_copy'])
async def test_invalid_output_is_atomic(db, product, tmp_path, mutation):
    outputs = valid_outputs()
    if mutation == 'malformed': outputs[0] = 'not json'
    elif mutation == 'short': outputs[2]['sections'] = outputs[2]['sections'][:1]
    elif mutation == 'missing_cta': del outputs[3]['cta']
    elif mutation == 'missing_measurement': del outputs[3]['measurement']['customer']
    elif mutation == 'copies': outputs[3]['derivatives'][1]['hook'] = outputs[3]['derivatives'][0]['hook']
    elif mutation == 'fake_claim': outputs[2]['claims'] = [dict(claim='Guaranteed growth', quote='Guaranteed growth', source_id='invented')]
    elif mutation == 'video': outputs[3]['derivatives'][-1]['video_mode'] = 'render_mp4'
    elif mutation == 'external_link': outputs[3]['landing_page']['destination'] = 'https://unverified.example/demo'
    elif mutation == 'extra': outputs[0]['verified_revenue'] = 1000000
    elif mutation == 'source_copy': outputs[3]['derivatives'][0]['hook'] = outputs[1]['hook']
    elif mutation == 'derivative_measurement': del outputs[3]['derivatives'][0]['measurement']
    workflow, _, request = setup(db, product, tmp_path, outputs)
    result = await workflow.run(request)
    assert result.status == 'failed' and result.error
    assert result.artifact_ids == result.approval_ids == []
    assert list(db.scalars(select(Artifact))) == list(db.scalars(select(Approval))) == []
    assert db.get(Task, result.task_id).status == TaskStatus.FAILED
    assert any(r.status == 'failed' for r in db.scalars(select(AgentRun)))




@pytest.mark.asyncio
async def test_article_binds_verified_experiment_context(db, product, tmp_path):
    experiment = Experiment(
        product_id=product.id,
        name='Own-site SEO test',
        hypothesis='Massage studios are an unranked audience hypothesis.',
        audience='Small massage-studio owners evaluating booking tools.',
        channel='own_site',
        target_metric='qualified_leads',
        success_condition='Persist at least one qualified lead tied to the page before considering KEEP.',
        status='draft',
    )
    db.add(experiment); db.commit()
    workflow, provider, request = setup(db, product, tmp_path, experiment_id=experiment.id)
    result = await workflow.run(request)
    assert result.status == 'completed', result.error
    content = ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content)
    assert content.experiment_id == experiment.id
    outbound = provider.requests[0].messages[0].content
    assert str(experiment.id) in outbound
    assert 'qualified_leads' in outbound


@pytest.mark.asyncio
async def test_article_rejects_missing_or_foreign_experiment(db, product, tmp_path):
    other = Product(name='Other experiment owner', slug='other-experiment-owner')
    db.add(other); db.flush()
    foreign = Experiment(product_id=other.id, name='Foreign', hypothesis='Foreign hypothesis')
    db.add(foreign); db.commit()

    for experiment_id in [uuid4(), foreign.id]:
        workflow, provider, request = setup(db, product, tmp_path, experiment_id=experiment_id)
        with pytest.raises(LookupError):
            await workflow.run(request)
        assert provider.requests == []


@pytest.mark.asyncio
async def test_publication_only_requests_approval(db, product, tmp_path):
    workflow, _, request = setup(db, product, tmp_path, request_publication=True)
    result = await workflow.run(request)
    assert result.status == 'completed' and result.approval_ids
    assert all(db.get(Approval, aid).status == 'pending' and db.get(Approval, aid).risk_level == 'YELLOW' for aid in result.approval_ids)
    assert list(db.scalars(select(ToolCall))) == list(db.scalars(select(Publication))) == []


@pytest.mark.asyncio
async def test_provider_failure_does_not_expose_credentials(db, product, tmp_path):
    workflow, _, request = setup(db, product, tmp_path, [RuntimeError('api_key=private-provider-secret')])
    result = await workflow.run(request)
    assert result.status == 'failed'
    assert 'private-provider-secret' not in result.model_dump_json()
    assert all('private-provider-secret' not in (r.error or '') for r in db.scalars(select(AgentRun)))


@pytest.mark.asyncio
async def test_privacy_on_all_requests_and_output(db, product, tmp_path):
    path = tmp_path / 'servgo' / '.claude'
    path.mkdir(parents=True)
    (path / 'product-marketing-context.md').write_text('## Product\nServGo is a booking service. api_key=private-context-secret')
    outputs = valid_outputs()
    outputs[0]['hypothesis'] += ' api_key=private-response-secret'
    workflow, provider, request = setup(db, product, tmp_path, outputs)
    request.problem += ' password=private-input-secret'
    result = await workflow.run(request)
    assert result.status == 'completed', result.error
    sent = ''.join(r.model_dump_json() for r in provider.requests)
    stored = ''.join(a.content for a in db.scalars(select(Artifact)))
    for secret in ['private-input-secret', 'private-context-secret', 'private-response-secret']:
        assert secret not in sent and secret not in stored


@pytest.mark.asyncio
@pytest.mark.parametrize('reference', ['missing_task', 'foreign_task', 'missing_decision', 'nonqueued'])
async def test_invalid_refs_never_call_provider(db, product, tmp_path, reference):
    other = Product(name='Other', slug='other'); db.add(other); db.flush()
    task = Task(product_id=other.id if reference == 'foreign_task' else product.id, title='Article',
        status=TaskStatus.COMPLETED if reference == 'nonqueued' else TaskStatus.QUEUED)
    db.add(task); db.commit()
    refs = {'missing_task': dict(task_id=uuid4()), 'foreign_task': dict(task_id=task.id),
        'missing_decision': dict(decision_id=uuid4()), 'nonqueued': dict(task_id=task.id)}
    workflow, provider, request = setup(db, product, tmp_path, **refs[reference])
    with pytest.raises(ValueError if reference == 'nonqueued' else LookupError):
        await workflow.run(request)
    assert provider.requests == []


def test_api_ids_inspection_and_bad_references(tmp_path):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        provider = SequenceProvider(valid_outputs())
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_article_production] = lambda: build_article_production(db, Settings(products_root=tmp_path), provider)
        try:
            with TestClient(app) as client:
                pid = client.post('/products', json=dict(name='ServGo', slug='servgo')).json()['id']
                body = dict(topic='Booking checklist')
                response = client.post(f'/products/{pid}/articles', json=body)
                assert response.status_code == 200, response.text
                result = response.json()
                assert result['status'] == 'completed', result
                assert result['main_artifact_id'] == result['artifact_ids'][0]
                assert len(result['derivative_artifact_ids']) == 7 and len(result['run_ids']) == 4
                aid = result['main_artifact_id']
                assert client.get(f'/products/{pid}/artifacts/{aid}').status_code == 200
                assert len(client.get(f'/products/{pid}/artifacts').json()) == 8
                other = client.post('/products', json=dict(name='Other', slug='other')).json()['id']
                assert client.get(f'/products/{other}/artifacts/{aid}').status_code == 404
                for key in ['task_id', 'decision_id']:
                    assert client.post(f'/products/{pid}/articles', json={**body, key: str(uuid4())}).status_code == 404
                assert client.post(f'/products/{uuid4()}/articles', json=body).status_code == 404
                assert client.post(f'/products/{pid}/articles', json={**body, 'channel': 'ads'}).status_code == 422
                assert client.post(f'/products/{pid}/articles', json={**body, 'topic': '   '}).status_code == 422
                assert client.post(f'/products/{pid}/articles', json={**body, 'task_id': result['task_id']}).status_code == 409
        finally:
            app.dependency_overrides.clear()
    engine.dispose()
