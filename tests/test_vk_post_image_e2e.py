"""Offline end-to-end contract: queue -> author -> image API -> vision API -> Studio.

Uses the real pipeline and real multimodal transport payload builders, intercepting
only external HTTP. No production records, no paid requests, no VK publication.
"""
import base64
import io
import json

import httpx
import pytest
from starlette.requests import Request
from PIL import Image
from sqlalchemy import select

from app.api.content_studio import (material_detail_page, studio_task_page,
                                    studio_material_approve, studio_material_detail,
                                    studio_creative_image)
from app.api.content_studio_ui import review_html
from app.content.quick_post import QuickPostRequest, run_quick_post
from app.content.service import queue_content
from app.content.review import ReviewError, approve_material
from app.content.vk_image_critic import verified_vk_image_approval
from app.content.whiteboard.adapters import ClaudeHubChat, ClaudeHubGeminiImageProvider
from app.core.config import Settings
from app.llm.schemas import LLMResponse, LLMUsage
from app.models.domain import Artifact, ArtifactStatus, Product, Publication, TaskStatus


class DraftModel:
    def __init__(self):
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        if self.calls == 1:
            value = {
                "audience_pain": "Владельцы небольших компаний теряют обращения, когда заняты текущей работой.",
                "hook": "Почему клиенты уходят, не дождавшись ответа",
                "useful_takeaway": "Записывать все заявки, отслеживать пропущенные сообщения, контролировать ответы.",
                "suggested_cta": "Узнайте, как удобнее принимать заявки.",
                "evidence_ids": [],
            }
        else:
            value = {
                "title": "Как не терять обращения клиентов",
                "body": (
                    "Потенциальный клиент пишет, пока специалист занят другой работой. "
                    "Если сообщение остаётся без ответа, покупатель может выбрать другое место. "
                    "Посмотрите, в какие часы поступает больше запросов. "
                    "Проверьте, кто отвечает на обращения, и не забывайте о пропущенных звонках. "
                    "Соберите обращения в понятную очередь, назначьте ответственных сотрудников. "
                    "Узнайте, как удобнее принимать заявки."
                ),
                "cta": "Узнайте, как удобнее принимать заявки.",
                "evidence_ids": [],
                "editor_notes": [],
            }
        return LLMResponse(content=json.dumps(value, ensure_ascii=False), model="fake-author",
                           usage=LLMUsage(input_tokens=100, output_tokens=120))


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict,expect_state", [
    ("minor", "ready"), ("blocking", "blocked"), ("timeout", "review_unavailable"),
])
async def test_vk_image_pipeline_end_to_end_with_provider_transport(
    db, tmp_path, monkeypatch, verdict, expect_state
):
    company = Product(name="Сервисный бизнес", slug="e2e-vk-post")
    db.add(company)
    db.commit()
    settings = Settings(_env_file=None, products_root=tmp_path,
                        xai_api_key="offline-not-a-real-key", agent_critic_enabled=False)
    task = queue_content(db, company.id, "Почему теряются заявки",
                         format="post", channel="vk",
                         image_model="gemini", image_ratio="4:5")
    request_row = db.scalar(select(Artifact).where(
        Artifact.product_id == company.id, Artifact.task_id == task.id,
        Artifact.kind == "content_request"))
    request = QuickPostRequest.model_validate_json(request_row.content)
    author = DraftModel()
    image_requests, vision_requests = [], []
    source = io.BytesIO()
    Image.new("RGB", (900, 900), (16, 75, 120)).save(source, format="PNG")
    encoded = base64.b64encode(source.getvalue()).decode("ascii")

    async def fake_image_post(self, endpoint, payload):
        assert endpoint == "images"
        image_requests.append(payload)
        assert payload["model"] == "gemini-3.1-flash-image"
        assert "обращ" in payload["prompt"].lower()
        return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

    async def fake_critic_post(self, endpoint, payload):
        assert endpoint == "chat/completions"
        vision_requests.append(payload)
        parts = payload["messages"][0]["content"]
        assert parts[1]["type"] == "image_url"
        assert "data:image/png;base64," in parts[1]["image_url"]["url"]
        image = base64.b64decode(parts[1]["image_url"]["url"].split(",", 1)[1])
        with Image.open(io.BytesIO(image)) as actual:
            assert actual.size == (1080, 1350) and actual.format == "PNG"
        assert "Как не терять обращения клиентов" in parts[0]["text"]
        if verdict == "timeout":
            raise httpx.TimeoutException("mock vision service timeout")
        issues = ([{
            "category": "contradiction", "severity": "critical", "confidence": "high",
            "evidence": "На иллюстрации видна ситуация, противоположная ключевому смыслу поста.",
        }] if verdict == "blocking" else [{
            "category": "style", "severity": "minor", "confidence": "high",
            "evidence": "Фон недостаточно выразительный, но смысл публикации сохранён.",
        }])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "summary": "Картинка проверена по основному смыслу.",
            "issues": issues,
        }, ensure_ascii=False)}}]})

    monkeypatch.setattr(ClaudeHubGeminiImageProvider, "post", fake_image_post)
    monkeypatch.setattr(ClaudeHubChat, "post", fake_critic_post)
    completed = await run_quick_post(
        db, settings, request.model_copy(update={"task_id": task.id}), provider=author)
    assert completed is True
    assert author.calls == 2 and len(image_requests) == 1 and len(vision_requests) == 1
    assert db.get(type(task), task.id).status == TaskStatus.COMPLETED

    post = db.scalar(select(Artifact).where(
        Artifact.product_id == company.id, Artifact.task_id == task.id,
        Artifact.kind == "channel_text"))
    media = db.scalar(select(Artifact).where(
        Artifact.product_id == company.id, Artifact.task_id == task.id,
        Artifact.kind == "generated_image"))
    assert post is not None and media is not None
    payload = json.loads(post.content)
    assert payload["image_state"] == expect_state
    assert verified_vk_image_approval(db, post, media) is (expect_state == "ready")
    html = material_detail_page(db, company, post, root=tmp_path)
    image_uri = f"/tasks/{task.id}/images/{media.id}"
    assert image_uri in html
    file_response = studio_creative_image(
        company.id, task.id, media.id, db=db, settings=settings)
    assert file_response.status_code == 200
    assert file_response.path.name == "frame-01.png"
    assert html.index(image_uri) < html.index('id="material-copy"'), "Image must precede post text"
    assert ('Утвердить материал</button>' in html) is (expect_state == "ready")
    assert ("Критик заблокировал изображение" in html) is (expect_state == "blocked")
    inbox = review_html(company.id, [post])
    assert image_uri in inbox, "Approval inbox must visibly include the illustration"
    assert ("Картинка отклонена" in inbox) is (expect_state == "blocked")
    if expect_state == "blocked":
        assert "ситуация, противоположная" in inbox
        assert "ситуация, противоположная" in html
        assert "Утвердить картинку и пост" in html
    if expect_state == "review_unavailable":
        assert "не подтвердил качество" in html
        assert "Утвердить картинку и пост" in html
    if expect_state == "ready":
        assert "критичных ошибок нет" in html

    page = studio_task_page(company.id, task.id, db=db, settings=settings)
    assert page.status_code == 200
    assert f"/tasks/{task.id}/images/{media.id}" in page.body.decode("utf-8")
    # Owner approval must enforce the visual decision, not just the display badge.
    if expect_state == "ready":
        approved = approve_material(db, tmp_path, company.id, post.id, facts_confirmed=True)
        assert approved.status == ArtifactStatus.APPROVED
    else:
        with pytest.raises(ReviewError, match="Изображение не согласовано"):
            approve_material(db, tmp_path, company.id, post.id, facts_confirmed=True)
        assert post.status == ArtifactStatus.AWAITING_APPROVAL
        # A failed browser form submission must return to the branded detail page,
        # not render FastAPI's raw JSON on an empty dark tab.
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        req = Request({
            "type": "http", "method": "POST", "path": "/",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        }, receive=receive)
        redirect = await studio_material_approve(company.id, post.id, req, db=db)
        assert redirect.status_code == 303
        assert redirect.headers["location"].endswith(
            f"/materials/{post.id}?approval_error=image")
        failed_page = studio_material_detail(
            company.id, post.id, approval_error="image", db=db, settings=settings)
        assert failed_page.status_code == 200
        assert "Материал не утверждён" in failed_page.body.decode("utf-8")
        assert image_uri in failed_page.body.decode("utf-8")
        # A deliberate owner click must approve the ORIGINAL media without any
        # additional image or critic billing, and keep the reviewer audit intact.
        from urllib.parse import urlencode
        from app.content.vk_image_critic import _post_digest
        accepted_form = urlencode({
            "image_confirmed": "yes",
            "facts_confirmed": "yes",
            "expected_image_sha256": payload["image_sha256"],
            "expected_post_sha256": _post_digest(payload),
        }).encode("utf-8")

        async def accepted_receive():
            return {"type": "http.request", "body": accepted_form, "more_body": False}

        accepted_request = Request({
            "type": "http", "method": "POST", "path": "/",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        }, receive=accepted_receive)
        monkeypatch.setattr("app.api.content_studio.get_settings", lambda: settings)
        accepted = await studio_material_approve(company.id, post.id, accepted_request, db=db)
        assert accepted.status_code == 303
        assert "approval_error" not in accepted.headers["location"]
        db.refresh(post)
        assert post.status == ArtifactStatus.APPROVED
        assert verified_vk_image_approval(db, post, media)
        assert len(image_requests) == len(vision_requests) == 1
    assert not list(db.scalars(select(Publication).where(
        Publication.product_id == company.id)))
    # The production path never attempts to publish a post in this task.
    kinds = [a.kind for a in db.scalars(select(Artifact).where(
        Artifact.product_id == company.id, Artifact.task_id == task.id))]
    assert kinds.count("vk_post_image_intent") == 1
    assert kinds.count("vk_image_critique_intent") == 1
    assert kinds.count("vk_image_critique") == (0 if verdict == "timeout" else 1)


