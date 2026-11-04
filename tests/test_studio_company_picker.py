"""Regression checks for the ServGo/Growth multi-company picker and themes."""
from types import SimpleNamespace
from uuid import uuid4

from app.api.studio_company_picker import CSS, selector_html


def _company(name):
    return SimpleNamespace(id=uuid4(), name=name)


def test_picker_has_readable_theme_tokens_and_no_legacy_panel():
    first = _company("100boats — аренда яхт")
    second = _company("Авто точка")
    page = selector_html([first, second], can_link=True)
    assert "Ваши компании" in page
    assert "Выберите проект" not in page
    assert "company-856" not in page
    assert "class=\"panel hero\"" not in page
    assert f'/studio/products/{first.id}' in page
    assert f'/studio/products/{second.id}' in page
    assert "Объединить профиль с ServGo" in page
    assert 'body[data-growth-design="v4"] .sg-company-choice-copy strong' in CSS
    assert "var(--s-text)" in CSS and "var(--s-surface)" in CSS


def test_picker_escapes_company_names_and_does_not_leak_tenant_ids():
    company = _company("<script>alert('x')</script>")
    page = selector_html([company], can_add=False, can_link=False)
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page
    assert "Добавить компанию" not in page
    assert "Объединить профиль" not in page
    assert str(company.id) in page  # only as a path, not as a visible technical slug


def test_empty_picker_is_honest_and_plain():
    page = selector_html([])
    assert "Пока нет организаций" in page
    assert "Открыть студию компании" not in page


def test_v4_preserves_chooser_wrapper_and_dark_theme_selectors():
    from app.api.growth_v4 import render_v4
    from types import SimpleNamespace
    page = selector_html([_company("Тёмная тема")])
    request = SimpleNamespace(url=SimpleNamespace(path="/studio", query="choose=1"))
    wrapped = render_v4(page, request, None, "/studio?design=classic")
    assert '<div class="sg-company-picker">' in wrapped
    assert 'data-growth-design="v4"' in wrapped
    assert "var(--s-text)" in wrapped
    assert "Тёмная тема" in wrapped


def test_v4_preserves_secure_account_link_page_wrapper():
    from app.api.growth_v4 import render_v4
    from app.api.studio_accounts import _identity_html
    page = _identity_html(connected=True).body.decode("utf-8")
    request = SimpleNamespace(url=SimpleNamespace(path="/studio/identity", query=""))
    wrapped = render_v4(page, request, None, "/studio/identity?design=classic")
    assert '<div class="sg-link-page">' in wrapped
    assert 'action="/studio/identity"' in wrapped


def test_unified_picker_reuses_existing_platform_product_only_with_admin_scope():
    platform = _company("ServGo")
    tenant = _company("Авто точка")
    owner_html = selector_html([tenant], unified_user_id=7, linked_business_ids=(12,))
    assert 'id="sg-servgo-organisations"' in owner_html
    assert 'data-user-id="7"' in owner_html
    assert 'data-linked-businesses="12"' in owner_html
    assert 'data-platform-product=' not in owner_html
    assert "Подключить существующие права администратора Growth" in owner_html
    assert '/studio/assets/servgo-organisations.js' in owner_html

    admin_html = selector_html([tenant, platform], unified_user_id=7,
                               linked_business_ids=(12,), platform_product=platform)
    assert f'data-platform-product="{platform.id}"' in admin_html
    assert 'data-platform-name="ServGo"' in admin_html
    assert "Подключить существующие права администратора Growth" not in admin_html
    # Neither a new Growth product nor an extra BookingBot business is created.
    assert '/studio/new' not in admin_html


def test_legacy_picker_does_not_load_canonical_organisation_script():
    platform = _company("ServGo")
    legacy_html = selector_html([platform], can_add=True)
    assert 'id="sg-servgo-organisations"' not in legacy_html
    assert '/studio/assets/servgo-organisations.js' not in legacy_html
    assert 'Добавить компанию' in legacy_html


def test_canonical_organisation_script_takes_platform_project_from_server_only():
    from app.api.studio_servgo_org_js import JS
    assert "mount.dataset.platformProduct" in JS
    assert "mount.dataset.platformName" in JS
    assert "platformButton.href = '/studio/products/'" in JS
    assert "fetch('/api/studio/businesses'" in JS
    assert "fetch('/api/studio/ticket'" in JS
    assert "window.location.assign(url.href)" in JS
    assert "url.origin !== window.location.origin" in JS
    assert 'platformButton' in JS and 'servgo_operator' not in JS
