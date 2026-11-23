"""Actual-image visual critic for VK posts: no network billing in tests."""
import hashlib
import io
import json

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.content.creative_images import image_folder
from app.content.vk_image_critic import (
    REVIEW_POLICY, VKImageIssue, VKImageReview, VKImageVisionReviewer,
    critic_profile, critic_prompt, review_vk_post_image, verified_vk_image_approval,
)
from app.core.config import Settings
from app.models.domain import Artifact, Product, Task
from app.services.artifacts import ArtifactService


def _setup(db, tmp_path):
    product = Product(name="Тестовая мастерская", slug="vk-critic-review")
    db.add(product)
    db.flush()
    task = Task(product_id=product.id, assigned_agent="content_factory",
                title="CONTENT: post:vk:critic", status="running")
    db.add(task)
    db.flush()
    data = io.BytesIO()
    Image.new("RGB", (1080, 1350), (20, 80, 130)).save(data, "PNG")
    picture = data.getvalue()
    path = image_folder(tmp_path, product.id, task.id)
    path.mkdir(parents=True)
    (path / "frame-01.png").write_bytes(picture)
    digest = hashlib.sha256(picture).hexdigest()
    asset = ArtifactService(db).create(
        product_id=product.id, task_id=task.id, kind="generated_image",
        title="Обложка VK", content=json.dumps({
            "filename": "frame-01.png", "frame_number": 1,
            "ratio": "4:5", "model": "gemini-3.1-flash-image", "sha256": digest,
        }), status="draft",
    )
    post = ArtifactService(db).create(
        product_id=product.id, task_id=task.id, kind="channel_text",
        title="Как вернуть клиентов",
        content=json.dumps({
            "channel": "vk", "title": "Как вернуть клиентов",
            "body": "Как мастерская отвечает на пропущенные обращения",
            "image_model": "gemini", "image_state": "awaiting_review",
            "image_review_state": "pending",
            "image_artifact_id": str(asset.id), "image_sha256": digest,
        }, ensure_ascii=False),
        status="awaiting_approval",
    )
    db.commit()
    return product, task, post, asset


class FakeReviewer:
    def __init__(self, report=None, failure=None):
        self.calls = 0
        self.report, self.failure = report, failure

    async def review(self, image, *, company_name, title, body):
        self.calls += 1
        assert image[:8] == b"\x89PNG\r\n\x1a\n"
        assert company_name == "Тестовая мастерская"
        assert "клиентов" in title
        assert "обращения" in body
        if self.failure:
            raise self.failure
        return self.report


def report(*issues):
    return VKImageReview(summary="Изображение проверено по смыслу и качеству.",
                         issues=list(issues))


def issue(*, severity, confidence="high", category="visual_defect"):
    return VKImageIssue(category=category, severity=severity,
                        confidence=confidence,
                        evidence="У фигуры виден небольшой дефект на краю кадра")


@pytest.mark.asyncio
async def test_minor_and_uncertain_issues_pass_without_redraw(db, tmp_path):
    product, task, post, asset = _setup(db, tmp_path)
    settings = Settings(_env_file=None, products_root=tmp_path, xai_api_key="fake")
    reviewer = FakeReviewer(report(
        issue(severity="minor"),
        issue(severity="critical", confidence="uncertain"),
        issue(severity="critical", category="style"),
    ))
    first = await review_vk_post_image(db, settings, product=product, task=task,
                                       post=post, reviewer=reviewer)
    second = await review_vk_post_image(db, settings, product=product, task=task,
                                        post=post, reviewer=reviewer)
    assert first == second == "approved"
    assert reviewer.calls == 1
    data = json.loads(post.content)
    assert data["image_state"] == "ready" and data["image_review_state"] == "approved"
    assert data["image_critical_issues"] == []
    assert verified_vk_image_approval(db, post, asset) is True
    audit = db.scalars(select(Artifact).where(
        Artifact.product_id == product.id, Artifact.kind == "vk_image_critique")).all()
    assert len(audit) == 1
    assert json.loads(audit[0].content)["policy"] == REVIEW_POLICY
    from app.api.content_studio import material_detail_page
    page = material_detail_page(db, product, post, root=tmp_path)
    assert f"/tasks/{task.id}/images/{asset.id}" in page


