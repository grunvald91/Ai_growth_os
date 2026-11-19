"""Upgrade ONLY the owner-approved ServGo marketing profile on a shared runtime volume.

The production profile-init intentionally does not overwrite existing company profiles.
This command adds verified manual-scheduling/CRM facts without discarding custom
owner data. --apply is explicit; prior bytes are backed up with a digest.
"""
import argparse
import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path

from app.company_profile import parse_company_profile


LEGACY_POSITIONING_ADDENDUM = (
    " Актуальные подтверждённые сценарии ServGo: визуальное расписание день/неделя "
    "и ручная запись клиента в админ-панели, отдельная клиентская CRM с историей "
    "записей и датой следующего контакта. Это помогает планировать "
    "повторные обращения и индивидуальную работу с клиентами. Самостоятельная онлайн-запись — "
    "дополнительная возможность, а не обязательная потребность бизнеса. "
    "Контакт в CRM сам по себе не даёт согласия на рекламную рассылку. "
    "Автоматическая передача CRM-аудиторий в Mail и реактивационные кампании "
    "не подтверждены; не описывать их как действующие."
)
POSITIONING_ADDENDUM = (
    " Актуальные сценарии ServGo: визуальное расписание день/неделя и ручная "
    "запись клиента, собственная CRM с историей визитов и датой следующего "
    "контакта. Это помогает владельцу видеть загрузку, не терять обращения "
    "и планировать повторные обращения к клиентам. Самостоятельная онлайн-запись — "
    "дополнительная возможность. Для рекламных сообщений требуется согласие "
    "получателя."
)

# Replace only exact previously distributed stock wording, never an owner's
# independently edited assertions or a different source reference.
STOCK_FACT_REPLACEMENTS = {
    "В CRM ServGo можно фиксировать заметки, категорию клиента и дату следующего контакта; автоматическая рассылка по наступлении даты не подтверждена.":
        "В CRM ServGo можно вручную фиксировать заметки, категорию клиента и дату следующего контакта.",
    "ServGo Mail поддерживает почтовые кампании, согласование писем и отказ от дальнейших писем; автоматическая синхронизация CRM с Mail не подтверждена.":
        "ServGo Mail поддерживает создание почтовых кампаний, согласование писем и отказ от дальнейших писем.",
    "В Growth реализована Студия для подготовки контента; этапы генерации зависят от доступности ИИ-провайдера, результат проходит редакторскую проверку. Надёжность всего конвейера и сквозная атрибуция не подтверждены.":
        "В ServGo Growth реализована Студия для подготовки контента и его редакторской проверки.",
    "SEO-завод собирает поисковые подсказки из Яндекса и Google, создаёт семантические кластеры и планы по компании. Недельный авторежим реализован для всех включивших SEO компаний, но глобально выключен до приёмки; статистика частотности и гарантированное привлечение лидов не подтверждены.":
        "SEO-завод собирает поисковые подсказки из Яндекса и Google, создаёт семантические кластеры и планы публикаций для конкретной компании.",
}

STOCK_OFFER_REPLACEMENTS = {
    "У каждой организации есть собственный список контактов; включаются клиенты с записями и контакты, добавленные вручную. Видны количество и история записей, последние и будущие визиты, заметки, статусы и дата следующего контакта. Автоматическая синхронизация CRM с Mail и автоматические повторные рассылки не подтверждены.":
        "У каждой организации есть собственный список контактов; включаются клиенты с записями и контакты, добавленные вручную. Видны количество и история записей, последние и будущие визиты, заметки, статусы и дата следующего контакта.",
    "Реализованы создание кампаний, управление получателями, подготовка и согласование рассылки, запуск/остановка и отказ от писем. Полная проверка доставки и конфигурации каждого отправителя требуется отдельно. Нельзя приписывать автоматическую синхронизацию с CRM, автоматический возврат клиентов или гарантированную доставляемость.":
        "Реализованы создание кампаний, управление получателями, подготовка и согласование рассылки, запуск/остановка и отказ от писем. Параметры конкретного отправителя и доставку писем необходимо проверять отдельно.",
}
LEGACY_POSITIONING_TAIL = (
    "Почтовые кампании существуют отдельно в ServGo Mail, но интеграция аудиторий из CRM в Mail не подтверждена; "
    "для рекламных сообщений требуется предварительное согласие адресата и соблюдение отказов; "
    "персональные предложения возможны только получателям, согласившимся на такую коммуникацию. "
    "Автоматическое возвращение клиентов, AI-анализ CRM и сквозная атрибуция остаются направлением развития, "
    "а не проверенными готовыми функциями."
)
NEW_POSITIONING_TAIL = (
    "Почтовые кампании доступны как отдельный инструмент ServGo Mail. "
    "Рекламные сообщения следует направлять только с учётом согласия адресата "
    "и возможности отказаться от рассылки."
)


