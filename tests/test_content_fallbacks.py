"""Company-neutral content fallback regression tests."""
from difflib import SequenceMatcher

from app.content.contracts import ReadyTextSet
from app.content.fallbacks import generic_text_fallback
from app.workflows.article_production import _platform_text_fallback_payload


def test_brand_neutral_fallback_produces_six_distinct_adaptations():
    paragraphs = [
        "Выбор букета начинается не с размера композиции, а с понимания повода и человека, "
        "которому предназначен подарок. Оттенки, аромат и условия доставки лучше уточнить заранее. ",
        "При выборе растений для дома важно учитывать естественное освещение и режим ухода. "
        "Покупателю полезнее простой список вопросов, чем обещание идеального результата. ",
        "Оформление небольшого подарка требует внимания к упаковке и пожеланиям получателя. "
        "В некоторых ситуациях достаточно одной выразительной детали вместо сложного декора. ",
        "Дальнейшие действия зависят от обстоятельств вручения и условий хранения. "
        "Полезно оставить человеку понятную памятку без необоснованных гарантий. ",
    ]
    sections = [
        {"heading": "Вопрос " + str(i), "body": (paragraphs[i] * 8)}
        for i in range(4)
    ]
    state = {
        "company_name": "Цветочная мастерская",
        "company_slug": "flower-shop",
        "prior_stage_outputs": {
            "marketing_editor": {
                "title": "Как выбрать подарок из цветов",
                "lead": "Выбирайте цветы исходя из потребностей человека, а не по шаблону.",
                "closing": "Сначала уточните важные детали и только потом выбирайте композицию.",
                "sections": sections,
            },
            "conversion_editor": {
                "cta": "Расскажите, для кого подбираете букет."
            },
        },
    }
    result = ReadyTextSet.model_validate(_platform_text_fallback_payload(state))
    assert len(result.texts) == 6
    assert {t.channel for t in result.texts} == {
        "dzen", "tenchat", "vc_ru", "telegram", "faq", "founder"
    }
    for copy in result.texts:
        assert "ServGo" not in copy.body
        assert "онлайн-запис" not in copy.body
        assert "Требуется проверка" in " ".join(copy.editorial_checks)
    assert len(result.texts[0].body) >= 2000
    assert len(result.texts[2].body) >= 2000
    assert SequenceMatcher(None, result.texts[0].body, result.texts[2].body).ratio() < .85


def test_generic_fallback_requires_a_complete_validated_source():
    try:
        generic_text_fallback({
            "company_slug": "other-business",
            "prior_stage_outputs": {
                "marketing_editor": {
                    "title": "Неоконченная статья",
                    "lead": "Начало",
                    "closing": "Конец",
                    "sections": [{"heading": "Пункт", "body": "Коротко"}],
                },
                "conversion_editor": {"cta": "Свяжитесь"},
            },
        })
    except ValueError as exc:
        assert "validated long-form" in str(exc)
    else:
        raise AssertionError("Incomplete source must not become publishable text")
