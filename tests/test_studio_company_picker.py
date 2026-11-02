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
