"""Resource widgets: server-side escaping, real links, and stable library filters."""
import json
from datetime import datetime, timezone
from uuid import uuid4

from app.api.studio_resource_widgets import resource_widget
from app.api.content_studio import material_card
from app.api.content_studio_activity import activity_html, activity_overview
from app.models.domain import Artifact, ArtifactStatus


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


def test_legacy_fallback_is_plain_details_with_link_outside():
    html = resource_widget(kind="material", primary_html='<a href="/studio">Просмотр</a>',
                           description="Без JavaScript карточка работает", detail_label="Подробности")
    assert html.index('</a>') < html.index('<details')
    assert '<summary>Подробности</summary>' in html