@pytest.mark.asyncio
async def test_clear_critical_issue_blocks_but_retains_image_and_text(db, tmp_path):
    product, task, post, asset = _setup(db, tmp_path)
    reviewer = FakeReviewer(report(issue(severity="critical", category="contradiction")))
    state = await review_vk_post_image(
        db, Settings(_env_file=None, products_root=tmp_path, xai_api_key="fake"),
        product=product, task=task, post=post, reviewer=reviewer)
    assert state == "blocked"
    data = json.loads(post.content)
    assert data["image_review_state"] == "blocked"
    assert data["image_state"] == "blocked"
    assert len(data["image_critical_issues"]) == 1
    assert verified_vk_image_approval(db, post, asset) is False
    from app.api.content_studio import material_detail_page
    assert "Критик заблокировал изображение" in material_detail_page(
        db, product, post, root=tmp_path)
    assert (image_folder(tmp_path, product.id, task.id) / "frame-01.png").is_file()


@pytest.mark.asyncio
async def test_critic_failure_marks_unknown_and_does_not_double_bill(db, tmp_path):
    product, task, post, asset = _setup(db, tmp_path)
    settings = Settings(_env_file=None, products_root=tmp_path, xai_api_key="fake")
    reviewer = FakeReviewer(failure=RuntimeError("provider timeout"))
    assert await review_vk_post_image(db, settings, product=product, task=task,
                                      post=post, reviewer=reviewer) == "unavailable"
    new_reviewer = FakeReviewer(report())
    assert await review_vk_post_image(db, settings, product=product, task=task,
                                      post=post, reviewer=new_reviewer) == "unavailable"
    assert reviewer.calls == 1 and new_reviewer.calls == 0
    assert json.loads(post.content)["image_state"] == "review_unavailable"
    assert verified_vk_image_approval(db, post, asset) is False
    from app.api.content_studio import material_detail_page
    assert "не подтвердил качество" in material_detail_page(
        db, product, post, root=tmp_path)
    intents = db.scalars(select(Artifact).where(
        Artifact.product_id == product.id,
        Artifact.kind == "vk_image_critique_intent")).all()
    assert len(intents) == 1
    assert not db.scalars(select(Artifact).where(
        Artifact.product_id == product.id,
        Artifact.kind == "vk_image_critique")).all()


@pytest.mark.asyncio
async def test_critic_uses_real_image_payload_on_existing_claudehub_endpoint():
    requests = []
    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(report(issue(severity="minor")).model_dump())
        }}]})
    async with httpx.AsyncClient(base_url="https://api.claudehub.fun/v1/",
                                 transport=httpx.MockTransport(handler)) as client:
        agent = VKImageVisionReviewer(
            Settings(_env_file=None, xai_api_key="fake"), client=client)
        result = await agent.review(
            b"\x89PNG\r\n\x1a\n" + b"fake-content",
            company_name="Бизнес", title="Почему теряют клиентов",
            body="Пропущенные заявки и возможности записи")
    assert not result.blocked
    assert len(requests) == 1
    body = requests[0]["messages"][0]["content"]
    assert body[1]["type"] == "image_url"
    assert body[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "Пропущенные заявки" in body[0]["text"]


def test_no_critic_endpoint_fails_before_paid_image_generation():
    with pytest.raises(ValueError, match="ключ"):
        critic_profile(Settings(_env_file=None, xai_api_key=None,
                                openai_api_key=None))

def test_ready_flag_without_persisted_review_is_rejected(db, tmp_path):
    product, task, post, image = _setup(db, tmp_path)
    data = json.loads(post.content)
    data.update(image_state="ready", image_review_state="approved",
                image_review_artifact_id="c826c074-0acb-4fd4-ae65-054e5fc61366")
    post.content = json.dumps(data)
    db.commit()
    assert not verified_vk_image_approval(db, post, image)
    from app.api.content_studio import material_detail_page
    page = material_detail_page(db, product, post, root=tmp_path)
    assert "Публикацию нужно проверить" in page


def test_invented_readable_contacts_are_critical_but_decorative_glyphs_are_not():
    """A screenshot must never silently invent the customer's contact details."""
    guidance = critic_prompt(
        company_name="Мастерская", title="Пропущенные звонки",
        body="Мастер занят, пока клиент ожидает ответа на своё обращение."
    )
    assert "phone number, email address, website URL" in guidance
    assert "a CRITICAL misleading claim" in guidance
    contact = VKImageIssue(
        category="misleading", severity="critical", confidence="high",
        evidence="На экране телефона показан читаемый номер, отсутствующий в тексте."
    )
    cosmetic = VKImageIssue(
        category="text", severity="minor", confidence="high",
        evidence="На экране телефона неразборчивые декоративные пиктограммы."
    )
    uncertain = VKImageIssue(
        category="misleading", severity="critical", confidence="uncertain",
        evidence="Есть сомнение, что на экране изображён телефонный номер."
    )
    assert VKImageReview(summary="Номер отсутствует в исходном посте.", issues=[contact]).blocked
    assert not VKImageReview(summary="Декоративные знаки не мешают.", issues=[cosmetic, uncertain]).blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["blocked", "review_unavailable"])
