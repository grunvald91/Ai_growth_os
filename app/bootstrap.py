"""Idempotent local ServGo bootstrap. No invented metrics or external calls."""
import json

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import Experiment, Goal, Product
from app.services.outreach import OutreachPackageService
from app.company_profile import CompanyProfileNotReady
from app.services.prospects import ProspectService


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
