from datetime import datetime, timedelta, timezone
import pytest
from scripts.content_studio_deploy_tasks import checked_stale_tasks
from app.models.domain import Product,Task,TaskStatus,AgentRun

def test_deploy_does_not_interrupt_recent_or_unapproved_tasks(db):
    now=datetime.now(timezone.utc)
    old=now-timedelta(days=2)
    product=Product(name='Deploy fixture',slug='deploy-fixture')
    db.add(product);db.flush()
    task=Task(product_id=product.id,title='Stale job',status=TaskStatus.RUNNING,created_at=old,updated_at=old)
    db.add(task);db.flush()
    with pytest.raises(RuntimeError):checked_stale_tasks(db,set(),now)
    assert checked_stale_tasks(db,{task.id},now)==[task]
    run=AgentRun(task_id=task.id,agent_name='test',status='running',started_at=now,created_at=now,updated_at=now)
    db.add(run);db.flush()
    with pytest.raises(RuntimeError):checked_stale_tasks(db,{task.id},now)
    assert task.status==TaskStatus.RUNNING
