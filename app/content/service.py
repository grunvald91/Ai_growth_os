"""Durable owner-created content jobs, independent of outreach experiment priority."""
import json
import hashlib
from uuid import UUID
from sqlalchemy import select
from app.models.domain import Artifact, Product, Task, TaskStatus
from app.services.artifacts import ArtifactService
from app.services.tasks import TaskService
from app.workflows.article_contracts import ArticleProductionInput
from app.content.quick_post import QuickPostRequest

DEFAULT_TOPIC = 'Как организовать онлайн-запись в небольшом сервисном бизнесе: путь клиента и чек-лист владельца'

def queue_content(db, product_id: UUID, topic: str = DEFAULT_TOPIC, *, commit=True, dedupe_key=None,
                  audience=None, editorial_brief=None, format="package", channel=None):
    """Submit full package (legacy) or a single platform post (two-agent fast path)."""
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        raise LookupError('Компания не найдена')
    if format == "post":
        request = QuickPostRequest(mode="post", product_id=product.id, topic=topic, channel=channel,
                                   audience=audience, editorial_brief=editorial_brief)
        title = f"CONTENT: post:{request.channel}:{request.topic}"
        description = "Один пост для проверки владельцем. Без автоматической публикации."
    elif format == "package":
        request = ArticleProductionInput(product_id=product.id, topic=topic, audience=audience,
                                         editorial_brief=editorial_brief,
                                         request_publication=True, produce_ready_assets=True)
        title = "CONTENT: " + request.topic
        description = ("SEO article, channel adaptations, two video scripts. "
                       "No automatic publication or video rendering.")
    else:
        raise ValueError("Unknown content format")
    if dedupe_key:
        title = dedupe_key
    else:
        fingerprint = hashlib.sha256(request.model_dump_json().encode()).hexdigest()[:16]
        title = title[:275] + ':' + fingerprint
    task = TaskService(db).find_active_duplicate(product_id=product.id, title=title,
                                               assigned_agent='content_factory')
    if task:
        return task
    task = TaskService(db).create(product_id=product.id, title=title, assigned_agent='content_factory',
                                 status=TaskStatus.QUEUED, priority=100,
                                 description=description)
    ArtifactService(db).create(product_id=product.id, task_id=task.id, kind='content_request',
                              title="Один пост" if format == "post" else "Пакет контента",
                              content=request.model_dump_json())
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
    if len(scripts) != 2:
        raise ValueError('Для рендера нужны два готовых сценария: YouTube Shorts и Instagram Reels')
    title = 'RENDER: ' + str(article.id)
    previous = db.scalar(select(Task).where(Task.product_id == article.product_id, Task.title == title,
        Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.COMPLETED])))
    if previous:
        return previous
    task = TaskService(db).create(product_id=article.product_id, title=title,
        assigned_agent='content_renderer', status=TaskStatus.QUEUED, priority=100)
    ArtifactService(db).create(product_id=article.product_id, task_id=task.id,
        kind='render_request', title='Одобрен рендер двух роликов',
        content=json.dumps({'article_id': str(article.id), 'script_ids': [str(s.id) for s in scripts]}))
    db.commit()
    return task

