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


POSITIONING_ADDENDUM = (
    " Актуальные подтверждённые сценарии ServGo: визуальное расписание день/неделя "
    "и ручная запись клиента в админ-панели, отдельная клиентская CRM с историей "
    "записей и датой следующего контакта. Самостоятельная онлайн-запись — "
    "дополнительная возможность, а не обязательная потребность бизнеса. "
    "Контакт в CRM сам по себе не даёт согласия на рекламную рассылку. "
    "Автоматическая передача CRM-аудиторий в Mail и реактивационные кампании "
    "не подтверждены; не описывать их как действующие."
)


def merge_profile(current: dict, packaged: dict) -> tuple[dict, dict]:
    """Make only additive, traceable product updates; preserve owner-specific fields."""
    old = parse_company_profile(current).model_dump(mode="json")
    new = parse_company_profile(packaged).model_dump(mode="json")
    merged = json.loads(json.dumps(old, ensure_ascii=False))

    added = {"offers": 0, "facts": 0, "audience": 0, "positioning": False}
    offers = merged["offer"]["products_services"]
    offer_names = {v["name"] for v in offers}
    for offer in new["offer"]["products_services"]:
        if offer["name"] in ("ServGo: внутреннее расписание и ручная запись",
                             "ServGo CRM: собственная база клиентов") and offer["name"] not in offer_names:
            offers.append(offer)
            offer_names.add(offer["name"])
            added["offers"] += 1

    facts = merged["proof"]["approved_facts"]
    present = {f["statement"] for f in facts}
    verified_paths = ("SchedulePage.tsx", "web/api/routes/crm.py", "mail_campaign_")
    for fact in new["proof"]["approved_facts"]:
        evidence = str(fact.get("evidence_ref") or "")
        if any(part in evidence for part in verified_paths) and fact["statement"] not in present:
            facts.append(fact)
            present.add(fact["statement"])
            added["facts"] += 1

    for key in ("primary_segments", "pains", "jobs_to_be_done"):
        values = merged["customers"][key]
        for value in new["customers"][key]:
            if any(term in value.casefold() for term in (
                "ручн", "журнал", "crm", "баз", "повторн", "персональн"
            )) and value not in values:
                values.append(value)
                added["audience"] += 1

    original_positioning = str(merged["brand"].get("positioning") or "")
    if POSITIONING_ADDENDUM.strip() not in original_positioning:
        merged["brand"]["positioning"] = (
            original_positioning.rstrip() + POSITIONING_ADDENDUM
        ).strip()
        added["positioning"] = True

    # Do not replace the owner's one_liner, pricing, service segments, contact
    # address, brand voice, hypotheses, metrics or source history.
    return parse_company_profile(merged).model_dump(mode="json"), added


def upgrade(target: Path, packaged: Path, *, apply: bool) -> dict:
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
    args = parser.parse_args()
    from app.core.config import get_settings
    target = get_settings().products_root / "servgo/company-profile.json"
    reference = Path("/app/products/servgo/company-profile.json")
    print("SERVGO_PROFILE_UPGRADE=" + json.dumps(
        upgrade(target, reference, apply=args.apply), ensure_ascii=False
    ))


if __name__ == "__main__":
    main()