def merge_profile(current: dict, packaged: dict) -> tuple[dict, dict]:
    """Make only additive, traceable product updates; preserve owner-specific fields."""
    old = parse_company_profile(current).model_dump(mode="json")
    new = parse_company_profile(packaged).model_dump(mode="json")
    merged = json.loads(json.dumps(old, ensure_ascii=False))
    # Replace ONLY previous stock descriptions, preserving owner-written copy.
    old_one_liner = "ServGo is an online service booking/reservation system for small service businesses."
    old_category = "B2B SaaS / online booking for service businesses"
    if merged["identity"].get("one_liner") == old_one_liner:
        merged["identity"]["one_liner"] = new["identity"]["one_liner"]
    if merged["identity"].get("category") == old_category:
        merged["identity"]["category"] = new["identity"]["category"]

    added = {"offers": 0, "facts": 0, "audience": 0, "positioning": False,
             "catalog_fields": 0, "unknowns": 0, "stock_corrections": 0}
    offers = merged["offer"]["products_services"]
    offer_by_name = {v["name"]: v for v in offers}
    # Only catalogue first-party ServGo modules from the tested and versioned
    # package, never overwrite owner-written descriptions, pricing or a status
    # that the owner has explicitly selected. Other tenants are untouched.
    approved_families = {"BookingBot", "Growth", "Mail", "Смена", "SEO-завод"}
    for packaged_offer in new["offer"]["products_services"]:
        if packaged_offer.get("product_family") not in approved_families:
            continue
        existing_offer = offer_by_name.get(packaged_offer["name"])
        if existing_offer is None:
            offers.append(packaged_offer)
            offer_by_name[packaged_offer["name"]] = packaged_offer
            added["offers"] += 1
            continue
        for field in ("product_family", "release_status", "evidence_refs",
                      "buyer_segment", "conversion_action"):
            if existing_offer.get(field) in (None, "", []) and packaged_offer.get(field):
                existing_offer[field] = packaged_offer[field]
                added["catalog_fields"] += 1
        # The previous package mislabeled a working module as unavailable.
        # Change only that exact stock record; never overwrite the owner's
        # custom text or custom release-status evidence. This transition is
        # explicitly backed by the owner's first-party confirmation.
        old_description = existing_offer.get("description", "")
        replacement = STOCK_OFFER_REPLACEMENTS.get(old_description)
        if (replacement is not None and
            replacement == packaged_offer.get("description")):
            existing_offer["description"] = replacement
            added["stock_corrections"] += 1
        if (packaged_offer["product_family"] == "Смена"
            and packaged_offer.get("release_status") == "live"
            and "owner-confirmation:servgo:2026-11-03:shifts-operational"
                in packaged_offer.get("evidence_refs", [])
            and existing_offer.get("release_status") == "unverified"
            and existing_offer.get("evidence_refs") in ([], packaged_offer.get("evidence_refs"))
            and existing_offer.get("description", "").startswith(
                "Модуль учёта смен рассматривается как направление платформы.")
        ):
            for field in ("description", "release_status", "evidence_refs",
                          "conversion_action"):
                existing_offer[field] = packaged_offer[field]
            added["stock_corrections"] += 1
        if (packaged_offer["product_family"] == "SEO-завод"
            and "общий переключатель по умолчанию отключён" in packaged_offer["description"]
            and existing_offer.get("description", "").startswith(
                "В Growth реализованы отдельное включение SEO для каждой компании")
            and "Автоматический еженедельный запуск пока привязан к ServGo" in
                existing_offer.get("description", "")
        ):
            existing_offer["description"] = packaged_offer["description"]
            added["stock_corrections"] += 1


    facts = merged["proof"]["approved_facts"]
    present = {f["statement"] for f in facts}
    verified_paths = ("SchedulePage.tsx", "web/api/routes/crm.py", "mail_campaign_",
                      "Ai_growth_os/app/api/content_studio.py", "Ai_growth_os/app/seo/suggest.py",
                      "owner-confirmation:servgo:2026-11-03:shifts-operational")
    # Migrate exact historic stock statements on the persistent runtime
    # volume. Adding the revised fact without replacing its predecessor would
    # leave the "не подтверждена" sentence available to article generation.
    approved_by_statement = {f["statement"]: f for f in new["proof"]["approved_facts"]}
    for old_fact in facts:
        replacement = STOCK_FACT_REPLACEMENTS.get(old_fact.get("statement"))
        expected = approved_by_statement.get(replacement) if replacement else None
        if expected and old_fact.get("evidence_ref") == expected["evidence_ref"]:
            old_fact["statement"] = replacement
            added["stock_corrections"] += 1
    present = {f["statement"] for f in facts}
    for fact in new["proof"]["approved_facts"]:
        evidence = str(fact.get("evidence_ref") or "")
        if any(part in evidence for part in verified_paths) and fact["statement"] not in present:
            facts.append(fact)
            present.add(fact["statement"])
            added["facts"] += 1

    # Previous packaged proof said autorun is ServGo-only. Correct only that
    # exact obsolete statement, preserving all custom owner-authored facts.
    old_autorun = "Недельный авторежим сейчас настроен только для ServGo;"
    new_autorun = "Недельный авторежим реализован для всех включивших SEO компаний, но глобально выключен до приёмки;"
    for fact in facts:
        statement = fact.get("statement", "")
        if old_autorun in statement:
            candidate = statement.replace(old_autorun, new_autorun)
            if any(x.get("statement") == candidate for x in new["proof"]["approved_facts"]):
                fact["statement"] = candidate
                added["stock_corrections"] += 1
    # Avoid duplicate proof after replacing the previous stock assertion.
    deduplicated = []
    seen_facts = set()
    for fact in facts:
        key = fact["statement"]
        if key not in seen_facts:
            deduplicated.append(fact)
            seen_facts.add(key)
    merged["proof"]["approved_facts"] = deduplicated

    for key in ("primary_segments", "pains", "jobs_to_be_done"):
        values = merged["customers"][key]
        for value in new["customers"][key]:
            if any(term in value.casefold() for term in (
                "ручн", "журнал", "crm", "баз", "повторн", "персональн"
            )) and value not in values:
                values.append(value)
                added["audience"] += 1

    original_positioning = str(merged["brand"].get("positioning") or "")
    if LEGACY_POSITIONING_TAIL in original_positioning:
        original_positioning = original_positioning.replace(
            LEGACY_POSITIONING_TAIL, NEW_POSITIONING_TAIL)
        added["stock_corrections"] += 1
    if LEGACY_POSITIONING_ADDENDUM.strip() in original_positioning:
        original_positioning = original_positioning.replace(
            LEGACY_POSITIONING_ADDENDUM.strip(), POSITIONING_ADDENDUM.strip())
        added["stock_corrections"] += 1
    if POSITIONING_ADDENDUM.strip() not in original_positioning:
        merged["brand"]["positioning"] = (
            original_positioning.rstrip() + POSITIONING_ADDENDUM
        ).strip()
        added["positioning"] = True
    elif original_positioning != str(merged["brand"].get("positioning") or ""):
        merged["brand"]["positioning"] = original_positioning

    # Keep risk boundaries in non-public profile policy, never as confirmed
    # feature claims. Preserve owner-authored rules and unknown questions.
    for rule in new["constraints"]["operational"]:
        if rule.startswith("Не ") and rule not in merged["constraints"]["operational"]:
            merged["constraints"]["operational"].append(rule)
            added["stock_corrections"] += 1
    for claim in new["brand"]["prohibited_claims"]:
        if claim.startswith("Unverified automatic CRM") and claim not in merged["brand"]["prohibited_claims"]:
            merged["brand"]["prohibited_claims"].append(claim)
            added["stock_corrections"] += 1
    for question in new["unknowns"]:
        if ("автоматическая отправка сообщения из CRM" in question["question"]
                and question not in merged["unknowns"]):
            merged["unknowns"].append(question)
            added["unknowns"] += 1

    # Remove only the exact obsolete question introduced by the previous
    # package, and retain independent questions written by the owner.
    stale = ("Проверен ли боевой выпуск «Смены», включая Telegram mini-app, "
             "QR/selfie и учёт рабочего времени? Без подтверждённых исходников "
             "и smoke-теста не рекламировать как действующий модуль.")
    if any(q["question"] == stale for q in merged["unknowns"]):
        merged["unknowns"] = [q for q in merged["unknowns"] if q["question"] != stale]
        added["stock_corrections"] += 1
    for question in new.get("unknowns", []):
        if ("Какие конкретные возможности действующей «Смены»" in question["question"]
            and question not in merged["unknowns"]):
            merged["unknowns"].append(question)
            added["unknowns"] += 1

    # Do not replace the owner's one_liner, pricing, service segments, contact
    # address, brand voice, hypotheses, metrics or source history.
    return parse_company_profile(merged).model_dump(mode="json"), added