async def test_owner_explicitly_accepts_existing_vk_image_with_immutable_audit(
    db, tmp_path, outcome
):
    from app.content.review import approve_material, ReviewError
    from app.content.vk_image_critic import _post_digest
    from app.api.content_studio import material_detail_page
    from app.models.domain import ArtifactStatus, Approval

    product, task, post, image = _setup(db, tmp_path)
    settings = Settings(_env_file=None, products_root=tmp_path, xai_api_key="fake")
    if outcome == "blocked":
        await review_vk_post_image(
            db, settings, product=product, task=task, post=post,
            reviewer=FakeReviewer(report(issue(severity="critical", category="contradiction"))))
    else:
        await review_vk_post_image(
            db, settings, product=product, task=task, post=post,
            reviewer=FakeReviewer(failure=RuntimeError("offline reviewer unavailable")))
    before = json.loads(post.content)
    assert before["image_state"] == outcome
    with pytest.raises(ReviewError):
        approve_material(db, tmp_path, product.id, post.id)

    page = material_detail_page(db, product, post, root=tmp_path)
    assert "Утвердить картинку и пост" in page
    assert 'name="image_confirmed"' in page
    assert f"/tasks/{task.id}/images/{image.id}" in page

    # A forged/stale acknowledgement must not approve a different image/text.
    with pytest.raises(ReviewError):
        approve_material(
            db, tmp_path, product.id, post.id,
            image_confirmed=True,
            expected_image_sha256=before["image_sha256"],
            expected_post_sha256="0" * 64)
    db.refresh(post)
    assert post.status == ArtifactStatus.AWAITING_APPROVAL
    assert db.scalars(select(Artifact).where(
        Artifact.kind == "vk_image_owner_approval")).first() is None

    approved = approve_material(
        db, tmp_path, product.id, post.id,
        image_confirmed=True,
        expected_image_sha256=before["image_sha256"],
        expected_post_sha256=_post_digest(before),
        actor="local-owner",
    )
    assert approved.status == ArtifactStatus.APPROVED
    after = json.loads(approved.content)
    assert after["image_review_state"] == "owner_approved"
    assert after["image_state"] == "ready"
    assert after["image_critical_issues"] == before.get("image_critical_issues", [])
    assert verified_vk_image_approval(db, approved, image)
    decision = db.get(Artifact, __import__("uuid").UUID(after["image_owner_approval_artifact_id"]))
    assert decision is not None
    details = json.loads(decision.content)
    assert details["post_artifact_id"] == str(post.id)
    assert details["image_sha256"] == before["image_sha256"]
    assert details["post_sha256"] == _post_digest(after)
    assert details["critic_state_before_override"] == outcome
    original = db.scalars(select(Artifact).where(
        Artifact.kind == "vk_image_critique")).all()
    assert len(original) == (1 if outcome == "blocked" else 0)
    approvals = db.scalars(select(Approval).where(
        Approval.artifact_id == post.id)).all()
    assert len(approvals) == 1
    assert approvals[0].action_type == "studio:approve_vk_owner_image"
    assert approve_material(db, tmp_path, product.id, post.id) == approved
    assert len(db.scalars(select(Approval).where(
        Approval.artifact_id == post.id)).all()) == 1


@pytest.mark.asyncio
async def test_owner_cannot_approve_modified_or_missing_vk_image(db, tmp_path):
    from app.content.review import approve_material, ReviewError
    from app.content.vk_image_critic import _post_digest
    product, task, post, image = _setup(db, tmp_path)
    await review_vk_post_image(
        db, Settings(_env_file=None, products_root=tmp_path, xai_api_key="fake"),
        product=product, task=task, post=post,
        reviewer=FakeReviewer(report(issue(severity="critical", category="misleading"))))
    before = json.loads(post.content)
    path = image_folder(tmp_path, product.id, task.id) / "frame-01.png"
    path.write_bytes(b"tampered")
    with pytest.raises(ReviewError):
        approve_material(
            db, tmp_path, product.id, post.id, image_confirmed=True,
            expected_image_sha256=before["image_sha256"],
            expected_post_sha256=_post_digest(before))
    db.refresh(post)
    assert json.loads(post.content)["image_state"] == "blocked"
    assert verified_vk_image_approval(db, post, image) is False
