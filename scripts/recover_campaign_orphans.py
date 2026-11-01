"""Operator-only research recovery. Never schedule or run automatically.

Usage: python -m scripts.recover_campaign_orphans --product-id UUID
    --experiment-id UUID --actor operator-name [--apply
    --confirm-worker-shutdown UUID]
"""
from __future__ import annotations

import argparse
from uuid import UUID

from app.db.session import SessionLocal
from app.services.campaign_lifecycle import recover_orphaned_research


def main() -> None:
    parser = argparse.ArgumentParser(description="Campaign orphan research recovery (dry-run by default)")
    parser.add_argument("--product-id", type=UUID, required=True)
    parser.add_argument("--experiment-id", type=UUID, required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-worker-shutdown", default="")
    args = parser.parse_args()
    if args.apply and args.confirm_worker_shutdown != str(args.experiment_id):
        parser.error("Workers must be stopped externally; confirm by entering the experiment UUID")
    if not args.actor.strip():
        parser.error("--actor cannot be blank")
    with SessionLocal() as db:
        tasks, runs = recover_orphaned_research(
            db, product_id=args.product_id, experiment_id=args.experiment_id,
            actor=args.actor, apply=args.apply, confirmed_workers_stopped=args.apply,
        )
    print(f"{'APPLIED' if args.apply else 'DRY RUN'}: {tasks} internal research tasks, {runs} agent runs")
    print("External deliveries and unrelated tasks were not modified.")


if __name__ == "__main__":
    main()
