"""Company-neutral safety fallback for six text channels.

Adapt only existing human-reviewed source content. Do not invent services,
prices, testimonials or product-specific promises when a derivative stage fails.
"""
from __future__ import annotations

from app.content.contracts import ReadyTextSet


def generic_text_fallback(stage_inputs: dict) -> dict:
    prior = stage_inputs.get("prior_stage_outputs") or {}
    article = prior.get("marketing_editor") or {}
    conversion = prior.get("conversion_editor") or {}
    title = " ".join(str(article.get("title") or "").split())
    lead = str(article.get("lead") or "").strip()
    closing = str(article.get("closing") or "").strip()
    cta = str(conversion.get("cta") or "").strip()
    sections = [
        (str(s.get("heading") or "").strip(), str(s.get("body") or "").strip())
        for s in (article.get("sections") or [])
        if isinstance(s, dict) and str(s.get("body") or "").strip()
    ]
    if not title or not lead or not closing or not cta or len(sections) < 4:
        raise ValueError("A validated long-form article is required for fallback")
    if len(lead + closing + "".join(s[1] for s in sections)) < 2500:
        raise ValueError("Fallback source article is incomplete")

    def section_text(rows):
        return "\n\n".join(
            (heading + "\n" if heading else "") + body
            for heading, body in rows
        )

    def extend_from_source(prefix, selected, minimum=500):
        """Add distinct remaining source sections to meet the ready-copy contract."""
        chosen = list(selected)
        body = prefix + "\n\n" + section_text(chosen)
        for section in sections:
            if len(body) >= minimum:
                break
            if section not in chosen:
                chosen.append(section)
                body = prefix + "\n\n" + section_text(chosen)
        if len(body) < minimum:
            raise ValueError("Not enough source material to adapt")
        return body.strip()

    # Differently ordered source sections prevent six copies of the same article.
    dzen = lead + "\n\n" + section_text(sections) + "\n\n" + closing + "\n\n" + cta
    vc = (title + "\n\n" + "Разберём практическую сторону вопроса: "
          "что важно учитывать, какие есть шаги и что проверить самостоятельно.\n\n"
          + section_text(list(reversed(sections))) + "\n\n" + closing + "\n\n" + cta)
    if len(dzen) < 2000 or len(vc) < 2000:
        raise ValueError("Not enough long-form source material for Dzen and vc.ru")

    tenchat = extend_from_source(
        title + "\n\n" + "Какие действия из этого разбора полезно проверить в своей работе?",
        sections[1:3],
    ) + "\n\n" + cta
    telegram = extend_from_source(
        lead,
        [sections[-1], sections[0]],
    ) + "\n\n" + cta
    faq = (
        "Вопросы по теме: " + title + "\n\n" +
        "\n\n".join(
            "Вопрос: " + heading.rstrip(".?") + "?\nОтвет: " + body
            for heading, body in sections[:4]
        ) + "\n\n" + cta
    )
    founder = extend_from_source(
        title + "\n\n" + "Точка зрения для обсуждения: какие из этих идей стоит проверить на практике?",
        [sections[2], sections[0]],
    ) + "\n\n" + cta

    checks = [
        "Резервная адаптация только из исходной статьи",
        "Требуется проверка владельцем перед публикацией",
    ]
    payload = {"texts": [
        {"channel": "dzen", "title": title, "body": dzen, "editorial_checks": checks},
        {"channel": "tenchat", "title": title, "body": tenchat, "editorial_checks": checks},
        {"channel": "vc_ru", "title": title, "body": vc, "editorial_checks": checks},
        {"channel": "telegram", "title": title, "body": telegram, "editorial_checks": checks},
        {"channel": "faq", "title": "Вопросы: " + title[:275], "body": faq, "editorial_checks": checks},
        {"channel": "founder", "title": "Авторская заметка: " + title[:270],
         "body": founder, "editorial_checks": checks},
    ]}
    # This validates lengths, supported channels and cross-channel similarity.
    return ReadyTextSet.model_validate(payload).model_dump(mode="json")