def upgrade(target: Path, packaged: Path, *, apply: bool, expected_sha256: str | None = None) -> dict:
    target, packaged = Path(target), Path(packaged)
    if target.is_symlink() or target.parent.is_symlink() or packaged.is_symlink():
        raise ValueError("Profile symlink is forbidden")
    if not target.is_file() or not packaged.is_file():
        raise FileNotFoundError("Both live and packaged ServGo profiles must exist")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target.parent / ".profile.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        before = target.read_bytes()
        sha = hashlib.sha256(before).hexdigest()
        if apply and expected_sha256 is not None and sha != expected_sha256:
            raise ValueError("Live ServGo profile changed since preflight")
        data = json.loads(before.decode("utf-8"))
        reference = json.loads(packaged.read_text(encoding="utf-8"))
        merged, stats = merge_profile(data, reference)
        changed = merged != parse_company_profile(data).model_dump(mode="json")
        result = {"profile": "servgo", "changed": changed, "additions": stats,
                  "previous_sha256": sha, "applied": False}
        if not apply or not changed:
            return result

        backup = target.parent / ("company-profile.pre-seo-20261103-" + sha[:12] + ".json")
        if not backup.exists():
            backup_fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(backup_fd, "wb") as out:
                out.write(before)
                out.flush()
                os.fsync(out.fileno())
        elif hashlib.sha256(backup.read_bytes()).hexdigest() != sha:
            raise ValueError("Existing backup does not match observed profile")

        encoded = (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(encoded) > 250_000:
            raise ValueError("Merged profile exceeds safe storage bound")
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".servgo-upgrade-",
                                             delete=False) as out:
                temp = out.name
                out.write(encoded)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, target)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
        result["applied"] = True
        result["backup"] = backup.name
        return result
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256", help="Preflight hash of the live profile; checked on --apply")
    args = parser.parse_args()
    from app.core.config import get_settings
    target = get_settings().products_root / "servgo/company-profile.json"
    reference = Path("/app/products/servgo/company-profile.json")
    print("SERVGO_PROFILE_UPGRADE=" + json.dumps(
        upgrade(target, reference, apply=args.apply, expected_sha256=args.expected_sha256), ensure_ascii=False
    ))


if __name__ == "__main__":
    main()
