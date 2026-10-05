"""Idempotent local ServGo bootstrap. No invented metrics or external calls."""
import hashlib
import json
import os
import tempfile
from pathlib import Path

from sqlalchemy import select

from app.company_profile import (
    CompanyProfileNotReady,
    parse_company_profile,
    sanitized_profile,
)
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import (
    Artifact,
    ArtifactStatus,
    Experiment,
    Goal,
    Product,
    Task,
    TaskStatus,
)
from app.services.outreach import OutreachPackageService
from app.services.prospects import ProspectService


SERVGO_GROWTH_PROFILE_MIGRATION = "servgo-growth-positioning-2026-10-05-v1"
SERVGO_GROWTH_PLAN_MARKER = "[servgo_growth_positioning_20261005_v1]"

_SUPERSEDED_UNKNOWNS = {
    "Approved primary customer segments",
    "Service geography and supported languages",
    "Primary conversion action",
    "Brand tone",
    "Primary conversion event, funnel stages and attribution rules",
}

_GROWTH_PATCH_FIELDS = {
    "identity": ("category", "languages", "service_geography"),
    "offer": ("primary_conversion_action",),
    "customers": ("primary_segments", "pains", "jobs_to_be_done", "ideal_customer"),
    "commercial": ("pricing_model", "currency"),
    "brand": (
        "tone",
        "required_phrases",
        "prohibited_phrases",
        "voice",
        "positioning",
        "prohibited_claims",
    ),
    "measurement": ("funnel_stages", "primary_conversion_event", "attribution_rules", "metrics"),
}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".servgo-growth-")
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _merge_unique_records(existing: list[dict], desired: list[dict], key: str) -> list[dict]:
    result = [dict(item) for item in existing]
    positions = {
        str(item.get(key, "")).strip().casefold(): index
        for index, item in enumerate(result)
        if str(item.get(key, "")).strip()
    }
    for item in desired:
        identity = str(item.get(key, "")).strip().casefold()
        if not identity:
            continue
        if identity in positions:
            result[positions[identity]] = dict(item)
        else:
            positions[identity] = len(result)
            result.append(dict(item))
    return result


