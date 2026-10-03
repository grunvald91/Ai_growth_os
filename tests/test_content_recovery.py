import json
from sqlalchemy import select
from app.content.bootstrap import recover_truncated_package
from app.models.domain import Product, Task, TaskStatus, Artifact, AgentRun, CostRecord


def test_first_failed_package_recovery_is_once_and_preserves_audit(db):
    product = Product(name='ServGo', slug='servgo')
    db.add(product)
    db.flush()
    task = Task(product_id=product.id, title='Content', assigned_agent='content_factory', status=TaskStatus.FAILED)
    db.add(task)
    db.flush()
    request = Artifact(product_id=product.id, task_id=task.id, kind='content_request', title='Request',
                       content=json.dumps({'produce_ready_assets': True}))
    run = AgentRun(task_id=task.id, agent_name='repurposing_editor', provider='xai', model='kimi-2.7',
                   status='failed', error='invalid_article_output:repurposing_editor')
    db.add_all([request, run])
    db.flush()
    cost = CostRecord(product_id=product.id, agent_run_id=run.id, category='llm', provider='xai',
                      amount=0, currency='USD', input_tokens=100, output_tokens=3999)
    db.add(cost)
    db.commit()
    assert not recover_truncated_package(db, request)
    cost.output_tokens = 4000
    db.commit()
    assert recover_truncated_package(db, request)
    assert task.status == TaskStatus.QUEUED and run.status == 'failed'
    assert db.get(CostRecord, cost.id).output_tokens == 4000
    task.status = TaskStatus.FAILED
    db.commit()
    assert not recover_truncated_package(db, request)
    assert len(list(db.scalars(select(Artifact).where(Artifact.kind == 'content_recovery')))) == 1
