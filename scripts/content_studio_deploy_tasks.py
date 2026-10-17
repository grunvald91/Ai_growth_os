"""Explicit, age-checked recovery of jobs orphaned by a previous restart."""
import os
import sys
from datetime import datetime, timedelta, timezone
from uuid import UUID
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.domain import AgentRun, Task, TaskStatus, Artifact

def checked_stale_tasks(db, allowed, now):
    cutoff=now-timedelta(hours=24)
    tasks=list(db.scalars(select(Task).where(Task.status==TaskStatus.RUNNING)))
    for task in tasks:
        if task.id not in allowed:
            raise RuntimeError('A task is running; wait for completion')
        runs=list(db.scalars(select(AgentRun).where(AgentRun.task_id==task.id)))
        stamps=[task.created_at,task.updated_at]+[stamp for run in runs for stamp in (run.created_at,run.updated_at,run.started_at,run.finished_at) if stamp]
        if any((stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp)>cutoff for stamp in stamps):
            raise RuntimeError('A permitted task has recent activity; do not interrupt it')
    return tasks

if __name__=='__main__':
    allowed={UUID(value) for value in os.environ.get('STALE_TASK_IDS','').split(',') if value}
    with SessionLocal() as db:
        tasks=checked_stale_tasks(db,allowed,datetime.now(timezone.utc))
        if '--recover' in sys.argv:
            for task in tasks:
                task.status=TaskStatus.FAILED
                for run in db.scalars(select(AgentRun).where(AgentRun.task_id==task.id,AgentRun.status.in_(['running','queued']))):
                    run.status='failed'
                    run.finished_at=datetime.now(timezone.utc)
                    run.error='stale_task_recovered_during_content_studio_deploy'
                db.add(Artifact(product_id=task.product_id,task_id=task.id,kind='deployment_recovery_note',title='Восстановление зависшей задачи',content='Задача не менялась более суток и была обнаружена незавершённой перед выкладкой Content Studio. Запуск остановлен после резервного копирования. Результаты сохранены; повторный запуск требует отдельного действия пользователя.'))
            db.commit()
        print('Task preflight OK; confirmed stale jobs:',len(tasks))
