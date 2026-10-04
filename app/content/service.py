"""Durable owner-created content jobs, independent of outreach experiment priority."""
import json
from uuid import UUID
from sqlalchemy import select
from app.models.domain import Artifact, Product, Task, TaskStatus
from app.services.artifacts import ArtifactService
from app.services.tasks import TaskService
from app.workflows.article_contracts import ArticleProductionInput

DEFAULT_TOPIC = 'Как организовать онлайн-запись в небольшом сервисном бизнесе: путь клиента и чек-лист владельца'

def queue_content(db, product_id: UUID, topic: str = DEFAULT_TOPIC, *, commit=True, dedupe_key=None, audience=None, editorial_brief=None):
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        raise LookupError('Компания не найдена')
    request = ArticleProductionInput(product_id=product.id, topic=topic, audience=audience, editorial_brief=editorial_brief,
                                    request_publication=True, produce_ready_assets=True)
    title = 'CONTENT: ' + request.topic
    if dedupe_key:
        title = dedupe_key
    task = TaskService(db).find_active_duplicate(product_id=product.id, title=title,
                                               assigned_agent='content_factory')
    if task:
        return task
    task = TaskService(db).create(product_id=product.id, title=title, assigned_agent='content_factory',
                                 status=TaskStatus.QUEUED, priority=100,
                                 description='Issue #8: SEO article, channel adaptations, YouTube Shorts and Instagram Reels production briefs. No video rendering; owner review before publication.')
    ArtifactService(db).create(product_id=product.id, task_id=task.id, kind='content_request',
                              title='Пакет контента', content=request.model_dump_json())
    if commit:
        db.commit()
    return task

def queue_render(db, article_id: UUID):
    article = db.scalar(select(Artifact).where(Artifact.id == article_id,
        Artifact.kind == 'article').with_for_update())
    if article is None:
        raise LookupError('Статья не найдена')
    scripts = list(db.scalars(select(Artifact).where(Artifact.task_id == article.task_id,
        Artifact.product_id == article.product_id, Artifact.kind == 'video_script')))
    if len(scripts) != 3:
        raise ValueError('Три сценария ещё не готовы')
    title = 'RENDER: ' + str(article.id)
    previous = db.scalar(select(Task).where(Task.product_id == article.product_id, Task.title == title,
        Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.COMPLETED])))
    if previous:
        return previous
    task = TaskService(db).create(product_id=article.product_id, title=title,
        assigned_agent='content_renderer', status=TaskStatus.QUEUED, priority=100)
    ArtifactService(db).create(product_id=article.product_id, task_id=task.id,
        kind='render_request', title='Одобрен рендер трёх роликов',
        content=json.dumps({'article_id': str(article.id), 'script_ids': [str(s.id) for s in scripts]}))
    db.commit()
    return task
