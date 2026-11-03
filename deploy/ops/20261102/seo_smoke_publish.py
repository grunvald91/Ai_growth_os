#!/usr/bin/env python3
"""One-time production canary for SEO publish + HTTP read, with guaranteed cleanup.

Synthetic critic verdict and synthetic article: this tests the publication safety
gates and public API, *not* real LLM quality or indexing. The disposable company
has no BookingBot business mapping and cannot change the real ServGo blog.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.models.domain import (
    AgentRun, Approval, Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus,
)
from app.seo.activation import SeoFactoryActivation, is_enabled, set_enabled
from app.seo.publishing import publish_if_clear, reader_hash
from app.workflows.article_contracts import ArticleArtifact


PREFIX = "seo-release-canary-"
PUBLIC_BASE = "http://127.0.0.1:8000"


def build_article(product_id, task_id, slug: str) -> ArticleArtifact:
    """A factual, short, clearly marked *technical test*, not a fake marketing article."""
    title = "Техническая проверка публикации SEO"
    lead = "Это временная техническая запись для проверки доставки статьи в публичный блог."
    section = "Система проверяет сохранение публикации, выдачу её через HTTP и последующее удаление."
    closing = "Эта тестовая запись будет удалена автоматически."
    reader = {
        "language": "ru",
        "title": title,
        "lead": lead,
        "sections": [{"heading": "Что проверяется", "body": section}],
        "closing": closing,
        "direct_answer": "Проверяется техническая выдача и удаление временной записи.",
        "meta_description": "Временная техническая проверка публикации статьи на сервере.",
    }
    stage = lambda event: {
        "event": event, "metric": "Количество действий", "attribution": "Тестовый пример"
    }
    return ArticleArtifact.model_validate({
        "product_id": str(product_id), "task_id": str(task_id),
        "decision_id": None, "experiment_id": None, "channel": "own_site",
        "topic": "Контрольная техническая публикация",
        "strategy": {
            "audience": "Техническая проверка",
            "pain": "Проверка доступности блога",
            "search_intent": "Контроль публикации",
            "angle": "Технический тест",
            "titles": [title],
            "hypothesis": "Тестовая запись должна появиться в публичной ленте",
        },
        "seo": {
            "primary_query": "seo canary " + slug,
            "title": title, "hook": lead,
            "outline": ["Проверка доставки"],
            "adaptation_notes": "Технический канареечный тест",
        },
        "draft": {
            "sections": [{"heading": "Что проверяется", "body": section, "kind": "advice"}],
            "proof_needed_notes": [],
        },
        "reader_article": reader,
        "seo_brief": {
            "plan_item_id": "a001", "primary_query": "seo canary " + slug,
            "related_queries": [], "stage": "awareness", "intent": "informational",
            "reader_problem": "Убедиться, что сервер может опубликовать и выдать статью",
            "ai_questions": ["Работает ли публичная выдача?"], "next_step": "Удалить тестовые записи",
        },
        "full_article": "\n".join([lead, section, closing]),
        "conversion": {
            "cta": "Это временная техническая публикация, действий не требуется.",
            "landing_page": {"label": "Тестовый блог", "destination": "/blog"},
            "internal_links": [{"label": "Тестовая страница", "destination": "/blog"}],
            "measurement": {
                "content": stage("Создание"), "traffic": stage("Открытие"),
                "lead": stage("Переход"), "activation": stage("Проверка"),
                "customer": stage("Завершение"),
                "activation_definition": "registration",
                "success_hypothesis": "Временная запись читается по HTTP",
            },
            "distribution": [{
                "channel": "own_site", "action": "prepare_draft",
                "rationale": "Проверка технического пути", "downstream_goal": "lead",
            }],
            "derivatives": [],
        },
    })


def perform(session_factory=SessionLocal, http_get=None) -> bool:
    """Create isolated rows, publish using the real gate, check live HTTP, delete."""
    if http_get is None:
        def http_get(path):
            return httpx.get(PUBLIC_BASE + path, timeout=15, trust_env=False)
    slug = PREFIX + uuid4().hex[:12]
    product_id = None
    base = f"/public/blog/{slug}/articles"
    try:
        with session_factory() as db:
            product = Product(name="Техническая SEO-проверка", slug=slug)
            db.add(product)
            db.commit()
            product_id = product.id
            assert set_enabled(db, product_id, enabled=True, actor="seo-release-canary")
            assert is_enabled(db, product_id)
            task = Task(
                product_id=product_id, assigned_agent="content_factory",
                title="SEO-ARTICLE: техническая проверка", status=TaskStatus.COMPLETED,
                due_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
            db.add(task)
            db.flush()
            article = build_article(product_id, task.id, slug)
            editor = AgentRun(task_id=task.id, agent_name="marketing_editor",
                              provider="synthetic-release-smoke", model="none", status="completed")
            db.add(editor)
            db.flush()
            row = Artifact(
                product_id=product_id, task_id=task.id, agent_run_id=editor.id,
                kind="article", title=article.reader_article.title,
                content=article.model_dump_json(), status=ArtifactStatus.AWAITING_APPROVAL,
            )
            db.add(row)
            db.add(Artifact(
                product_id=product_id, task_id=task.id, agent_run_id=editor.id,
                kind="seo_final_review", title="Synthetic canary-only verdict",
                content=json.dumps({"status": "no_material_issues",
                                    "reader_hash": reader_hash(article.reader_article)}),
            ))
            db.commit()
            result = publish_if_clear(db, row.id, internal_canary=True)
            if not result.published or not result.slug:
                raise AssertionError("CANARY_PUBLISH_BLOCKED: " + result.reason)
            article_slug = result.slug
            print("CANARY_PUBLICATION_COMMITTED (temporary)")
        feed = http_get(base)
        if feed.status_code != 200:
            raise AssertionError(f"CANARY_FEED_HTTP_{feed.status_code}")
        entries = feed.json().get("articles", [])
        if not any(x.get("slug") == article_slug for x in entries):
            raise AssertionError("CANARY_NOT_IN_FEED")
        detail = http_get(base + "/" + article_slug)
        if detail.status_code != 200:
            raise AssertionError(f"CANARY_ARTICLE_HTTP_{detail.status_code}")
        payload = detail.json()
        if payload.get("title") != "Техническая проверка публикации SEO":
            raise AssertionError("CANARY_CONTENT_MISMATCH")
        print("CANARY_HTTP_200_AND_CONTENT_OK")
        return True
    finally:
        if product_id is not None:
            with session_factory() as db:
                stored = db.get(Product, product_id)
                if stored is not None and stored.slug == slug and slug.startswith(PREFIX):
                    # Only rows for the UUID created above; never touch real clients.
                    db.execute(delete(Publication).where(Publication.product_id == product_id))
                    db.execute(delete(Approval).where(Approval.product_id == product_id))
                    db.execute(delete(Artifact).where(Artifact.product_id == product_id))
                    db.execute(delete(AgentRun).where(AgentRun.task_id.in_(
                        select(Task.id).where(Task.product_id == product_id))))
                    db.execute(delete(Task).where(Task.product_id == product_id))
                    db.execute(delete(SeoFactoryActivation).where(
                        SeoFactoryActivation.product_id == product_id))
                    db.execute(delete(Product).where(Product.id == product_id, Product.slug == slug))
                    db.commit()
                    print("CANARY_RECORDS_REMOVED")
            removed = http_get(base)
            if removed.status_code != 404:
                raise AssertionError(f"CANARY_NOT_REMOVED_HTTP_{removed.status_code}")
            print("CANARY_HTTP_404_AFTER_CLEANUP")


if __name__ == "__main__":
    if sys.argv[1:] != ["--execute"]:
        raise SystemExit("Use --execute; synthetic canary affects a temporary company only")
    if not perform():
        raise SystemExit(1)
