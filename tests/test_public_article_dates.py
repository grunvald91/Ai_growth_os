"""GEO: public dates come from publication and reviewed content revisions."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.api.public_blog import public_article_dates


PUBLISHED = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def test_status_only_artifact_touch_does_not_forge_date_modified():
    art = SimpleNamespace(version=1, updated_at=PUBLISHED + timedelta(days=2))
    pub = SimpleNamespace(published_at=PUBLISHED)
    assert public_article_dates(art, pub) == (PUBLISHED.isoformat(), PUBLISHED.isoformat())


def test_substantive_approved_revision_changes_date_modified():
    edited = PUBLISHED + timedelta(days=3)
    art = SimpleNamespace(version=2, updated_at=edited)
    pub = SimpleNamespace(published_at=PUBLISHED)
    assert public_article_dates(art, pub) == (PUBLISHED.isoformat(), edited.isoformat())


def test_prepublication_revision_does_not_forge_later_edit():
    art = SimpleNamespace(version=2, updated_at=PUBLISHED - timedelta(days=1))
    pub = SimpleNamespace(published_at=PUBLISHED)
    assert public_article_dates(art, pub) == (PUBLISHED.isoformat(), PUBLISHED.isoformat())


def test_naive_test_database_timestamps_are_compared_as_utc():
    art = SimpleNamespace(version=2, updated_at=datetime(2026, 11, 18, 10))
    pub = SimpleNamespace(published_at=datetime(2026, 11, 16, 12))
    assert public_article_dates(art, pub)[1] == "2026-11-18T10:00:00"


def test_unpublished_article_never_gets_synthetic_publication_date():
    with pytest.raises(ValueError, match="missing publication timestamp"):
        public_article_dates(SimpleNamespace(version=1, updated_at=PUBLISHED),
                             SimpleNamespace(published_at=None))
