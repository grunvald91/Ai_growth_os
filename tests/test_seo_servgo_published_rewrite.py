"""One-time published ServGo article corrections must remain safely review-gated."""
from types import SimpleNamespace

import pytest

from app.workflows.article_contracts import HumanEditedArticle
from app.seo.revise_servgo_published import REWRITES, CTA, REVISION_NOTE, build_reader


def test_two_known_published_slugs_only():
    assert set(REWRITES) == {
        "/blog/zapis-k-psihologu",
        "/blog/zapisatsya-na-manikyur-onlayn",
    }
    assert set(CTA) == set(REWRITES)


@pytest.mark.parametrize("slug", sorted(REWRITES))
def test_editorial_rewrite_is_readable_search_article_not_fake_client_booking(slug):
    old = SimpleNamespace(reader_article=SimpleNamespace(fact_renderings=[]))
    reader = build_reader(slug, old)
    assert isinstance(reader, HumanEditedArticle)
    assert reader.language == "ru"
    assert REVISION_NOTE in reader.editorial_notes
    assert reader.direct_answer and reader.meta_description
    assert len(reader.sections) >= 3
    assert any(any(line.startswith("1. ") for line in section.body.splitlines()) for section in reader.sections)
    assert all(section.heading and section.body for section in reader.sections)
    assert "Telegram @Grunvald" in CTA[slug]
    assert "Запишитесь к психологу онлайн с помощью ServGo" not in CTA[slug]
    assert "Запишитесь на маникюр онлайн через ServGo" not in CTA[slug]


def test_rewrite_code_requires_real_critic_and_original_backup():
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "app/seo/revise_servgo_published.py"
    source = path.read_text(encoding="utf-8")
    assert "status not in CLEAN_CRITIC_STATUSES" in source
    assert "seo_article_revision_backup" in source
    assert "staged = []" in source
    assert "db.commit()" in source
    assert "with_for_update()" in source
    assert "Article changed since read-only preflight" in source
    assert "row.status != ArtifactStatus.PUBLISHED" in source
    assert "Expected exactly the two inspected published pages" in source
    assert 'parser.add_argument("--apply"' in source


def test_rewrite_applies_only_after_all_articles_reviewed():
    import inspect
    from app.seo.revise_servgo_published import run
    source = inspect.getsource(run)
    assert source.index("prepared = [await revise_one") < source.index("staged = []")
    assert source.index("staged = []") < source.index("db.commit()")
    assert "Expected exactly the two inspected published pages" in source
