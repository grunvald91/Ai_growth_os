"""Source-grounded company video topics; no API calls, invented product claims or spend."""
from __future__ import annotations

from app.company_profile import load_company_profile, sanitized_profile


def _short(value, limit=95):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit].strip(" ,:;.")


def suggest_company_video_topics(products_root, product) -> dict:
    """Use only this product's canonical validated profile (tenant isolation)."""
    loaded = load_company_profile(products_root, product.slug)
    if loaded.profile is None:
        return {"topics": [], "profile_ready": False,
                "message": "Добавьте нишу, услуги и запросы клиентов в профиль компании."}
    profile = sanitized_profile(loaded.profile)
    identity = profile.get("identity") or {}
    offer = profile.get("offer") or {}
    customers = profile.get("customers") or {}
    niche = _short(identity.get("category") or identity.get("industry") or offer.get("category"))
    services = [
        _short(item.get("name"))
        for item in (offer.get("products_services") or [])[:8]
        if isinstance(item, dict)
    ]
    if _short(offer.get("name")):
        services.insert(0, _short(offer.get("name")))
    needs = [_short(x) for x in (customers.get("pains") or [])[:6]]
    jobs = [_short(x) for x in (customers.get("jobs_to_be_done") or [])[:6]]
    proposals = []
    def add(title, basis):
        title = _short(title, 220)
        if len(title) >= 8 and title.casefold() not in {t["topic"].casefold() for t in proposals}:
            proposals.append({"topic": title, "basis": basis})

    # Mix service explainers, audience pain, practical task and niche in one batch.
    # Never infer business outcomes, prices, statistics or unverified features.
    if services:
        add(f"Что важно уточнить перед выбором «{services[0]}»?", "Услуга из профиля компании")
    for need in needs[:3]:
        if need:
            add(f"Ситуация клиента: {need}. Как подойти к решению?", "Проблема аудитории из профиля")
    for job in jobs[:2]:
        if job:
            add(f"Пошагово: {job}", "Задача аудитории из профиля")
    for name in services[1:3]:
        if name:
            add(f"Как устроен выбор «{name}»: простое объяснение", "Услуга из профиля компании")
    if niche:
        add(f"Что уточнить при выборе услуг в сфере «{niche}»?", "Ниша компании")
        add(f"Распространённые вопросы клиентов в сфере «{niche}»", "Ниша компании")
    if services:
        add(f"Кому подходит «{services[0]}» и что спросить заранее?", "Услуга из профиля компании")
    if not proposals:
        return {"topics": [], "profile_ready": False,
                "message": "Для предложений нужны хотя бы одна услуга, ниша или запрос клиента."}
    return {"topics": proposals[:8], "profile_ready": True,
            "message": "Предложения составлены по заполненным полям профиля компании."}