def apply_servgo_growth_profile_migration(
    db,
    product: Product,
    *,
    runtime_root: Path,
    packaged_root: Path,
) -> bool:
    """Apply the owner's ServGo Growth positioning once, preserving prior truth.

    The runtime Company Profile deliberately survives deploys. This migration
    updates only the positioning-owned fields requested on 2026-10-05, keeps
    verified capability facts and unrelated owner fields, and records the full
    previous profile before the atomic write.
    """
    marker = db.scalar(
        select(Artifact).where(
            Artifact.product_id == product.id,
            Artifact.kind == "company_profile_migration",
            Artifact.title == SERVGO_GROWTH_PROFILE_MIGRATION,
        ).limit(1)
    )
    if marker is not None:
        return False

    runtime_path = Path(runtime_root) / "servgo" / "company-profile.json"
    desired_path = Path(packaged_root) / "servgo" / "company-profile.json"
    if not desired_path.is_file():
        raise RuntimeError("ServGo Growth packaged profile is missing")

    desired_raw = desired_path.read_bytes()
    desired = parse_company_profile(json.loads(desired_raw))
    desired_data = sanitized_profile(desired)

    if runtime_path.is_file():
        previous_raw = runtime_path.read_bytes()
        previous = parse_company_profile(json.loads(previous_raw))
        previous_data = sanitized_profile(previous)
    else:
        previous_raw = b""
        previous_data = {"schema_version": "1.0"}

    updated = json.loads(json.dumps(previous_data, ensure_ascii=False))
    updated["schema_version"] = "1.0"

    for section, fields in _GROWTH_PATCH_FIELDS.items():
        target = updated.setdefault(section, {})
        source = desired_data.get(section, {})
        for field in fields:
            if field in source:
                target[field] = source[field]

    # Legacy price points are no longer approved facts for new marketing.
    updated.setdefault("proof", {})["approved_numbers"] = []

    updated["hypotheses"] = _merge_unique_records(
        updated.get("hypotheses", []),
        desired_data.get("hypotheses", []),
        "statement",
    )

    retained_unknowns = [
        item for item in updated.get("unknowns", [])
        if item.get("question") not in _SUPERSEDED_UNKNOWNS
    ]
    updated["unknowns"] = _merge_unique_records(
        retained_unknowns,
        desired_data.get("unknowns", []),
        "question",
    )

    validated = parse_company_profile(updated)
    safe = sanitized_profile(validated)
    encoded = (json.dumps(safe, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")

    previous_sha = _digest(previous_raw)
    new_sha = _digest(encoded)
    changed = previous_raw != encoded

    if changed:
        db.add(Artifact(
            product_id=product.id,
            kind="company_profile_previous",
            title="ServGo profile before ServGo Growth positioning migration",
            content=json.dumps({
                "migration": SERVGO_GROWTH_PROFILE_MIGRATION,
                "sha256": previous_sha,
                "profile": previous_data if previous_raw else None,
            }, ensure_ascii=False, sort_keys=True),
            status=ArtifactStatus.APPROVED,
        ))
        db.add(Artifact(
            product_id=product.id,
            kind="company_profile_apply_intent",
            title="Owner-approved ServGo Growth positioning migration",
            content=json.dumps({
                "migration": SERVGO_GROWTH_PROFILE_MIGRATION,
                "previous_sha256": previous_sha,
                "new_sha256": new_sha,
                "profile": safe,
            }, ensure_ascii=False, sort_keys=True),
            status=ArtifactStatus.APPROVED,
        ))
        # Durable history exists before the canonical file is touched.
        db.commit()
        _atomic_write(runtime_path, encoded)

    migration = Artifact(
        product_id=product.id,
        kind="company_profile_migration",
        title=SERVGO_GROWTH_PROFILE_MIGRATION,
        content=json.dumps({
            "migration": SERVGO_GROWTH_PROFILE_MIGRATION,
            "changed": changed,
            "previous_sha256": previous_sha,
            "new_sha256": new_sha,
            "owner_instruction_date": "2026-10-05",
        }, ensure_ascii=False, sort_keys=True),
        status=ArtifactStatus.APPROVED,
    )
    db.add(migration)

    existing_plan = db.scalar(
        select(Task).where(
            Task.product_id == product.id,
            Task.assigned_agent == "editorial_planner",
            Task.description.contains(SERVGO_GROWTH_PLAN_MARKER),
        ).limit(1)
    )
    if existing_plan is None:
        db.add(Task(
            product_id=product.id,
            assigned_agent="editorial_planner",
            title="Редактор: ServGo Growth — свежий план после смены позиционирования",
            description=(
                SERVGO_GROWTH_PLAN_MARKER
                + " Новый план после owner-approved позиционирования; "
                  "перед SEO/контент-планом обязательно выполнить viral_video_researcher."
            ),
            priority=125,
            status=TaskStatus.QUEUED,
        ))
    db.commit()
    return changed


def _apply_servgo_enrichment(db, product: Product) -> int:
    settings = get_settings()
    path = (
        settings.products_root
        / "servgo"
        / "experiments"
        / "EXP-SERVGO-001"
        / "enrichment.json"
    )
    if not path.is_file():
        return 0
    experiment = db.scalar(
        select(Experiment).where(
            Experiment.product_id == product.id,
            Experiment.experiment_key == "EXP-SERVGO-001",
        )
    )
    if experiment is None:
        return 0
    # Enrichment is only meaningful after the launch has materialized prospects.
    if not ProspectService(db).list_for_experiment(experiment.id):
        return 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("prospects") or []
    updated = ProspectService(db).apply_enrichment(
        experiment_id=experiment.id,
        rows=rows,
    )
    db.commit()
    return len(updated)


def bootstrap(db):
    settings = get_settings()
    product = db.scalar(select(Product).where(Product.slug == "servgo"))
    if product is None:
        product = Product(
            name="ServGo",
            slug="servgo",
            description="Online service booking/reservation system for small service businesses.",
        )
        db.add(product)
        db.flush()
    if db.scalar(
        select(Goal).where(
            Goal.product_id == product.id,
            Goal.name == "Validate organic customer acquisition",
        )
    ) is None:
        db.add(
            Goal(
                product_id=product.id,
                name="Validate organic customer acquisition",
                description=(
                    "Test evidence-backed SEO, articles and video briefs; measure traffic "
                    "to leads to demo/registration/booking to customers. No paid ads."
                ),
                target_metric="qualified_leads",
            )
        )
    db.commit()

    if settings.app_env == "production":
        apply_servgo_growth_profile_migration(
            db,
            product,
            runtime_root=settings.products_root,
            packaged_root=Path(__file__).resolve().parents[1] / "products",
        )

    _apply_servgo_enrichment(db, product)

    experiment = db.scalar(
        select(Experiment).where(
            Experiment.product_id == product.id,
            Experiment.experiment_key == "EXP-SERVGO-001",
            Experiment.status == "running",
        )
    )
    if experiment is not None:
        try:
            OutreachPackageService(db).prepare(experiment_id=experiment.id)
        except CompanyProfileNotReady:
            # An incomplete profile blocks preparation, not startup or the experiment.
            pass
        except ValueError as exc:
            if "No validated prospects with direct channels" not in str(exc):
                raise

    return product.id


if __name__ == "__main__":
    with SessionLocal() as db:
        print(bootstrap(db))
