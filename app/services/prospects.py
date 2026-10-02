from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Prospect


class ProspectService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def import_cohort(
        self,
        *,
        product_id: UUID,
        experiment_id: UUID,
        leads: list[dict],
    ) -> list[Prospect]:
        imported: list[Prospect] = []
        for row in leads[:100]:
            lead_key = str(row.get("lead_id") or "").strip()
            business_name = str(row.get("business_name") or "").strip()
            if not lead_key or not business_name:
                continue
            prospect = self.db.scalar(
                select(Prospect).where(
                    Prospect.experiment_id == experiment_id,
                    Prospect.lead_key == lead_key,
                )
            )
            checked_at = self._date(row.get("source_checked_at"))
            if prospect is None:
                prospect = Prospect(
                    product_id=product_id,
                    experiment_id=experiment_id,
                    lead_key=lead_key,
                    business_name=business_name,
                    area=self._text(row.get("area")),
                    category=self._text(row.get("category")),
                    public_phone=self._text(row.get("public_phone")),
                    address=self._text(row.get("address")),
                    source_place_id=self._text(row.get("source_place_id")),
                    source_checked_at=checked_at,
                    fit_status=self._text(row.get("fit_status")) or "needs_validation",
                    validation_status="pending",
                    outreach_status=self._text(row.get("outreach_status")) or "not_contacted",
                    tracking_url=self._text(row.get("tracking_url")),
                    evidence=[],
                )
                self.db.add(prospect)
                self.db.flush()
            else:
                # Cohort import may refresh public directory facts, but must never
                # overwrite enriched channels or contact history.
                prospect.business_name = business_name
                prospect.area = self._text(row.get("area"))
                prospect.category = self._text(row.get("category"))
                prospect.public_phone = self._text(row.get("public_phone"))
                prospect.address = self._text(row.get("address"))
                prospect.source_place_id = self._text(row.get("source_place_id"))
                prospect.source_checked_at = checked_at
                prospect.fit_status = self._text(row.get("fit_status")) or prospect.fit_status
                prospect.tracking_url = self._text(row.get("tracking_url")) or prospect.tracking_url
                self.db.flush()
            imported.append(prospect)
        return imported

    def list_for_experiment(self, experiment_id: UUID) -> list[Prospect]:
        return list(
            self.db.scalars(
                select(Prospect)
                .where(Prospect.experiment_id == experiment_id)
                .order_by(Prospect.lead_key.asc(), Prospect.id)
            )
        )

    @staticmethod
    def _text(value) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _date(value) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return None
