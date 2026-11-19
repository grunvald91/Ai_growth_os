"""Resource widgets: server-side escaping, real links, and stable library filters."""
import json
from datetime import datetime, timezone
from uuid import uuid4

from app.api.studio_resource_widgets import resource_widget
from app.api.content_studio import material_card
from app.api.content_studio_activity import activity_html, activity_overview
from app.models.domain import Artifact, ArtifactStatus
from app.dispatcher.models import DispatcherMessage
from app.dispatcher.service import to_dict


def test_shared_widget_preserves_server_owned_link_and_escapes_text():
    markup = resource_widget(
        kind="task", primary_html='<a href="/studio/products/x/work">Открыть</a>',
        description='<img src=x onerror=alert(1)>',
        note='<script>alert(1)</script>', detail_label='Подробнее <опасно>',
    )
    assert 'data-sg-resource-widget="task"' in markup
    assert '<details class="sg-resource-details" data-sg-resource-details>' in markup
    assert '<a href="/studio/products/x/work">' in markup
    assert '<img' not in markup and '<script>' not in markup
    assert '&lt;img src=x onerror=alert(1)&gt;' in markup
    assert '&lt;script&gt;' in markup
    assert '&lt;опасно&gt;' in markup


def test_widget_filter_attributes_are_escaped_and_invalid_kind_rejected():
    markup = resource_widget(
        kind="material", primary_html="<a>Материал</a>",
        description="Описание",
        filter_kind='post" onmouseover="alert(1)',
        search_text='<svg onload=alert(1)>',
    )
    assert 'data-material' in markup
    assert 'data-kind="post&quot; onmouseover=&quot;alert(1)"' in markup
    assert '&lt;svg onload=alert(1)&gt;' in markup
    assert '<svg' not in markup
    import pytest
    with pytest.raises(ValueError):
        resource_widget(kind="delete", primary_html="", description="nope")


def test_material_library_card_uses_same_widget_and_actual_scoped_route():
    product_id, artifact_id = uuid4(), uuid4()
    record = Artifact(
        id=artifact_id, product_id=product_id,
        kind="channel_text", title='Новая тема <script>bad()</script>',
        content=json.dumps({"body": "Проверенный материал о записи. <img src=x>"},
                           ensure_ascii=False),
        status=ArtifactStatus.AWAITING_APPROVAL,
    )
    record.created_at = datetime.now(timezone.utc)
    rendered = material_card(product_id, record)
    assert 'data-sg-resource-widget="material"' in rendered
    assert 'data-material data-kind="post"' in rendered
    assert 'data-search="' in rendered
    assert f'href="/studio/products/{product_id}/materials/{artifact_id}"' in rendered
    assert rendered.count("<details") == 1
    assert '<script>' not in rendered
    assert '<img' not in rendered


def test_task_widgets_share_contract_on_work_and_overview():
    product_id = uuid4()
    item = {
        "id": str(uuid4()), "agent": "content_factory", "title": "Подготовка поста",
        "state": "running", "tone": "running", "label": "Создаётся",
        "description": "Работа идёт, результат ещё не готов.",
        "artifacts": [], "animator_stage": None, "animator_calls": None,
    }
    work = activity_html(product_id, [item])
    overview = activity_overview(product_id, [item])
    for markup in (work, overview):
        assert 'data-sg-resource-widget="task"' in markup
        assert 'data-sg-resource-details' in markup
        assert f'href="/studio/products/{product_id}/tasks/{item["id"]}"' in markup
        assert "Работа идёт, результат ещё не готов." in markup
        assert '<button type="submit"' not in markup


def test_task_result_status_is_safe_and_scoped_to_matching_id():
    task_id = uuid4()
    message = DispatcherMessage(
        id=uuid4(), seq=3, role="assistant", input_mode="text",
        text="Работа завершена", blocks=[],
        capability="task_result", task_id=task_id,
        params={"status": "completed", "secret": "never-expose-this"},
    )
    result = to_dict(message)
    assert result["task_result"] == {"task_id": str(task_id), "status": "completed"}
    assert "secret" not in str(result)
    message.capability = "other"
    assert "task_result" not in to_dict(message)
    message.capability = "task_result"
    message.params = {"status": "running"}
    assert "task_result" not in to_dict(message)


def test_workflow_block_validates_nested_safe_links_and_never_embeds_confirmations():
    from app.dispatcher.blocks import clean_blocks
    from pydantic import ValidationError

    result = clean_blocks([{
        "type": "workflow", "title": "Результаты",
        "steps": [
            {"title": "Работа", "status": "completed", "detail": "Проверено",
             "blocks": [{"type": "links", "items": [
                 {"label": "Задачи", "href": "/studio/products/abc/work"}]}]},
            {"title": "План", "status": "skipped", "detail": "Пропущено", "blocks": []},
        ],
    }])
    assert len(result) == 1 and result[0]["type"] == "workflow"
    assert len(result[0]["steps"]) == 2
    blocked = clean_blocks([{
        "type": "workflow", "title": "Чужие данные",
        "steps": [
            {"title": "Работа", "status": "completed", "detail": "Ок",
             "blocks": [{"type": "links", "items": [
                 {"label": "Опасно", "href": "https://evil.example/steal"}]}]},
            {"title": "План", "status": "completed", "detail": "Ок", "blocks": []},
        ],
    }])
    assert blocked == []
    write = clean_blocks([{
        "type": "workflow", "title": "Подтверждение",
        "steps": [
            {"title": "1", "status": "completed", "detail": "Ок",
             "blocks": [{"type": "confirm", "confirmation_id": "forged", "title": "Оплатить"}]},
            {"title": "2", "status": "completed", "detail": "Ок", "blocks": []},
        ],
    }])
    assert write == []


def test_workflow_review_link_is_validated_and_cannot_embed_foreign_site():
    from app.dispatcher.blocks import clean_blocks

    def workflow(href):
        return {"type": "workflow", "title": "Post reviews", "steps": [
            {"kind": "queued_post", "title": "Post", "status": "completed",
             "task_status": "completed", "review_href": href,
             "detail": "Queued", "blocks": []},
            {"kind": "read", "title": "Read", "status": "completed",
             "detail": "Done", "blocks": []},
        ]}
    allowed = clean_blocks([workflow("/studio/products/demo/materials/a")])
    assert len(allowed) == 1
    assert allowed[0]["steps"][0]["review_href"] == "/studio/products/demo/materials/a"
    assert clean_blocks([workflow("https://evil.example/approval")]) == []
    assert clean_blocks([workflow("//evil.example/approval")]) == []
    assert clean_blocks([workflow("/studio\\\\evil.example/approval")]) == []


def test_shared_resource_wrapper_accepts_workflow_kind_with_native_fallback():
    markup = resource_widget(kind="workflow", primary_html="<strong>Шаги</strong>",
                             description="Результат без JavaScript")
    assert 'data-sg-resource-widget="workflow"' in markup
    assert "<details" in markup
    assert "<strong>Шаги</strong>" in markup


def test_legacy_fallback_is_plain_details_with_link_outside():
    html = resource_widget(kind="material", primary_html='<a href="/studio">Просмотр</a>',
                           description="Без JavaScript карточка работает", detail_label="Подробности")
    assert html.index('</a>') < html.index('<details')
    assert '<summary>Подробности</summary>' in html
