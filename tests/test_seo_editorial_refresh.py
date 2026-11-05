"""A rewritten live SEO article must be shorter, grounded and reviewable."""
import pytest

from app.ops.seo_editorial_refresh import (
    ALLOWED_SLUGS, assemble_reader, reader_chars, validate_refresh,
)
from app.workflows.article_contracts import HumanEditedArticle, ReaderSection, RenderedFact


def reader(sections=4):
    return HumanEditedArticle(
        language="ru", title="Как записаться к психологу онлайн",
        lead="Запись к психологу начинается с выбора специалиста и формата встречи.",
        sections=[
            ReaderSection(
                heading="Как записаться " + str(i),
                body=("Выберите удобную дату и время. Уточните формат и условия записи. " * 25)
            )
            for i in range(sections)
        ],
        closing="Проверьте условия перед подтверждением записи.",
        direct_answer="Выберите услугу, удобное время и дождитесь подтверждения записи.",
        meta_description="Пошаговое объяснение выбора психолога и оформления записи онлайн.",
        fact_renderings=[RenderedFact(
            source_id="source-1",
            text="Указанные условия можно узнать у владельца бизнеса.",
        )],
    )


def test_short_edit_is_accepted_with_original_facts_title_and_search_answer():
    old = reader()
    revised = old.model_copy(update={"sections": [
        ReaderSection(heading="Как записаться", body="Выберите специалиста, формат и время. " * 50)
    ]})
    assert reader_chars(revised) < reader_chars(old)
    validate_refresh(old, revised)
    text = assemble_reader(revised, "Посмотреть запись")
    assert "## Как записаться" in text and "Посмотреть запись" in text
    assert "Проверенные возможности" in text


@pytest.mark.parametrize("field", ["title", "fact_renderings", "direct_answer"])
def test_edit_rejects_rewritten_facts_title_or_missing_search_answer(field):
    old = reader()
    revised = old.model_copy(update={"sections": [
        ReaderSection(heading="Как записаться", body="Выберите время. " * 130)
    ]})
    if field == "title":
        revised = revised.model_copy(update={"title": "Совсем другой заголовок"})
    elif field == "fact_renderings":
        revised = revised.model_copy(update={"fact_renderings": []})
    else:
        revised = revised.model_copy(update={"direct_answer": None})
    with pytest.raises(ValueError):
        validate_refresh(old, revised)


def test_long_edit_or_missing_useful_content_cannot_be_auto_published():
    old = reader()
    with pytest.raises(ValueError, match="not_materially_shorter"):
        validate_refresh(old, old)
    tiny = old.model_copy(update={"sections": [
        ReaderSection(heading="Шаги", body="Уточните свободное время.")],
    })
    with pytest.raises(ValueError, match="essential_guidance"):
        validate_refresh(old, tiny)


def test_scope_is_only_two_owner_approved_published_slugs():
    assert ALLOWED_SLUGS == {"zapis-k-psihologu", "zapisatsya-na-manikyur-onlayn"}
