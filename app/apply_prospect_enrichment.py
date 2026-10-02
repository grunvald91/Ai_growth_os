import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.domain import Experiment
from app.services.outreach import OutreachPackageService
from app.services.prospects import ProspectService


def apply_file(
    *,
    experiment_key: str,
    path: Path,
    prepare_outreach: bool = False,
) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    file_key = payload.get("experiment_key")
    if file_key != experiment_key:
        raise ValueError(
            f"Experiment key mismatch: expected {experiment_key}, file has {file_key}"
        )
    rows = payload.get("prospects")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Enrichment file must contain non-empty prospects list")

    lead_ids = [str(row.get("lead_id") or "").strip() for row in rows]
    if any(not lead_id for lead_id in lead_ids):
        raise ValueError("Every enrichment row requires lead_id")
    if len(lead_ids) != len(set(lead_ids)):
        raise ValueError("Enrichment file contains duplicate lead_id values")

    with SessionLocal() as db:
        experiment = db.scalar(
            select(Experiment).where(Experiment.experiment_key == experiment_key)
        )
        if experiment is None:
            raise LookupError(f"Experiment not found: {experiment_key}")

        service = ProspectService(db)
        service.apply_enrichment(
            experiment_id=experiment.id,
            rows=rows,
        )
        db.commit()
        stats = service.stats_for_experiment(experiment.id)

        package = None
        if prepare_outreach and stats["ready_for_outreach"] > 0:
            result = OutreachPackageService(db).prepare(
                experiment_id=experiment.id,
                limit=100,
            )
            package = {
                "artifact_id": str(result.artifact.id),
                "approval_id": str(result.approval.id),
                "message_count": result.message_count,
            }

        return {
            "experiment_key": experiment_key,
            "rows_applied": len(rows),
            "stats": stats,
            "outreach_package": package,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument(
        "--prepare-outreach",
        action="store_true",
        help="Prepare a review-only outreach package; never sends messages.",
    )
    args = parser.parse_args()
    result = apply_file(
        experiment_key=args.experiment,
        path=args.file,
        prepare_outreach=args.prepare_outreach,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
