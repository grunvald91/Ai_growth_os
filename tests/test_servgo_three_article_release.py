"""Safety boundaries for the owner-requested one-off SEO release."""
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.seo import servgo_three_article_release as release


@dataclass
class _Prefs:
    publications_per_day: int = 3


def test_release_refuses_global_autorun(monkeypatch):
    monkeypatch.setattr(release, "get_settings", lambda: SimpleNamespace(
        seo_autorun_enabled=True, seo_servgo_autorun_enabled=False))
    with pytest.raises(RuntimeError, match="global SEO autorun"):
        release._check_production_preconditions()
    monkeypatch.setattr(release, "get_settings", lambda: SimpleNamespace(
        seo_autorun_enabled=False, seo_servgo_autorun_enabled=True))
    with pytest.raises(RuntimeError, match="global SEO autorun"):
        release._check_production_preconditions()


def test_release_does_not_modify_global_or_other_tenant_publication_quota(monkeypatch):
    from app.seo import publishing
    saved = publishing.editorial_preferences
    original = _Prefs()
    observed = []

    def preferences(db, product_id, **kwargs):
        return original

    def publish(db, artifact_id, *, daily_limit):
        observed.append(
            (publishing.editorial_preferences(None, "servgo").publications_per_day,
             publishing.editorial_preferences(None, "other").publications_per_day,
             daily_limit)
        )
        return SimpleNamespace(published=False, reason="critic_hold", slug=None)

    monkeypatch.setattr(publishing, "editorial_preferences", preferences)
    monkeypatch.setattr(publishing, "publish_if_clear", publish)
    outcome = release._publish_for_this_release(None, "servgo", "article1")
    assert outcome.published is False
    assert observed == [(30, 3, 30)]
    assert publishing.editorial_preferences is preferences


def test_release_restores_quota_even_if_publisher_raises(monkeypatch):
    from app.seo import publishing
    original = _Prefs()

    def preferences(db, product_id, **kwargs):
        return original

    def publish(db, article, *, daily_limit):
        raise ValueError("publication blocked")

    monkeypatch.setattr(publishing, "editorial_preferences", preferences)
    monkeypatch.setattr(publishing, "publish_if_clear", publish)
    with pytest.raises(ValueError, match="publication blocked"):
        release._publish_for_this_release(None, "servgo", "article1")
    assert publishing.editorial_preferences is preferences


def test_release_targets_only_exact_old_pages_and_new_verified_query():
    assert set(release.OLD_PAGES) == {
        "/blog/zapis-k-psihologu",
        "/blog/zapisatsya-na-manikyur-onlayn",
    }
    assert release.NEW_QUERY == "ии для продвижения бизнеса"