@pytest.mark.asyncio
async def test_vk_image_http_failure_preserves_post_and_records_numeric_status(db, tmp_path, monkeypatch):
    company = Product(name="Сервисный бизнес", slug="e2e-vk-http-failure")
    db.add(company)
    db.commit()
    settings = Settings(_env_file=None, products_root=tmp_path,
                        xai_api_key="offline-not-a-real-key", agent_critic_enabled=False)
    task = queue_content(db, company.id, "Почему теряются заявки",
                         format="post", channel="vk", image_model="gemini", image_ratio="4:5")
    request_row = db.scalar(select(Artifact).where(
        Artifact.product_id == company.id, Artifact.task_id == task.id,
        Artifact.kind == "content_request"))
    request = QuickPostRequest.model_validate_json(request_row.content)
    calls = []
    async def fail_image(self, endpoint, payload):
        calls.append(endpoint)
        response = httpx.Response(503, request=httpx.Request(
            "POST", "https://api.claudehub.fun/v1/images/generations"))
        response.raise_for_status()
    monkeypatch.setattr(ClaudeHubGeminiImageProvider, "post", fail_image)
    author = DraftModel()
    assert not await run_quick_post(
        db, settings, request.model_copy(update={"task_id": task.id}), provider=author)
    post = db.scalar(select(Artifact).where(Artifact.task_id == task.id,
                                           Artifact.kind == "channel_text"))
    data = json.loads(post.content)
    assert data["image_state"] == "failed"
    assert data["image_error_stage"] == "generation"
    assert data["image_http_status"] == 503
    assert calls == ["images"] and author.calls == 2
    assert post.status == ArtifactStatus.AWAITING_APPROVAL
    assert data["body"]
    assert "HTTP 503" in material_detail_page(db, company, post, root=tmp_path)
